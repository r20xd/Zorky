#!/usr/bin/env bash
# Сборка .tpu прямо на стенде (Linux x86_64, Python 3.12 через uv, 64 ядра): запускается из linq.compile_on_stand.
#   stand_compile.sh <шаг>   шаги: venv | export | calib | compile | all
# Работает в ~/work/mosprom: sdk/packets (колёса SDK), venv/, models/, calib/, programs/.
# Колёса SDK и наши скрипты export/ сюда загружает linq.upload_big.
set -euo pipefail
ROOT="$HOME/work/mosprom"
UV="$HOME/.local/bin/uv"
PY="$ROOT/venv/bin/python"
cd "$ROOT"

step_venv() {
  [ -x "$PY" ] || "$UV" venv --python 3.12 venv
  # колёса SDK из sdk/packets (там же requirements.txt); numpy 1.26.4 — с numpy 2 компилятор падает
  (cd sdk/packets && "$UV" pip install --python "$PY" -r requirements.txt)
  "$UV" pip install --python "$PY" "numpy==1.26.4" onnx pillow opencv-python-headless \
    ultralytics onnxruntime
  "$PY" -c "import tpu_framework, onnx, numpy; print('SDK_OK', numpy.__version__)"
}

step_export() {
  mkdir -p models
  cd models
  # веса YOLO11n (люди) и YOLO11n-pose (поза): статический вход 1x3x640x640, без NMS внутри
  "$PY" - <<'EOF'
from ultralytics import YOLO
for name, out in (("yolo11n.pt", "people"), ("yolo11n-pose.pt", "pose")):
    path = YOLO(name).export(format="onnx", imgsz=640, opset=12, dynamic=False, batch=1,
                             nms=False, simplify=True)
    import shutil
    shutil.copyfile(path, f"{out}.onnx")
    print("EXPORTED", out)
EOF
}

step_calib() {
  mkdir -p calib
  if [ ! -d calib/coco128 ]; then
    curl -sL -o calib/coco128.zip https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip
    (cd calib && unzip -q -o coco128.zip && ls | head)
  fi
  find calib -name '*.jpg' | wc -l
}

step_compile() {
  mkdir -p programs
  cd "$ROOT"
  # export/ — наши linq_compile.py и quantize.py (поиск модулей: из папки ROOT)
  "$PY" -m export.linq_compile people=models/people.onnx pose=models/pose.onnx \
    --calib calib/coco128/images/train2017 --out programs
}

case "${1:-all}" in
  venv) step_venv ;;
  export) step_export ;;
  calib) step_calib ;;
  compile) step_compile ;;
  all) step_venv; step_export; step_calib; step_compile ;;
  *) echo "неизвестный шаг $1" >&2; exit 2 ;;
esac
echo "STEP_DONE ${1:-all}"
