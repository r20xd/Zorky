# export/ — LinQ (ML-2; в первые часы помогает ML-1)

Главный риск проекта: модель обязана работать на LinQ. Путь проверяем в первый же час на готовой YOLO, свою модель подключаем потом.

## L0 — разведка доступа (факты из SDK организаторов)
- Доступ: Python-пакет `pytpu` (пример — `usage_example_python_api/example_torch_resnet50.py` из API.zip; самого колеса pytpu в архивах нет, только пример). Device, program и inference — контекстные менеджеры (`with`). Схема: `pytpu.Device.list_devices()` -> `Device.open` -> `device.load(<.tpu>)` -> `program.inference()` -> `inference.run({имя_входа: float32 NCHW})` -> словарь выходов по именам узлов. Воркер: `worker.infer.LinqSession` (закрывает их через `close()`/`with`).
- Платформа: H1M. Компилятор (`tpu_framework`, SDK 2026.9.45, колёса `packets/*.whl`, `pip install -r packets/requirements.txt`) работает только на Linux x86_64 с Python 3.12.11 (колёса cp312; на Mac/Windows .tpu не собрать — WSL2 Ubuntu или Docker `python:3.12`).
- Компилятор: `RegularModel(model=onnx).calibrate_torch(calibration_data={вход: float32 N x C x H x W})` (один тензор, кадры по оси 0, настоящие кадры), `quantize(thresholds)`, `Network.from_quantized_model`, `compiler.compile_(hardware_parameters=TPU_H1M, network=..., parameters=O1|O5)`; динамические входы запрещены, инициализаторы убираются из inputs.
- Имя программы: `<имя>_b<батч>_<o1|o5>_h1m.tpu`.
- Формат модели: компилируется в программу `.tpu` из ONNX. Пресеты компиляции: O1 (надёжный, берём по умолчанию) и O5.
- Поддерживаемые модели: `yolo11n_obb`, `yolov8n_pose` — с отрезанной головой (декодирование и NMS делает воркер). Другие YOLO не гарантированы.
- Батч фиксируется в программе при компиляции (у нас 1).
- Имена входа и выходов воркер берёт из файла-описания `<модель>.tpu.json` рядом с .tpu (`<файл>.tpu.json`, пишет `linq_compile`; имена выходов — выходные узлы ONNX после `preliminary_model_conditioning`): `{"input": "...", "outputs": ["..."], "names": {"0": "person"}}`.
- Не выяснено из репо (в архиве SDK не было): можно ли запускать свой процесс на хосте и ходят ли с него запросы наружу.

## L1 — готовая YOLO на LinQ
1. Экспорт (из `ml/`): `python -m export.bench export yolo11n.pt [--opset N]` (opset — какой поддерживает LinQ).
2. Компиляция и загрузка в LinQ: _шаги из документации LinQ_.
3. Замер: `python -m export.bench run yolo11n.onnx` печатает готовую строку таблицы. Через `bench.py`: FPS и задержка p50/p95 на LinQ и на CPU. Результаты — в таблицу ниже, она пойдёт в презентацию.
4. Полный бенч: `python -m export.bench all yolo11n.onnx [--engines cpu,gpu,linq] [--out bench.json]` - FPS, p50/p95 инференса, пред- и постобработка, кадр->событие для 1, 2, 4 потоков; таблица markdown и JSON. Недоступный linq даёт строку «пропущен».

## Замеры
| Модель | Устройство | Разрешение | FPS | p50, мс | p95, мс |
|---|---|---|---|---|---|
| yolo11n (ONNX) | CPU, Mac, onnxruntime | 640 | 37.9 | 26.5 | 27.8 |

## INT8 (S25)
1. `python -m export.quantize best.onnx data/ppe/images/val --out best.int8.onnx` — статическое INT8 (QDQ), калибровка на 100 картинках из `ml/data/ppe`.
2. FPS: `python -m export.bench run best.onnx` и `... best.int8.onnx`; строки — в таблицу.
3. Качество: события воркера на роликах для FP32 и INT8 — `python -m eval.evaluate events.json labels.json` (в `ml/eval` считаются found/missed/false по событиям, а не mAP; mAP по датасету — `yolo val` в `train/`).

Замеры INT8 **не сняты**: в репо нет `ml/data/ppe` и весов СИЗ (датасеты и веса вне git), на тесте проверена только крошечная модель. Строки ниже заполняет S15 после обучения.

| Модель | Устройство | Разрешение | FPS | p50, мс | p95, мс | mAP / found-missed-false |
|---|---|---|---|---|---|---|
| СИЗ FP32 (ONNX) | CPU, onnxruntime | 640 | не снято | не снято | не снято | не снято |
| СИЗ INT8 (ONNX) | CPU, onnxruntime | 640 | не снято | не снято | не снято | не снято |

## ONNX под LinQ (K14)
Три модели: СИЗ, люди и техника (`yolo11n.pt`), поза (`yolo11n-pose.pt`). Из `ml/`:
1. `python -m export.linq export --ppe runs/ppe-full-n-640/weights/best.pt --out linq_models` даёт `ppe.onnx`, `people.onnx`, `pose.onnx`. Каждый файл проверяется `check_onnx`; проверить отдельно: `python -m export.linq check linq_models/ppe.onnx`.
2. Калибровка: `python -m export.linq calib data/ppe/images/val --out linq_calib` собирает 200 JPEG 640x640 (простой resize, как в `quantize.py`).

Что требуем от ONNX: вход один, статический `1x3x640x640` (batch 1), выходы без динамических осей, **без NonMaxSuppression внутри** (NMS, трекинг и разбор выходов делает воркер на хосте), opset 12.

Ограничения SDK LinQ: **документация SDK и ответы L0 в репо не попали** (доступы вне git), поэтому ниже значения по умолчанию, а не подтверждённые факты.
- opset 12 выбран как консервативный: старые компиляторы редко принимают новее; если SDK принимает больше или меньше, меняется флагом `--opset`.
- Статические формы и отсутствие NMS обычно требуются компиляторами ускорителей; остаток графа (Reshape, Concat, Transpose, Sigmoid, Conv, Resize, MaxPool) для YOLO11 считается обычным.
- Проверить на LinQ и поправить этот раздел: какой opset принят, какие операции отвергнуты (SiLU раскладывается в Sigmoid*Mul), нужна ли INT8-калибровка и в каком формате.

## Сборка программ .tpu (P57)
`export/linq_compile.py` повторяет `compile.py` из SDK: ONNX 1x3x640x640 -> убрать инициализаторы из inputs -> `preliminary_model_conditioning(ModelProto)` -> `RegularModel.calibrate_torch` (200-500 настоящих кадров одним тензором N x 3 x 640 x 640: `demo/images/`, кадры роликов, val СИЗ; 500 кадров = около 2.4 ГБ ОЗУ, уменьшить можно `--count`, но не ниже 200) -> `quantize` -> `Network.from_quantized_model` -> `compiler.compile_(TPU_H1M, O5)`, при сбое O1. Результат в `--out`: `<имя>_b1_<o5|o1>_h1m.tpu` и `<тот же файл>.tpu.json`: file, input, outputs, names, sha256, sdk_version, preset, batch. Из `ml/`, только Linux x86_64 / Python 3.12 (WSL2 Ubuntu или Docker `python:3.12`, колёса SDK вне git):
```
docker run --rm --platform linux/amd64 -v "$PWD/..":/w -w /w/ml python:3.12 bash -c "pip install -r /w/tz/tpu_framework/packets/requirements.txt -r requirements-dev.txt opencv-python-headless onnx && python -m export.linq_compile ppe=linq_models/ppe.onnx people=linq_models/people.onnx pose=linq_models/pose.onnx --calib ../demo/images --calib data/ppe/images/val --out linq_programs"
```
(`requirements.txt` в `packets/` ссылается на колёса относительными путями, поэтому запускать `pip` надо из этой папки; поправьте путь под свою раскладку.) Воркеру отдавать путь к `.tpu`: `--model linq_programs/ppe_b1_o5_h1m.tpu`.
Если компилятор не принимает голову YOLO, ONNX режется через `get_sub_model` (раздел «Sub model» руководства `tpu_framework.pdf`), отрезанную часть делает постобработка (`decode_obb`).

Статус: **скрипт и тесты (с подменой SDK по вызовам compile.py) готовы; на настоящем SDK и железе не запускались** — колёса только Linux x86_64. Сравнение квантованной модели с ONNX на 50 кадрах: не снято.

| Модель | Средняя разница выходов | Совпадение рамок | Кадров |
|---|---|---|---|
| СИЗ / люди / поза | не снято | не снято | 50 |

## Замеры на удалённом Jupyter LinQ (P58)
`export/linq_bench.ipynb` (логика — `export/linq_bench.py`): загрузите в папку ноутбука `packages/` (`ppe|people|pose.tpu` + `*.tpu.json`), `ml.zip` (`git archive -o ml.zip HEAD ml`) и нажмите Run All. По желанию: `fp32/ppe.onnx` + `val/` (картинки, разметка YOLO в `labels/`) — точность INT8 против FP32; `videos/` — воркер на LinQ, события в `events.jsonl` и `events.json` (`python demo/replay_events.py --file events.json`). Итог — `linq_bench_result.json`: FPS, p50/p95 инференса, пред/пост, 1/2/4 потока, каждая программа и все вместе, ёмкость в камерах при 5 кадр/с (`LINQ_CAMERA_FPS`). Логин Jupyter нужен только для необязательной выгрузки на другой сервер (`LINQ_JUPYTER_URL`, `LINQ_JUPYTER_USER`/`LINQ_JUPYTER_PASSWORD` или ввод). На настоящем LinQ не запускался: тест исполняет ноутбук с подменой pytpu.
