import json
from datetime import datetime, timezone

import pytest

from eval.evaluate import evaluate, format_report, main

T0 = datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc)


def ev(cam, typ, ts):
    return {
        "camera_id": cam,
        "ts": ts,
        "frame_id": 0,
        "type": typ,
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "confidence": 0.9,
        "track_id": 1,
    }


EVENTS = [
    ev("cam-1", "no_helmet", "2026-10-21T10:00:00Z"),  # t=0, ложное для разметки с 5 с
    ev("cam-1", "no_helmet", "2026-10-21T10:00:06Z"),  # t=6, найдено
    ev("cam-1", "smoking", "2026-10-21T10:00:30Z"),  # ложное
]
LABELS = [
    {"camera": "cam-1", "type": "no_helmet", "start_s": 5, "end_s": 8},
    {"camera": "cam-1", "type": "danger_zone", "start_s": 10, "end_s": 12},
]


def test_counts_by_type():
    r = evaluate(EVENTS, LABELS, tol=1.0, start=T0)
    assert r["no_helmet"] == {"found": 1, "missed": 0, "false": 1}
    assert r["danger_zone"] == {"found": 0, "missed": 1, "false": 0}
    assert r["smoking"] == {"found": 0, "missed": 0, "false": 1}


def test_other_camera_does_not_match():
    labels = [{"camera": "cam-2", "type": "no_helmet", "start_s": 5, "end_s": 8}]
    assert evaluate(EVENTS, labels, start=T0)["no_helmet"]["missed"] == 1


def test_report_and_cli(tmp_path, capsys, monkeypatch):
    assert "всего" in format_report(evaluate(EVENTS, LABELS, start=T0))
    (tmp_path / "e.json").write_text(json.dumps(EVENTS))
    (tmp_path / "l.json").write_text(json.dumps(LABELS))
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            str(tmp_path / "e.json"),
            str(tmp_path / "l.json"),
            "--start",
            "2026-10-21T10:00:00Z",
        ],
    )
    main()
    assert "no_helmet" in capsys.readouterr().out


def test_full_dup_delay_and_clean_rate(tmp_path):
    from eval.evaluate import clean_false_per_hour, evaluate_full, format_full, load_events

    events = [
        ev("a", "fall", "2026-10-21T10:00:05Z"),  # задержка 2 с от начала метки (3 с)
        ev("a", "fall", "2026-10-21T10:00:06Z"),  # дубль в той же метке
        ev("clean", "fall", "2026-10-21T10:00:01Z"),  # ложное на ролике без нарушений
    ]
    labels = [{"camera": "a", "type": "fall", "start_s": 3, "end_s": 7}]
    res = evaluate_full(events, labels, start=T0)
    assert res["fall"]["found"] == 1 and res["fall"]["dup"] == 1 and res["fall"]["false"] == 1
    assert res["fall"]["delays"] == [2.0]
    clean = clean_false_per_hour(events, labels, {"a": 10.0, "clean": 1800.0})
    assert clean["false"] == 1 and abs(clean["per_hour"] - 2.0) < 1e-9
    assert "задержка" in format_full(res, clean)
    path = tmp_path / "e.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    assert load_events(path) == events


def test_default_start_is_video_start():
    # без --start время считается от 2000-01-01T00:00:00Z, как пишет воркер --offline
    e = [ev("c", "fall", "2000-01-01T00:00:04Z")]
    labels = [{"camera": "c", "type": "fall", "start_s": 3, "end_s": 5}]
    assert evaluate(e, labels)["fall"]["found"] == 1


def test_one_alarm_cannot_confirm_two_overlapping_episodes():
    from eval.evaluate import evaluate_full

    events = [ev("c", "fall", "2000-01-01T00:00:04Z")]
    labels = [
        {"camera": "c", "type": "fall", "start_s": 2, "end_s": 5},
        {"camera": "c", "type": "fall", "start_s": 3, "end_s": 6},
    ]
    row = evaluate_full(events, labels)["fall"]
    assert (row["found"], row["missed"], row["false"], row["dup"]) == (1, 1, 0, 0)


def test_matching_reassigns_ambiguous_alarm_instead_of_missing_person():
    from eval.evaluate import evaluate_full

    events = [
        ev("c", "fall", "2000-01-01T00:00:02Z"),
        ev("c", "fall", "2000-01-01T00:00:03Z") | {"track_id": 2},
    ]
    labels = [
        {"camera": "c", "type": "fall", "start_s": 0, "end_s": 4},
        {"camera": "c", "type": "fall", "start_s": 0, "end_s": 5, "track_id": 1},
    ]
    row = evaluate_full(events, labels, tol=0)["fall"]
    assert (row["found"], row["missed"], row["dup"]) == (2, 0, 0)


def test_wrong_identity_and_zone_are_not_true_positives():
    events = [ev("c", "fall", "2000-01-01T00:00:02Z") | {"zone_id": "z1"}]
    for identity in ({"track_id": 2}, {"zone_id": "z2"}):
        labels = [{"camera": "c", "type": "fall", "start_s": 0, "end_s": 4} | identity]
        assert evaluate(events, labels)["fall"] == {"found": 0, "missed": 1, "false": 1}


def test_summary_penalizes_duplicates_and_reports_tail_latency():
    from eval.evaluate import metric_summary

    row = {"found": 2, "missed": 1, "false": 1, "dup": 1, "delays": [1.0, 5.0]}
    summary = metric_summary({"fall": row})["fall"]
    assert summary["precision"] == 0.5
    assert summary["recall"] == pytest.approx(2 / 3)
    assert summary["f1"] == pytest.approx(4 / 7)
    assert summary["duplicate_rate"] == 0.25
    assert summary["delay_p50_s"] == 3.0
    assert summary["delay_p95_s"] == 4.8
    assert summary["delay_samples"] == 2


def test_summary_does_not_invent_quality_for_missing_evidence():
    from eval.evaluate import metric_summary

    missed = {"found": 0, "missed": 1, "false": 0, "dup": 0, "delays": []}
    negative = {"found": 0, "missed": 0, "false": 1, "dup": 0, "delays": []}
    summary = metric_summary({"missed": missed, "negative": negative})
    assert summary["missed"]["precision"] is None
    assert summary["missed"]["recall"] == 0
    assert summary["missed"]["delay_p95_s"] is None
    assert summary["negative"]["recall"] is None
    assert summary["negative"]["precision"] == 0


def test_early_tolerance_is_not_negative_latency():
    from eval.evaluate import metric_summary

    row = {"found": 1, "missed": 0, "false": 0, "dup": 0, "delays": [-0.5]}
    summary = metric_summary({"fall": row})["fall"]
    assert summary["early_matches"] == 1
    assert summary["delay_p50_s"] == 0


@pytest.mark.parametrize("tol", [-1, float("nan"), float("inf")])
def test_invalid_tolerance_is_rejected(tol):
    with pytest.raises(ValueError, match="tol"):
        evaluate([], [], tol=tol)


def test_json_report_cli(tmp_path, monkeypatch):
    events = tmp_path / "events.json"
    labels = tmp_path / "labels.json"
    report = tmp_path / "nested" / "report.json"
    events.write_text(json.dumps(EVENTS))
    labels.write_text(json.dumps(LABELS))
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate",
            str(events),
            str(labels),
            "--start",
            T0.isoformat(),
            "--json-out",
            str(report),
        ],
    )
    main()
    saved = json.loads(report.read_text())
    assert saved["by_type"]["no_helmet"]["precision"] == 0.5
    assert saved["by_type"]["danger_zone"]["delay_p95_s"] is None
    assert "not end-to-end" in saved["delay_scope"]


def test_only_verified_negative_cameras_enter_false_alarm_denominator():
    from eval.evaluate import clean_false_per_hour

    events = [ev("unchecked", "fall", "2000-01-01T00:00:02Z")]
    r = clean_false_per_hour(
        events, [], {"checked": 3600, "unchecked": 7200}, negative_cameras=["checked"]
    )
    assert r["hours"] == 1
    assert r["false"] == 0
    assert r["negative_scope"] == "explicit"
    assert r["zero_count_upper95_per_hour"] == pytest.approx(2.995732)
    assert clean_false_per_hour([], [], {}, negative_cameras=[])["per_hour"] is None
    with pytest.raises(ValueError, match="негативные"):
        clean_false_per_hour([], [], {}, negative_cameras=["missing"])


def test_pattern_report_uses_same_one_to_one_protocol():
    from eval.patterns import pattern_stats

    events = [ev("c", "phone", "2000-01-01T00:00:04Z")]
    labels = [
        {"camera": "c", "type": "phone", "start_s": 2, "end_s": 5},
        {"camera": "c", "type": "phone", "start_s": 3, "end_s": 6},
    ]
    row = pattern_stats(events, labels)["phone"]
    assert (row["found"], row["missed"], row["dups"]) == (1, 1, 0)
