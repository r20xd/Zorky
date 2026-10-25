"""Свой ByteTrack (по мотивам ultralytics): фильтр Калмана xyah + двухэтапное сопоставление по IoU.

Отличие от ultralytics: сопоставление жадное (по возрастанию цены), а не венгерское.
"""

from __future__ import annotations

import numpy as np

from .detect import iou_matrix


def _xyah(box: np.ndarray) -> np.ndarray:
    w, h = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] + w / 2, box[1] + h / 2, w / max(h, 1e-9), h])


class Kalman:
    """Постоянная скорость, состояние (x, y, a, h, vx, vy, va, vh)."""

    WP, WV = 1 / 20, 1 / 160

    def __init__(self):
        self.motion = np.eye(8)
        self.motion[:4, 4:] = np.eye(4)
        self.observe = np.eye(4, 8)

    def initiate(self, z):
        h = z[3]
        mean = np.r_[z, np.zeros(4)]
        std = [2 * self.WP * h] * 2 + [1e-2, 2 * self.WP * h]
        std += [10 * self.WV * h] * 2 + [1e-5, 10 * self.WV * h]
        return mean, np.diag(np.square(std))

    def predict(self, mean, cov):
        h = mean[3]
        std = [self.WP * h] * 2 + [1e-2, self.WP * h] + [self.WV * h] * 2 + [1e-5, self.WV * h]
        mean = self.motion @ mean
        return mean, self.motion @ cov @ self.motion.T + np.diag(np.square(std))

    def update(self, mean, cov, z):
        h = mean[3]
        r = np.diag(np.square([self.WP * h, self.WP * h, 1e-1, self.WP * h]))
        s = self.observe @ cov @ self.observe.T + r
        gain = np.linalg.solve(s, self.observe @ cov).T
        mean = mean + gain @ (z - self.observe @ mean)
        return mean, cov - gain @ s @ gain.T


class Track:
    def __init__(self, kalman: Kalman, box, score, track_id: int, activated: bool, frame: int):
        self.mean, self.cov = kalman.initiate(_xyah(box))
        self.score = score
        self.id = track_id
        self.activated = activated
        self.tracked = True  # False — потерян
        self.last_frame = frame

    @property
    def box(self) -> np.ndarray:
        x, y, a, h = self.mean[:4]
        return np.array([x - a * h / 2, y - h / 2, x + a * h / 2, y + h / 2])


def _match(tracks: list[Track], boxes: np.ndarray, scores, thresh: float, fuse: bool):
    """Жадное сопоставление: [(трек, детекция)], оставшиеся треки, оставшиеся детекции."""
    if not tracks or not len(boxes):
        return [], list(range(len(tracks))), list(range(len(boxes)))
    cost = 1 - iou_matrix(np.array([t.box for t in tracks]), boxes)
    if fuse:
        cost = 1 - (1 - cost) * np.asarray(scores)[None, :]
    pairs, used_t, used_d = [], set(), set()
    for flat in np.argsort(cost, axis=None, kind="stable"):
        i, j = divmod(int(flat), cost.shape[1])
        if cost[i, j] > thresh:
            break
        if i in used_t or j in used_d:
            continue
        pairs.append((i, j))
        used_t.add(i)
        used_d.add(j)
    return (
        pairs,
        [i for i in range(len(tracks)) if i not in used_t],
        [j for j in range(len(boxes)) if j not in used_d],
    )


class ByteTracker:
    def __init__(
        self,
        track_high_thresh=0.4,
        track_low_thresh=0.1,
        new_track_thresh=0.5,
        track_buffer=90,
        match_thresh=0.9,
        fuse_score=True,
    ):
        self.high, self.low, self.new = track_high_thresh, track_low_thresh, new_track_thresh
        self.buffer, self.match_thresh, self.fuse = track_buffer, match_thresh, fuse_score
        self.kalman = Kalman()
        self.tracks: list[Track] = []
        self.frame = 0
        self.next_id = 1

    def _update(self, track: Track, box, score):
        track.mean, track.cov = self.kalman.update(track.mean, track.cov, _xyah(box))
        track.score, track.tracked, track.activated = score, True, True
        track.last_frame = self.frame

    def update(self, boxes: np.ndarray, scores: np.ndarray) -> list[tuple[np.ndarray, float, int]]:
        """Детекции кадра (xyxy, оценки) -> [(xyxy, оценка, id)] подтверждённых треков."""
        self.frame += 1
        boxes = np.asarray(boxes, dtype=float).reshape(-1, 4)
        scores = np.asarray(scores, dtype=float)
        high = scores >= self.high
        low = (scores > self.low) & ~high
        hb, hs, lb, ls = boxes[high], scores[high], boxes[low], scores[low]

        for track in self.tracks:
            track.mean, track.cov = self.kalman.predict(track.mean, track.cov)
        confirmed = [t for t in self.tracks if t.activated]
        unconfirmed = [t for t in self.tracks if not t.activated]

        pairs, rest_t, rest_d = _match(confirmed, hb, hs, self.match_thresh, self.fuse)
        for i, j in pairs:
            self._update(confirmed[i], hb[j], hs[j])
        left = [confirmed[i] for i in rest_t]

        # вторая ассоциация: слабые детекции к ещё не найденным отслеживаемым трекам
        second = [t for t in left if t.tracked]
        pairs, rest2, _ = _match(second, lb, ls, 0.5, False)
        for i, j in pairs:
            self._update(second[i], lb[j], ls[j])
        for i in rest2:
            second[i].tracked = False

        # неподтверждённые — к оставшимся сильным детекциям
        pairs, rest_u, rest_d2 = _match(unconfirmed, hb[rest_d], hs[rest_d], 0.7, self.fuse)
        for i, j in pairs:
            self._update(unconfirmed[i], hb[rest_d][j], hs[rest_d][j])
        removed = {id(unconfirmed[i]) for i in rest_u}

        for j in rest_d2:
            k = rest_d[j]
            if hs[k] >= self.new:
                self.tracks.append(
                    Track(self.kalman, hb[k], hs[k], self.next_id, self.frame == 1, self.frame)
                )
                self.next_id += 1

        self.tracks = [
            t
            for t in self.tracks
            if id(t) not in removed and self.frame - t.last_frame <= self.buffer
        ]
        return [(t.box, t.score, t.id) for t in self.tracks if t.activated and t.tracked]
