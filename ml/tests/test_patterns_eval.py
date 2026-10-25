from datetime import datetime, timezone

from eval.patterns import NOT_READY, OK, SYNTH, format_table, pattern_stats

T0 = datetime(2026, 10, 21, 10, 0, 0, tzinfo=timezone.utc)


def ev(cam, typ, sec):
    return {
        "camera_id": cam,
        "ts": f"2026-10-21T10:00:{sec:02d}Z",
        "frame_id": 0,
        "type": typ,
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "confidence": 0.9,
    }


def test_found_missed_false_dups_and_status():
    events = [
        ev("a", "phone", 0),
        ev("a", "phone", 6),
        ev("a", "phone", 7),  # дубль
        ev("a", "phone", 20),  # ложное
        ev("a", "at_height", 0),
        ev("a", "at_height", 6),
        ev("n", "lone_worker", 0),  # на ролике без нарушения
        ev("n", "lone_worker", 1),
    ]
    labels = [
        {"camera": "a", "type": "phone", "start_s": 5, "end_s": 8},
        {"camera": "a", "type": "fence_crossing", "start_s": 5, "end_s": 8},
        {"camera": "a", "type": "at_height", "start_s": 5, "end_s": 8},
    ]
    s = pattern_stats(events, labels, negative=("n",), start=T0)
    assert s["phone"]["found"] == 1 and s["phone"]["dups"] == 1 and s["phone"]["false"] == 2
    assert s["phone"]["status"] == NOT_READY
    assert s["fence_crossing"]["missed"] == 1
    assert s["lone_worker"]["neg_false"] == 2 and s["lone_worker"]["status"] == NOT_READY
    assert s["at_height"]["false"] == 1  # событие в 0 с вне окна
    assert s["overcrowding"]["status"] == SYNTH


def test_clean_pattern_ok():
    events = [ev("a", "under_load", 6)]
    labels = [{"camera": "a", "type": "under_load", "start_s": 5, "end_s": 8}]
    s = pattern_stats(events, labels, start=T0)
    assert s["under_load"]["status"] == OK
    assert "| `under_load` | 1 | 0 | 0 | 0 | 0 |" in format_table(s)
