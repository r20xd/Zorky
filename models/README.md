# Веса модели

`best.onnx` — детектор средств защиты (YOLO11s, вход 640×640): каска, голова без каски, защита лица, сигарета.

SHA256: `477b5c4d6aac4a542e4be5328fde095202bc5d383aad9629433862550ab9eb4d`

Воркер находит файл сам: сначала `MOSPROM_PPE_WEIGHTS`, затем `models/best.onnx` в репозитории, затем `~/.mosprom/models/best.onnx`.
