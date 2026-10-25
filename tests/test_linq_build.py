"""Сборка программы для LinQ из новых весов: команды стенда, разбор статуса, выбор порога."""

import pytest

from linq import build_program as bp
from linq import frames


def test_frames_plan_splits_evenly_and_gives_rest_to_longest():
    assert frames.plan({"a": 100, "b": 300, "c": 200}, 7) == {"a": 2, "b": 3, "c": 2}
    assert frames.plan({}, 5) == {}
    assert frames.plan({"a": 10}, 0) == {}


def test_frames_positions_are_distinct_for_calibration_and_eval():
    calib = frames.positions(1000, 10, 0.25)
    check = frames.positions(1000, 10, 0.5)
    assert len(calib) == len(check) == 10
    assert not set(calib) & set(check)  # проверка не на кадрах калибровки
    assert frames.positions(3, 10, 0.5) == [0, 1, 2] or len(frames.positions(3, 10, 0.5)) <= 3
    assert frames.positions(0, 5, 0.5) == []


def test_safe_name_rejects_shell_tricks():
    assert bp.safe_name("ppe48") == "ppe48"
    for bad in ("", "1ppe", "a b", "a;rm", "../x", "a" * 30):
        with pytest.raises(SystemExit):
            bp.safe_name(bad)


def test_compile_command_is_detached_and_writes_exit_code():
    cmd = bp.compile_command("ppe48", "model.onnx", 48)
    assert "setsid nohup" in cmd and "< /dev/null" in cmd
    assert "--count 48 --min-frames 48" in cmd
    assert "ppe48=$HOME/work/mosprom/build/ppe48/model.onnx" in cmd
    assert "echo $? > $HOME/work/mosprom/build/ppe48/EXIT" in cmd
    assert "~" not in cmd  # тильда после «=» не раскрывается


def test_accuracy_command_uses_both_environments():
    cmd = bp.accuracy_command("ppe48", "hackathon1")
    assert "../venv/bin/python stand_accuracy.py prep" in cmd  # onnxruntime и cv2
    assert "python3 stand_accuracy.py run hackathon1 ppe48_b1_o5_h1m.tpu" in cmd  # pika
    assert "ACC_EXIT" in cmd


def test_best_conf_picks_highest_f1():
    sweep = {
        "0.3": {"f1": 0.54},
        "0.4": {"f1": 0.57, "recall": 0.7},
        "0.5": {"f1": None},
        "0.6": {"f1": 0.5},
    }
    conf, metrics = bp.best_conf(sweep)
    assert conf == 0.4 and metrics["recall"] == 0.7
    with pytest.raises(SystemExit):
        bp.best_conf({"0.3": {"f1": None}})


def test_parse_status():
    running = "RUNNING=1\n[ppe48] калибровка готова: 1200 с от старта\n"
    info = bp.parse_status(running)
    assert info["running"] and info["exit"] is None and "калибровка готова" in info["stage"]
    done = "EXIT=0\nPROGRAM=1\n"
    info = bp.parse_status(done)
    assert not info["running"] and info["exit"] == 0 and info["program"]
    assert bp.parse_status("")["exit"] is None


def test_update_measured_sets_program_and_calibration(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "measured.json").write_text('{"linq_program": "ppe16", "cpu_laptop": {}}', "utf-8")
    monkeypatch.setattr(bp, "ROOT", tmp_path)
    bp.update_measured("ppe64", {})
    import json

    data = json.loads((docs / "measured.json").read_text("utf-8"))
    assert data["linq_program"] == "ppe64" and data["calibration_frames"] == 64
    assert data["cpu_laptop"] == {}


def test_status_command_echo_is_not_mistaken_for_answer():
    echoed = bp.status_command("ppe48")
    info = bp.parse_status(echoed)  # терминал печатает команду как есть
    assert not info["running"] and info["exit"] is None and not info["program"]


def test_running_is_detected_by_process_name_not_setsid_pid():
    """Регресс: $! - это pid обёртки setsid, она сразу завершается."""
    assert "pgrep -f" in bp.status_command("ppe48")
    assert "export.linq_compile ppe48=" in bp.status_command("ppe48")
    assert "$!" not in bp.compile_command("ppe48", "model.onnx", 48)
