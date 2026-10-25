"""Лежащий по форме рамки и по позе на наборе Simuletic CCTV Fall/Lying Down (HF, CC BY 4.0).

111 синтетических кадров 1024x1024 с рамкой и 17 точками;
класс 0 - лежит, 1 - стоит (10 кадров).
Нужны только метки: py -m eval.fall_pose [--data data/p70]
(скачивает labels в ml/data/p70, вне git).
Форма: w/h рамки >= порога (lying_ratio правила, 0.7; кадр квадратный - доли равны физическим).
Поза: lying_by_pose (голова на уровне бёдер), наклон туловища < 30 градусов.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from worker.rules.fall import lying_by_pose

ML = Path(__file__).resolve().parents[1]
HF = "Simuletic/CCTV_Incident_Dataset_Fall_Lying_Down_Detection"


def parse_label(line: str):
    """Строка YOLO pose -> (класс, [x, y, w, h], 17 x (x, y, видимость 0/1/2))."""
    v = line.split()
    kp = tuple((float(v[5 + 3 * i]), float(v[6 + 3 * i]), float(v[7 + 3 * i])) for i in range(17))
    return int(v[0]), [float(x) for x in v[1:5]], kp


def by_shape(box, ratio: float = 0.7) -> bool:
    return box[2] / box[3] >= ratio


def by_pose(kp, slope: float = 0.5):
    pts = tuple((x, y, 1.0 if vis > 0 else 0.0) for x, y, vis in kp)
    return lying_by_pose(pts, slope)


def evaluate(rows) -> dict:
    out = {}
    for name, fn in (("рамка w/h >= 0.7", by_shape), ("поза (30 град.)", by_pose)):
        lay = [r for r in rows if r[0] == 0]
        std = [r for r in rows if r[0] == 1]

        def test(r, fn=fn):
            return bool(fn(r[1]) if fn is by_shape else fn(r[2]))

        hit, fp = sum(test(r) for r in lay), sum(test(r) for r in std)
        out[name] = (hit, len(lay), fp, len(std))
    return out


def download(dest: Path) -> None:
    api = f"https://huggingface.co/api/datasets/{HF}/tree/main/laying_dataset/labels"
    for f in json.load(urllib.request.urlopen(api)):
        target = dest / "labels" / Path(f["path"]).name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"https://huggingface.co/datasets/{HF}/resolve/main/{f['path']}"
            target.write_bytes(urllib.request.urlopen(url).read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ML / "data" / "p70"))
    dest = Path(ap.parse_args().data)
    download(dest)
    rows = [
        parse_label(line)
        for f in sorted((dest / "labels").glob("*.txt"))
        for line in f.read_text().splitlines()
        if line.strip()
    ]
    print("| способ | лежащих найдено | стоящих ложно |\n|---|---|---|")
    for name, (hit, n, fp, m) in evaluate(rows).items():
        print(f"| {name} | {hit} из {n} | {fp} из {m} |")


if __name__ == "__main__":
    main()
