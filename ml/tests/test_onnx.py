from pathlib import Path

import pytest

from worker.infer import load_model
from worker.main import ppe_detections, send_metrics, yolo_detections
from worker.metrics import MetricsReporter

ONNX = Path(__file__).parents[1] / "yolo11n.onnx"


class Client:
    def post_metrics(self, body):
        pass


def test_fps_is_logged(capsys):
    reporter = MetricsReporter("cam-1", every_s=1)
    reporter.report(0.0)
    reporter.frame(0.02)
    assert send_metrics(Client(), reporter, 2.0)
    assert "FPS 0.5" in capsys.readouterr().out


@pytest.mark.skipif(not ONNX.exists(), reason="ml/yolo11n.onnx вне git")
def test_onnx_worker_paths():
    pytest.importorskip("ultralytics")
    pytest.importorskip("onnxruntime")
    np = pytest.importorskip("numpy")
    model = load_model(str(ONNX))
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert isinstance(yolo_detections(model, frame), list)
    assert isinstance(ppe_detections(model, frame), list)
