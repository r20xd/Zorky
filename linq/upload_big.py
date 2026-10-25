"""Докачиваемая загрузка больших файлов на стенд через Jupyter (канал медленный, ~0,15 МБ/с).

    python -m linq.upload_big <файл> [<файл> ...] [--to mosprom/sdk]

Файл режется на куски по 1 МиБ, каждый кусок уходит отдельным запросом с повтором; уже
загруженные куски пропускаются, поэтому после обрыва команду можно просто запустить заново.
В конце куски склеиваются на стенде и сверяется sha256. Прогресс печатается строками.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

from . import config
from .jupyter import Jupyter, JupyterError, connect

CHUNK = 1024 * 1024
SHELL_ROOT = "~/work"  # корень Jupyter в терминале стенда


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parts_of(path: Path, chunk: int = CHUNK):
    """(номер, байты) кусков файла по порядку."""
    with path.open("rb") as handle:
        index = 0
        while block := handle.read(chunk):
            yield index, block
            index += 1


def upload_parts(
    jupyter: Jupyter, path: Path, parts_dir: str, retries: int = 6, say=print, relogin=None
) -> int:
    """Загружает недостающие куски, возвращает их число; `relogin()` — при обрыве сессии."""
    jupyter.mkdir(parts_dir)
    have = set(jupyter.listdir(parts_dir))
    total = -(-path.stat().st_size // CHUNK)
    sent = 0
    started = time.monotonic()
    for index, block in parts_of(path):
        name = f"{path.name}.part{index:05d}"
        if name in have:
            continue
        for attempt in range(1, retries + 1):
            try:
                jupyter.upload(f"{parts_dir}/{name}", block)
                break
            except (JupyterError, OSError) as exc:
                if attempt == retries:
                    raise
                say(f"  кусок {index}: {exc}; повтор {attempt}")
                if relogin:
                    jupyter = relogin()
                time.sleep(2 * attempt)
        sent += 1
        if sent % 10 == 0 or index == total - 1:
            speed = sent * CHUNK / 1e6 / max(time.monotonic() - started, 1e-9)
            say(f"{path.name}: {index + 1}/{total} кусков, {speed:.2f} МБ/с")
    return sent


def _exists(jupyter: Jupyter, directory: str) -> bool:
    try:
        jupyter.listdir(directory)
        return True
    except JupyterError:
        return False


def assemble(jupyter: Jupyter, path: Path, parts_dir: str, target_dir: str) -> str:
    """Склеивает куски на стенде и возвращает sha256 результата."""
    parts = f"{SHELL_ROOT}/{parts_dir}"
    target = f"{SHELL_ROOT}/{target_dir}"
    command = (
        f"mkdir -p {target} && cat $(ls {parts}/{path.name}.part* | sort) > {target}/{path.name} "
        f"&& sha256sum {target}/{path.name}"
    )
    code, out = jupyter.run(command, timeout=600)
    if code != 0:
        raise JupyterError(f"сборка {path.name} не удалась: {out[-200:]}")
    found = re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", out)
    return found[-1] if found else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--to", default="mosprom/sdk", help="папка на стенде (от корня Jupyter)")
    args = parser.parse_args(argv)
    settings = config.load()
    jupyter = connect(settings)

    def relogin() -> Jupyter:
        for attempt in range(1, 6):  # канал бывает забит: ждём и пробуем снова
            try:
                return connect(settings)
            except (JupyterError, OSError):
                if attempt == 5:
                    raise
                time.sleep(10 * attempt)
        raise AssertionError("недостижимо")

    for path in args.files:
        local = sha256(path)
        parts_dir = f"mosprom/parts/{path.name}"
        print(f"{path.name}: {path.stat().st_size / 1e6:.1f} МБ, sha256 {local[:12]}…", flush=True)
        upload_parts(jupyter, path, parts_dir, say=lambda t: print(t, flush=True), relogin=relogin)
        remote = assemble(jupyter, path, parts_dir, args.to)
        ok = remote == local
        print(f"{path.name}: на стенде {'совпадает' if ok else 'НЕ СОВПАДАЕТ'}", flush=True)
        if not ok:
            return 1
        jupyter.run(f"rm -rf {SHELL_ROOT}/{parts_dir}", timeout=120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
