from worker.rules import Detection
from worker.rules.fall import Fall, lying_by_pose

STAND_BOX = (0.4, 0.1, 0.6, 0.9)
TALL_BOX = (0.4, 0.1, 0.55, 0.9)  # лежит, но рамка вертикальная (высокая камера, ноги к камере)


def pose(head, hip):
    """17 точек COCO: нос/глаза/уши в head, бёдра в hip, остальное посередине."""
    mid = ((head[0] + hip[0]) / 2, (head[1] + hip[1]) / 2, 0.9)
    pts = [mid] * 17
    for i in range(5):
        pts[i] = (*head, 0.9)
    for i in (11, 12):
        pts[i] = (*hip, 0.9)
    return tuple(pts)


STANDING = pose((0.5, 0.15), (0.5, 0.55))
LYING_POSE = pose((0.4, 0.5), (0.52, 0.52))


def run(rule, steps):
    out = []
    for now, box, kp in steps:
        rule.on_frame(now)
        out.append(rule.check(Detection("person", box, 0.9, 1, keypoints=kp), [], []).active)
    return out


def test_lying_by_pose():
    assert lying_by_pose(STANDING) is False
    assert lying_by_pose(LYING_POSE) is True
    assert lying_by_pose(None) is None
    assert lying_by_pose(pose((0.5, 0.1), (0.5, 0.5))[:10]) is None


def test_lying_with_tall_box_from_high_camera_is_fall_by_pose():
    steps = [(0.0, STAND_BOX, STANDING), (0.2, STAND_BOX, STANDING), (0.4, TALL_BOX, LYING_POSE)]
    assert run(Fall(pose_lying=True), steps) == [False, False, True]
    assert run(Fall(), steps) == [False, False, False]  # по рамке такое не видно


def test_standing_person_with_pose_is_not_fall():
    steps = [(0.0, STAND_BOX, STANDING), (0.4, STAND_BOX, STANDING)]
    assert run(Fall(pose_lying=True), steps) == [False, False]


def test_without_keypoints_falls_back_to_box():
    lying = (0.1, 0.6, 0.8, 0.8)
    steps = [(0.0, STAND_BOX, None), (0.2, lying, None)]
    assert run(Fall(pose_lying=True), steps) == [False, True]


def test_eval_fall_pose_parse_and_count():
    from eval.fall_pose import evaluate, parse_label

    line = "0 0.5 0.5 0.6 0.2 " + " ".join("0.5 0.5 2" for _ in range(17))
    row = parse_label(line)
    assert row[0] == 0 and len(row[2]) == 17
    res = evaluate([row, (1, [0.5, 0.5, 0.2, 0.6], row[2])])
    assert res["рамка w/h >= 0.7"] == (1, 1, 0, 1)
