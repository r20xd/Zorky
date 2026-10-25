from worker.main import fake_detections
from worker.pipeline import Detection, find_violations, parse_ppe
from worker.rules import Debouncer

# Та же зона, что бэкенд создаёт для cam-1 при старте (backend/cmd/server/main.go)
ZONES = [
    {
        "id": "z-1",
        "kind": "danger",
        "polygon": [[0.6, 0.3], [0.95, 0.3], [0.95, 0.95], [0.6, 0.95]],
    }
]


def person(x: float, track_id: int = 1) -> Detection:
    return Detection("person", (x, 0.4, x + 0.1, 0.9), 0.9, track_id)


def test_person_in_danger_zone_fires_once_after_min_frames():
    debouncer = Debouncer(min_frames=3)
    fired = [find_violations([person(0.7)], ZONES, debouncer, now=float(t)) for t in range(5)]
    assert [len(f) for f in fired] == [0, 0, 1, 0, 0]
    violation = fired[2][0]
    assert violation.type == "danger_zone"
    assert violation.zone_id == "z-1"


def test_person_outside_zone_and_other_classes_are_ignored():
    debouncer = Debouncer(min_frames=1)
    helmet = Detection("helmet", (0.7, 0.4, 0.8, 0.5), 0.9, 1)
    assert find_violations([person(0.1), helmet], ZONES, debouncer, now=0) == []


def test_flammable_zone_is_not_danger_zone():
    debouncer = Debouncer(min_frames=1)
    zones = [{**ZONES[0], "kind": "flammable"}]
    assert find_violations([person(0.7)], zones, debouncer, now=0) == []


def test_fake_walk_enters_danger_zone_once():
    # Синтетика воркера (--fake) за проход кадра должна дать ровно одно событие
    debouncer = Debouncer()
    fired = [
        violation
        for frame_id in range(200)
        for violation in find_violations(fake_detections(frame_id), ZONES, debouncer, frame_id / 10)
    ]
    assert [v.type for v in fired if v.type == "danger_zone"] == ["danger_zone"]
    assert [v.type for v in fired if v.type == "no_helmet"] == ["no_helmet"]
    assert [v.type for v in fired if v.type == "no_respirator"] == ["no_respirator"]
    assert [v.type for v in fired if v.type == "smoking"] == ["smoking"]


def head(x: float, cls: str = "head", y: float = 0.4) -> Detection:
    return Detection(cls, (x + 0.03, y, x + 0.07, y + 0.1), 0.8, -1)


def run_helmet(detections: list[Detection]) -> list[str]:
    debouncer = Debouncer(min_frames=2)
    return [
        v.type for t in range(4) for v in find_violations(detections, [], debouncer, now=float(t))
    ]


def test_head_without_helmet_fires_once():
    assert run_helmet([person(0.1), head(0.1)]) == ["no_helmet", "no_respirator"]


def test_helmet_means_no_violation():
    mask = head(0.1, "respirator")
    assert run_helmet([person(0.1), head(0.1, "helmet"), mask]) == []
    assert run_helmet([person(0.1), head(0.1), head(0.1, "helmet"), mask]) == []


def test_head_outside_person_is_ignored():
    assert run_helmet([person(0.1), head(0.6)]) == []
    assert run_helmet([person(0.1), head(0.1, y=0.8)]) == []


def test_no_respirator_fires_once_for_head_or_helmet():
    assert run_helmet([person(0.1), head(0.1, "helmet")]) == ["no_respirator"]
    assert run_helmet([person(0.1), head(0.1, "helmet"), head(0.1)]) == ["no_respirator"]


def test_respirator_in_head_area_means_no_violation():
    assert run_helmet([person(0.1), head(0.1), head(0.1, "respirator")]) == ["no_helmet"]
    assert run_helmet([person(0.1), head(0.1, "helmet"), head(0.1, "respirator")]) == []


def test_respirator_outside_person_does_not_help():
    stray = head(0.6, "respirator")
    assert run_helmet([person(0.1), head(0.1, "helmet"), stray]) == ["no_respirator"]
    assert run_helmet([person(0.1), head(0.1, "helmet"), head(0.1, "respirator", y=0.8)]) == [
        "no_respirator"
    ]


def test_parse_tracks_normalizes_and_clips():
    from worker.pipeline import parse_tracks

    result = parse_tracks([[100, 50, 300, 250], [-10, 0, 700, 100]], [0.8, 0.5], [7, 8], 400, 500)
    assert result[0] == Detection("person", (0.25, 0.1, 0.75, 0.5), 0.8, 7, frame_aspect=0.8)
    assert result[1].bbox == (0.0, 0.0, 1.0, 0.2)
    assert result[1].track_id == 8


def test_parse_tracks_without_ids_is_empty():
    from worker.pipeline import parse_tracks

    assert parse_tracks([[1, 1, 2, 2]], [0.9], None, 10, 10) == []


def test_write_tracker_yaml(tmp_path):
    from worker.main import write_tracker_yaml

    path = write_tracker_yaml(str(tmp_path / "t.yaml"), track_buffer=120)
    text = (tmp_path / "t.yaml").read_text(encoding="utf-8")
    assert path.endswith("t.yaml")
    assert "track_buffer: 120" in text
    assert "fuse_score: true" in text


def test_one_event_per_track_even_after_leaving_and_returning():
    debouncer = Debouncer(min_frames=2)
    xs = [0.7, 0.7, 0.1, 0.7, 0.7, 0.7]
    fired = [find_violations([person(x)], ZONES, debouncer, now=0.1 * i) for i, x in enumerate(xs)]
    assert sum(len(f) for f in fired) == 1


def test_parse_ppe_keeps_ppe_classes_and_normalizes():
    names = {0: "person", 1: "helmet", 2: "head"}
    found = parse_ppe(
        [[10, 0, 50, 20], [0, 0, 5, 5], [-5, 10, 30, 400]],
        [0.9, 0.8, 0.7],
        [1, 0, 2],
        names,
        100,
        200,
    )
    assert [d.cls for d in found] == ["helmet", "head"]
    assert found[0].bbox == (0.1, 0.0, 0.5, 0.1)
    assert found[1].bbox == (0.0, 0.05, 0.3, 1.0)
    assert all(d.track_id == -1 for d in found)


def test_parse_ppe_maps_face_protection_to_respirator():
    names = {0: "person", 3: "face_protection"}
    found = parse_ppe([[10, 0, 50, 20]], [0.9], [3], names, 100, 200)
    assert [d.cls for d in found] == ["respirator"]


def test_cigarette_near_head_fires_smoking_once():
    cigarette = head(0.1, "cigarette", y=0.45)
    types = run_helmet([person(0.1), head(0.1, "helmet"), head(0.1, "respirator"), cigarette])
    assert types == ["smoking"]


def test_cigarette_outside_person_is_ignored():
    helmet, mask = head(0.1, "helmet"), head(0.1, "respirator")
    assert run_helmet([person(0.1), helmet, mask, head(0.6, "cigarette")]) == []
    assert run_helmet([person(0.1), helmet, mask, head(0.1, "cigarette", y=0.8)]) == []
