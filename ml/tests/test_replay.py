import importlib.util
from datetime import datetime, timezone
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / "demo"


def load_replay():
    spec = importlib.util.spec_from_file_location("replay_events", DEMO / "replay_events.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_with_now_replaces_only_ts():
    replay = load_replay()
    original = replay.load_events(DEMO / "events.sample.json")[0]
    updated = replay.with_now(original, datetime(2026, 10, 21, 12, 30, tzinfo=timezone.utc))
    assert updated["ts"] == "2026-10-21T12:30:00Z"
    assert {k: v for k, v in updated.items() if k != "ts"} == {
        k: v for k, v in original.items() if k != "ts"
    }
