# ruff: noqa: E501
"""Графики для README: SVG без зависимостей, цифры - из файлов, не из головы.

    py docs/make_charts.py            # пишет docs/img/*.svg

Источники:
  ml/export/linq_results/<программа>_accuracy.json   замеры на стенде LinQ (linq/stand_accuracy.py)
  docs/measured.json                                 замеры CPU ноутбука (живой воркер демо)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "img"
BG, PANEL, GRID = "#0d1012", "#151a1d", "#2c343a"
TEXT, MUTED = "#edf1f3", "#8b979e"
ACCENT, BLUE, ORANGE, RED, GREEN = "#b8e62e", "#5aa9ff", "#ff9f1c", "#ff5a5f", "#5be3a0"
FONT = 'font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif"'


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def frame(width: int, height: int, title: str, subtitle: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{esc(title)}" {FONT}>'
        f'<rect width="{width}" height="{height}" rx="14" fill="{BG}"/>'
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="13" fill="{PANEL}" '
        f'stroke="{GRID}"/>'
        f'<text x="24" y="38" fill="{TEXT}" font-size="20" font-weight="700">{esc(title)}</text>'
        f'<text x="24" y="60" fill="{MUTED}" font-size="13">{esc(subtitle)}</text>{body}</svg>'
    )


def bars(
    rows: list[tuple[str, float, str, str]], unit: str, title: str, subtitle: str, note: str
) -> str:
    """Горизонтальные столбики: (подпись, значение, цвет, подпись значения)."""
    width, left, top, row = 880, 330, 86, 46
    top_value = max(v for _, v, _, _ in rows)
    scale = (width - left - 150) / top_value
    body = []
    for i, (label, value, color, shown) in enumerate(rows):
        y = top + i * row
        w = max(4.0, value * scale)
        body.append(f'<text x="24" y="{y + 20}" fill="{TEXT}" font-size="14">{esc(label)}</text>')
        body.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="28" rx="6" fill="{color}"/>')
        body.append(
            f'<text x="{left + w + 10:.1f}" y="{y + 20}" fill="{TEXT}" font-size="15" '
            f'font-weight="700">{esc(shown)} <tspan fill="{MUTED}" font-weight="400">{esc(unit)}</tspan></text>'
        )
    height = top + len(rows) * row + 44
    body.append(f'<text x="24" y="{height - 18}" fill="{MUTED}" font-size="12">{esc(note)}</text>')
    return frame(width, height, title, subtitle, "".join(body))


def speed(acc: dict, cpu: dict) -> str:
    fps = acc["fps_by_inflight"]
    rows = [
        (
            f"CPU ноутбука, {cpu['cameras']} камеры сразу",
            cpu["total_fps"],
            MUTED,
            f"{cpu['total_fps']:.1f}",
        ),
        ("LinQ, один запрос за раз", fps["1"], BLUE, f"{fps['1']:.1f}"),
        ("LinQ, 2 запроса в полёте", fps["2"], BLUE, f"{fps['2']:.1f}"),
        ("LinQ, 4 запроса в полёте", fps["4"], ACCENT, f"{fps['4']:.1f}"),
    ]
    gain = fps["4"] / cpu["total_fps"]
    return bars(
        rows,
        "кадров/с",
        "Пропускная способность: одна и та же модель СИЗ",
        f"LinQ быстрее процессорного пути в {gain:.1f} раза (больше - лучше)",
        "Замер: ml/export/linq_results и docs/measured.json. Модель best.onnx, вход 640x640, батч 1.",
    )


def latency(acc: dict, cpu: dict) -> str:
    lat = acc["latency_ms"]
    rows = [
        ("CPU ноутбука (средняя)", cpu["inference_ms"], MUTED, f"{cpu['inference_ms']:.0f}"),
        ("LinQ, p95", lat["p95"], BLUE, f"{lat['p95']:.0f}"),
        ("LinQ, средняя", lat["mean"], BLUE, f"{lat['mean']:.0f}"),
        ("LinQ, медиана (p50)", lat["p50"], ACCENT, f"{lat['p50']:.0f}"),
    ]
    gain = cpu["inference_ms"] / lat["mean"]
    return bars(
        rows,
        "мс на кадр",
        "Задержка инференса одного кадра",
        f"LinQ отвечает в {gain:.0f} раз быстрее (меньше - лучше)",
        "LinQ: от отправки тензора брокеру стенда до ответа. CPU: время инференса в живом воркере.",
    )


def quality(acc: dict, label: str) -> str:
    sweep = acc["agreement_by_tpu_conf"]
    keys = sorted(sweep, key=float)
    width, height = 880, 410
    x0, x1, y0, y1 = 80, 840, 110, 310

    def px(i: int) -> float:
        return x0 + (x1 - x0) * i / max(1, len(keys) - 1)

    def py(v: float) -> float:
        return y1 - (y1 - y0) * v

    body = []
    for g in (0, 0.25, 0.5, 0.75, 1.0):
        body.append(
            f'<line x1="{x0}" y1="{py(g):.0f}" x2="{x1}" y2="{py(g):.0f}" stroke="{GRID}"/>'
        )
        body.append(
            f'<text x="{x0 - 10}" y="{py(g) + 4:.0f}" fill="{MUTED}" font-size="12" text-anchor="end">{int(g * 100)}%</text>'
        )
    for i, k in enumerate(keys):
        body.append(
            f'<text x="{px(i):.0f}" y="{y1 + 24}" fill="{MUTED}" font-size="12" text-anchor="middle">{k}</text>'
        )
    body.append(
        f'<text x="{(x0 + x1) / 2:.0f}" y="{y1 + 46}" fill="{MUTED}" font-size="12" text-anchor="middle">порог уверенности на LinQ</text>'
    )
    for _name, field, color in (
        ("Полнота", "recall", ACCENT),
        ("Точность", "precision", BLUE),
        ("F1", "f1", ORANGE),
    ):
        pts = [(px(i), py(sweep[k][field] or 0)) for i, k in enumerate(keys)]
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
        body.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"/>'
        )
        body.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>' for x, y in pts)
    lx = x0
    for name, color in (("полнота", ACCENT), ("точность", BLUE), ("F1", ORANGE)):
        body.append(f'<rect x="{lx}" y="78" width="12" height="12" rx="3" fill="{color}"/>')
        body.append(f'<text x="{lx + 18}" y="89" fill="{TEXT}" font-size="13">{name}</text>')
        lx += 110
    body.append(
        f'<text x="24" y="{height - 16}" fill="{MUTED}" font-size="12">'
        f"Эталон - тот же best.onnx в FP32; {acc['frames']} кадров из 6 роликов; совпадение: класс тот же, IoU не ниже 0,5.</text>"
    )
    return frame(
        width,
        height,
        f"Качество после квантования в INT8 ({label})",
        "Совпадение детекций LinQ с эталоном FP32",
        "".join(body),
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    measured = json.loads((ROOT / "docs" / "measured.json").read_text(encoding="utf-8"))
    results = ROOT / "ml" / "export" / "linq_results"
    chosen = measured.get("linq_program", "ppe16")
    acc = json.loads((results / f"{chosen}_accuracy.json").read_text(encoding="utf-8"))
    cpu = measured["cpu_laptop"]
    (OUT / "speed.svg").write_text(speed(acc, cpu), encoding="utf-8")
    (OUT / "latency.svg").write_text(latency(acc, cpu), encoding="utf-8")
    (OUT / "quality.svg").write_text(
        quality(acc, f"{chosen}, {measured.get('calibration_frames', '?')} кадров калибровки"),
        encoding="utf-8",
    )
    print("готово:", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
