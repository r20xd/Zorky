from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from train.train import check_hardware, print_class_metrics


def fake_torch(available):
    return SimpleNamespace(
        __version__="2.test",
        version=SimpleNamespace(cuda="12.8" if available else None),
        cuda=SimpleNamespace(
            is_available=lambda: available,
            device_count=lambda: 1,
            get_device_name=Mock(return_value="NVIDIA GeForce RTX 5070"),
        ),
    )


@pytest.mark.parametrize("device", ["0", "0,1", "cuda:0"])
def test_explicit_gpu_without_cuda_fails_with_install_command(device, capsys):
    torch = fake_torch(False)
    with pytest.raises(SystemExit, match="https://download.pytorch.org/whl/cu128"):
        check_hardware(torch, device)
    assert "torch: 2.test; CUDA: нет" in capsys.readouterr().out
    torch.cuda.get_device_name.assert_not_called()


@pytest.mark.parametrize("device", [None, "cpu", "mps"])
def test_non_cuda_device_does_not_require_cuda(device):
    check_hardware(fake_torch(False), device)


def test_gpu_info_includes_version_and_name(capsys):
    check_hardware(fake_torch(True), "0")
    output = capsys.readouterr().out
    assert "CUDA: 12.8" in output
    assert "GPU 0: NVIDIA GeForce RTX 5070" in output


def test_class_metrics_use_validated_class_ids(capsys):
    metrics = SimpleNamespace(
        names={0: "person", 1: "helmet", 2: "head"},
        box=SimpleNamespace(ap_class_index=[1, 2], ap50=[0.625, 0.25]),
    )
    print_class_metrics(metrics)
    output = capsys.readouterr().out
    assert "helmet: 0.6250" in output
    assert "head: 0.2500" in output
    assert "person" not in output


def test_no_validation_metrics(capsys):
    print_class_metrics(None)
    assert "нет результатов валидации" in capsys.readouterr().out


def test_resume_and_patience_arguments(tmp_path, monkeypatch):
    from train import train

    monkeypatch.setattr(train, "RUNS", tmp_path)
    checkpoint = tmp_path / "interrupted" / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.touch()
    args = train.parse_args(["--name", "interrupted", "--resume", "--patience", "0"])
    assert args.resume == checkpoint
    assert args.patience == 0
    assert train.parse_args(["--resume", str(checkpoint)]).resume == checkpoint
    with pytest.raises(SystemExit):
        train.parse_args(["--resume", str(tmp_path / "missing.pt")])
    with pytest.raises(SystemExit):
        train.parse_args(["--patience", "-1"])
    assert train.parse_args([]).resume is None


def test_training_saves_class_metrics_in_actual_run_without_training(tmp_path, monkeypatch, capsys):
    import json
    import sys

    from train import train

    metrics = SimpleNamespace(
        names={0: "person", 1: "helmet", 3: "respirator"},
        box=SimpleNamespace(
            ap_class_index=[1, 3], ap50=[0.8, 0.4], ap=[0.6, 0.2], map50=0.6, map=0.4
        ),
    )
    actual_run = tmp_path / "ppe2"
    model = SimpleNamespace(
        train=Mock(return_value=metrics), trainer=SimpleNamespace(save_dir=actual_run)
    )
    yolo = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "torch", fake_torch(True))
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=yolo))
    monkeypatch.setattr(train, "resolved_data_yaml", lambda: tmp_path / "data.yaml")
    checkpoint = tmp_path / "last.pt"
    checkpoint.touch()
    train.main(["--resume", str(checkpoint), "--patience", "12", "--device", "0"])
    yolo.assert_called_once_with(str(checkpoint))
    assert model.train.call_args.kwargs["resume"] is True
    assert model.train.call_args.kwargs["patience"] == 12
    saved = json.loads((actual_run / "metrics.json").read_text())
    assert saved["classes"] == {
        "helmet": {"class_id": 1, "mAP50": 0.8, "mAP50-95": 0.6},
        "respirator": {"class_id": 3, "mAP50": 0.4, "mAP50-95": 0.2},
    }
    assert saved["mAP50"] == 0.6
    assert saved["mAP50-95"] == 0.4
    assert "| ppe2 | respirator | 0.4000 | 0.2000 |" in capsys.readouterr().out


def test_missing_validation_saved_as_null(tmp_path):
    import json

    from train.train import save_metrics

    saved = json.loads(save_metrics(None, tmp_path).read_text())
    assert saved == {"mAP50": None, "mAP50-95": None, "classes": {}}
