"""Новые веса -> программа .tpu на стенде -> проверка качества и скорости -> результаты.

    py tasks.py linq-build start  <веса.onnx> [--name ppe64] [--frames 48]
    py tasks.py linq-build status [--name ppe64]
    py tasks.py linq-build finish [--name ppe64]

`start`: кадры для калибровки и проверки из роликов (ml/.venv), загрузка весов и кода на стенд,
сборка отдельным процессом (setsid: не умирает вместе с терминалом Jupyter). Калибровка
идёт долго (на стенде ~2,5 мин на кадр), поэтому команда не ждёт: смотрите `status`.
`finish`: копирует программу в /auto/cache, прогоняет `linq/stand_accuracy.py` против ONNX FP32,
скачивает отчёт и программу, обновляет docs/measured.json и перерисовывает графики README.
Нужен поднятый туннель до стенда (`py tasks.py linq-up`).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .config import ROOT, Settings, load
from .jupyter import Jupyter, connect

SHELL = (
    "$HOME/work/mosprom"  # корень наших файлов на стенде (в терминале; ~ после = не раскрывается)
)
REMOTE = "mosprom"  # он же от корня Jupyter
RESULTS = ROOT / "ml" / "export" / "linq_results"
PROGRAMS = ROOT / "ml" / "linq_programs"
EVAL_FRAMES = 44
SUFFIX = "_b1_o5_h1m"
CODE = (
    ("ml/export/linq_compile.py", "export"),
    ("ml/export/quantize.py", "export"),
    ("ml/export/__init__.py", "export"),
    ("linq/stand_accuracy.py", "acc"),
    ("linq/stand_bench.py", "acc"),
    ("linq/stand_streams.py", "acc"),
    ("ml/worker/detect.py", "acc"),
)


def safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,23}", name):
        raise SystemExit("имя программы: латиница, цифры и _, до 24 знаков, с буквы")
    return name


def build_dir(name: str) -> str:
    return f"{SHELL}/build/{name}"


def compile_command(name: str, model: str, frames: int) -> str:
    """Сборка отдельным процессом: setsid отрывает его от терминала, код выхода - в файл EXIT."""
    work = build_dir(name)
    inner = (
        f"cd {SHELL} && venv/bin/python -m export.linq_compile {name}={work}/{model} "
        f"--calib {work}/calib --count {frames} --min-frames {frames} --out programs/{name}; "
        f"echo $? > {work}/EXIT"
    )
    return (
        f"mkdir -p {work} programs/{name} && rm -f {work}/EXIT && "
        f"setsid nohup sh -c '{inner}' > {work}/compile.log 2>&1 < /dev/null & "
        f"sleep 1; echo LAUNCHED"
    )


def accuracy_command(name: str, login: str) -> str:
    """Подготовка кадров (venv, onnxruntime) и прогон на ускорителе (системный python, pika)."""
    work = build_dir(name)
    inner = (
        f"cd {SHELL}/acc && ../venv/bin/python stand_accuracy.py prep {work}/eval "
        f"{work}/model.onnx {work}/in.npz && "
        f"python3 stand_accuracy.py run {login} {name}{SUFFIX}.tpu {work}/in.npz"
        f" {work}/accuracy.json --conf 0.3; echo $? > {work}/ACC_EXIT"
    )
    return (
        f"rm -f {work}/ACC_EXIT && setsid nohup sh -c '{inner}' > {work}/accuracy.log 2>&1 "
        f"< /dev/null & sleep 1; echo LAUNCHED"
    )


def best_conf(sweep: dict) -> tuple[float, dict]:
    """Порог LinQ с лучшим F1 из отчёта (`agreement_by_tpu_conf`): (порог, его метрики)."""
    scored = [(float(k), v) for k, v in sweep.items() if v.get("f1") is not None]
    if not scored:
        raise SystemExit("в отчёте нет ни одного порога с F1: на кадрах нет детекций")
    return max(scored, key=lambda item: item[1]["f1"])


def parse_status(text: str) -> dict:
    """Разбор вывода `status` со стенда: идёт ли сборка, чем закончилась, на каком этапе."""
    exit_code = re.search(r"EXIT=(-?\d+)", text)
    stages = re.findall(r"\] ([^\n:]+): (\d+) с от старта", text)
    return {
        "running": "RUNNING=1" in text,
        "exit": int(exit_code.group(1)) if exit_code else None,
        "stage": f"{stages[-1][0]} ({stages[-1][1]} с)"
        if stages
        else "ещё не дошла до первого этапа",
        "program": "PROGRAM=1" in text,
    }


def say(text: str) -> None:
    print(text, flush=True)


def ml_python() -> str:
    for rel in ("ml/.venv/Scripts/python.exe", "ml/.venv/bin/python"):
        if (ROOT / rel).is_file():
            return str(ROOT / rel)
    return sys.executable


def make_frames(name: str, frames: int, videos: Path) -> Path:
    local = ROOT / "ml" / f"linq_calib_{name}"
    for kind, count, offset in (("calib", frames, "0.25"), ("eval", EVAL_FRAMES, "0.5")):
        subprocess.run(
            [ml_python(), str(ROOT / "linq" / "frames.py"), str(videos), str(local / kind)]
            + ["--count", str(count), "--offset", offset],
            check=True,
        )
    return local


def upload(jupyter: Jupyter, name: str, local: Path, weights: Path) -> None:
    remote = f"{REMOTE}/build/{name}"
    for kind in ("calib", "eval"):
        for path in sorted((local / kind).glob("*.jpg")):
            jupyter.upload_file(path, f"{remote}/{kind}")
    for rel, folder in CODE:
        jupyter.upload_file(ROOT / rel, f"{REMOTE}/{folder}")
    say(
        f"загружаю веса {weights.name} ({weights.stat().st_size / 1e6:.0f} МБ, докачка при обрыве)…"
    )
    subprocess.run(
        [sys.executable, "-m", "linq.upload_big", str(weights), "--to", f"build/{name}"],
        cwd=ROOT,
        check=True,
    )
    # upload_big кладёт файл в ~/work/<to>; нам нужен ~/work/mosprom/build/<name>/model.onnx
    jupyter.run(
        f"mv -f $HOME/work/build/{name}/{weights.name} {build_dir(name)}/model.onnx", timeout=120
    )


def start(s: Settings, weights: Path, name: str, frames: int, videos: Path) -> None:
    if weights.suffix.lower() != ".onnx" or not weights.is_file():
        raise SystemExit("нужен файл весов .onnx со статическим входом 1x3x640x640 (см. ml/export)")
    say(f"кадры калибровки ({frames}) и проверки ({EVAL_FRAMES}) из {videos}…")
    local = make_frames(name, frames, videos)
    jupyter = connect(s)
    upload(jupyter, name, local, weights)
    code, out = jupyter.run(compile_command(name, "model.onnx", frames), timeout=120)
    if "LAUNCHED" not in out:
        raise SystemExit(f"сборка не запустилась (код {code}): {out[-300:]}")
    say(f"сборка {name} запущена на стенде; калибровка ~{frames * 2.5:.0f} мин. Дальше: status")


def status_command(name: str) -> str:
    """Метки разорваны кавычками: терминал эхом печатает саму команду, и её текст не должен
    приниматься за ответ (RUN""NING в эхе, RUNNING в выводе)."""
    work = build_dir(name)
    return (
        f'pgrep -f "export.linq_compile {name}=" >/dev/null && echo RUN""NING=1; '
        f'[ -f {work}/EXIT ] && echo EX""IT=$(cat {work}/EXIT); '
        f'ls {SHELL}/programs/{name}/{name}{SUFFIX}.tpu >/dev/null 2>&1 && echo PRO""GRAM=1; '
        f"grep -a 'с от старта' {work}/compile.log | tail -3"
    )


def status(s: Settings, name: str) -> dict:
    command = status_command(name)
    _code, out = connect(s).run(command, timeout=60)
    info = parse_status(out)
    state = "идёт" if info["running"] else ("готова" if info["program"] else "не идёт")
    say(f"сборка {name}: {state}; этап: {info['stage']}; код выхода: {info['exit']}")
    return info


def finish(s: Settings, name: str) -> None:
    info = status(s, name)
    if not info["program"]:
        raise SystemExit("программы ещё нет: дождитесь конца сборки (status)")
    jupyter = connect(s)
    work, login = build_dir(name), s.jupyter_login
    cache = s.programs_dir
    code, out = jupyter.run(
        f"cp -f {SHELL}/programs/{name}/{name}{SUFFIX}.tpu* {cache}/ && ls {cache}", timeout=120
    )
    if code != 0:
        raise SystemExit(f"не скопировал в {cache}: {out[-200:]}")
    code, out = jupyter.run(accuracy_command(name, login), timeout=60)
    if "LAUNCHED" not in out:
        raise SystemExit(f"проверка не запустилась: {out[-300:]}")
    say("сравниваю с ONNX FP32 на ускорителе…")
    for _ in range(90):  # до ~15 минут
        time.sleep(10)
        _c, probe = jupyter.run(f"cat {work}/ACC_EXIT 2>/dev/null || echo WAIT", timeout=60)
        if "WAIT" not in probe:
            break
    else:
        raise SystemExit("проверка не закончилась за 15 минут; смотрите accuracy.log на стенде")
    report = json.loads(jupyter.download(f"{REMOTE}/build/{name}/accuracy.json"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}_accuracy.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    PROGRAMS.mkdir(parents=True, exist_ok=True)
    for suffix in (".tpu", ".tpu.json"):
        file = f"{name}{SUFFIX}{suffix}"
        (PROGRAMS / file).write_bytes(jupyter.download(f"{REMOTE}/programs/{name}/{file}"))
    conf, metrics = best_conf(report["agreement_by_tpu_conf"])
    update_measured(name, report)
    subprocess.run([sys.executable, str(ROOT / "docs" / "make_charts.py")], check=True)
    say(
        f"готово: {name}. Лучший порог LinQ {conf}: полнота {metrics['recall']}, "
        f"точность {metrics['precision']}, F1 {metrics['f1']}; задержка p50 "
        f"{report['latency_ms']['p50']} мс, {report['fps_by_inflight']} кадр/с. "
        f"Файлы: ml/linq_programs, ml/export/linq_results. Обновите числа в README."
    )


def update_measured(name: str, report: dict) -> None:
    path = ROOT / "docs" / "measured.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["linq_program"] = name
    frames = re.search(r"(\d+)$", name)
    if frames:
        data["calibration_frames"] = int(frames.group(1))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("start", "status", "finish"))
    parser.add_argument("weights", nargs="?", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--frames", type=int, default=48)
    parser.add_argument("--videos", type=Path, default=Path.home() / ".mosprom" / "demo-videos")
    args = parser.parse_args(argv)
    name = safe_name(args.name or f"ppe{args.frames}")
    s = load()
    if args.action == "start":
        if args.weights is None:
            raise SystemExit("укажите файл весов .onnx")
        start(s, args.weights, name, args.frames, args.videos)
    elif args.action == "status":
        status(s, name)
    else:
        finish(s, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
