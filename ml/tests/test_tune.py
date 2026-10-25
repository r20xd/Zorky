import json

from eval.tune import format_table, main, replay, tune


def frames():
    out = []
    # трек 1: настоящее нарушение 5..9 с, 5 кадров по 1 с, conf 0.7
    for i in range(5, 10):
        out.append(
            {
                "camera": "cam-1",
                "t": float(i),
                "detections": [{"type": "no_helmet", "track_id": 1, "conf": 0.7}],
            }
        )
    # трек 2: шум, 3 кадра слабого conf 0.35 в 20..22 с
    for i in range(20, 23):
        out.append(
            {
                "camera": "cam-1",
                "t": float(i),
                "detections": [{"type": "no_helmet", "track_id": 2, "conf": 0.35}],
            }
        )
    # пустые кадры, чтобы серии обрывались
    out.append({"camera": "cam-1", "t": 30.0, "detections": []})
    return out


LABELS = [{"camera": "cam-1", "type": "no_helmet", "start_s": 5, "end_s": 9}]


def test_replay_threshold_and_frames():
    assert len(replay(frames(), 0.3, 3, float("inf"))) == 2
    assert len(replay(frames(), 0.5, 3, float("inf"))) == 1
    assert replay(frames(), 0.3, 6, float("inf")) == []


def test_best_row_filters_noise():
    rows = tune(frames(), LABELS, [0.3, 0.5], [3, 6], [float("inf")])
    best = rows[0]
    assert (best["conf"], best["frames"]) == (0.5, 3)
    assert (best["found"], best["missed"], best["false"]) == (1, 0, 0)
    assert "Лучшая" in format_table(rows)


def test_cli(tmp_path, capsys, monkeypatch):
    (tmp_path / "f.json").write_text(json.dumps(frames()))
    (tmp_path / "l.json").write_text(json.dumps(LABELS))
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            str(tmp_path / "f.json"),
            str(tmp_path / "l.json"),
            "--conf",
            "0.5",
            "--frames-n",
            "3",
            "--cooldown",
            "inf",
        ],
    )
    main()
    assert "Лучшая" in capsys.readouterr().out


def test_false_limit_beats_equal_sum():
    # без ограничения (max_false большой) побеждает меньше пропусков; при 0 — только без ложных
    rows = tune(frames(), LABELS, [0.3, 0.5], [3], [float("inf")], max_false=0)
    assert rows[0]["false"] == 0
    loose = tune(frames(), LABELS, [0.3, 0.5], [3], [float("inf")], max_false=5)
    assert loose[0]["missed"] <= rows[0]["missed"]
    from eval.tune import sort_key

    a = {"false": 0, "missed": 3, "conf": 0.5, "frames": 3}
    b = {"false": 1, "missed": 0, "conf": 0.3, "frames": 3}
    assert sort_key(a, 0) < sort_key(b, 0)  # b нарушает предел, хоть и без пропусков
    assert sort_key(b, 1) < sort_key(a, 1)
