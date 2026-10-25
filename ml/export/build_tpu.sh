#!/usr/bin/env bash
# Сборка программ .tpu для LinQ одной командой (по compile.py и tpu_framework.pdf из ТЗ).
#
# Нужен Linux x86_64 с Python 3.12 (Ubuntu, WSL2 или Docker python:3.12): колёса SDK собраны
# только под него. Из корня репозитория:
#
#   ml/export/build_tpu.sh <папка packets из tpu_framework.zip> [веса_людей.pt] [веса_СИЗ.pt] [веса_позы.pt]
#
# Пример (SDK распакован в ~/tpu_framework): ml/export/build_tpu.sh ~/tpu_framework/packets
# Результат: ml/linq_programs/<имя>_b1_<o1|o5>_h1m.tpu и рядом .tpu.json (вход, выходы, sha256).
# Калибровка — кадры из demo/videos (настоящие кадры камер), при наличии — ещё демо-картинки.
set -euo pipefail

# пути из командной строки — от текущей папки; после cd в ml/ они уже не нашлись бы
abs() { [ -z "$1" ] && return 0; case "$1" in /*|~*) echo "$1" ;; *) echo "$PWD/$1" ;; esac; }
PACKETS="$(abs "${1:?нужна папка packets SDK (из tpu_framework.zip)}")"
PEOPLE="${2:-yolo11n.pt}"   # без пути — имя весов ultralytics, скачается само
case "$PEOPLE" in */*) PEOPLE="$(abs "$PEOPLE")" ;; esac
PPE="$(abs "${3:-}")"
POSE="$(abs "${4:-}")"

cd "$(dirname "$0")/.."   # папка ml/
python3 --version | grep -q "3.12" || { echo "СТОП: нужен Python 3.12 (сейчас $(python3 --version))" >&2; exit 2; }

if [ ! -d .venv-tpu ]; then
  python3 -m venv .venv-tpu
fi
. .venv-tpu/bin/activate
pip install -q -U pip
(cd "$PACKETS" && pip install -q -r requirements.txt)   # SDK: tpu_framework и зависимости
pip install -q -r requirements.txt                       # ultralytics, onnx, opencv для экспорта

ARGS=(--people "$PEOPLE")
[ -n "$PPE" ] && ARGS+=(--ppe "$PPE")
[ -n "$POSE" ] && ARGS+=(--pose "$POSE")
python -m export.linq export "${ARGS[@]}" --out linq_models

CALIB=()
[ -d ../demo/videos ] && python -m export.linq calib ../demo/videos --out linq_calib_videos --count 300 && CALIB+=(--calib linq_calib_videos)
[ -d ../demo/images ] && CALIB+=(--calib ../demo/images)
[ ${#CALIB[@]} -gt 0 ] || { echo "СТОП: нет кадров для калибровки (demo/videos или demo/images)" >&2; exit 2; }

MODELS=()
for name in ppe people pose; do
  [ -f "linq_models/$name.onnx" ] && MODELS+=("$name=linq_models/$name.onnx")
done
python -m export.linq_compile "${MODELS[@]}" "${CALIB[@]}" --out linq_programs
ls -la linq_programs
