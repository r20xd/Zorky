from train import feedback_finetune as ft


def _setup(tmp_path):
    fb = tmp_path / "feedback"
    (fb / "images").mkdir(parents=True)
    for n in ("evt-1", "evt-2", "evt-3"):
        (fb / "images" / f"{n}.jpg").write_bytes(b"x")
    manifest = [
        {"id": "evt-1", "type": "no_helmet", "bbox": [0.1, 0.1, 0.5, 0.9], "verdict": "confirmed"},
        {"id": "evt-2", "type": "no_helmet", "bbox": [0.1, 0.1, 0.5, 0.9], "verdict": "false"},
        {"id": "evt-3", "type": "fall", "bbox": [0.1, 0.1, 0.5, 0.9], "verdict": "false"},
    ]
    import json

    (fb / "manifest.json").write_text(json.dumps(manifest))
    data = tmp_path / "ppe"
    (data / "images" / "train").mkdir(parents=True)
    (data / "images" / "val").mkdir(parents=True)
    for i in range(30):
        (data / "images" / "train" / f"t{i}.jpg").write_bytes(b"x")
    return fb, data


def test_relabel_and_run(tmp_path):
    fb, data = _setup(tmp_path)
    dets = [(0, 0.1, 0.1, 0.5, 0.9), (2, 0.2, 0.1, 0.3, 0.2), (2, 0.8, 0.1, 0.9, 0.2)]
    calls = []

    def train(weights, yaml, epochs, name, device):
        calls.append((weights, epochs, name))
        return tmp_path / "new.pt"

    args = ft.parse_args(
        [
            "--weights",
            str(tmp_path / "w.pt"),
            "--feedback",
            str(fb),
            "--data",
            str(data),
            "--out",
            str(tmp_path / "runs"),
        ]  # fmt: skip
    )
    result = ft.run(
        args,
        lambda image: dets,
        lambda w, y: {"head": 0.5} if w.name == "w.pt" else {"head": 0.6},
        train,
    )
    work = tmp_path / "runs" / result["run"]
    assert result["run"].startswith("feedback-")
    classes = lambda n: [  # noqa: E731
        int(x.split()[0])
        for x in (work / "feedback/labels" / f"{n}.txt").read_text().split("\n")
        if x
    ]
    assert classes("evt-1") == [0, 2, 2]
    assert classes("evt-2") == [0, 2]  # head внутри рамки человека убран, снаружи остался
    assert not (work / "feedback/labels/evt-3.txt").exists()
    assert result["report"]["пропущен тип fall (нет класса детектора)"] == 1
    # 2 кадра фидбэка + 20 исходных
    assert len((work / "train.txt").read_text().split()) == 22
    assert calls[0][1] == 3
    assert result["after"]["head"] > result["before"]["head"]
