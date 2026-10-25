from train.stats import report, split_stats


def write_example(root, name, text):
    image = root / "images/train" / f"{name}.jpg"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"image")
    if text is not None:
        label = root / "labels/train" / f"{name}.txt"
        label.parent.mkdir(parents=True, exist_ok=True)
        label.write_text(text, encoding="utf-8")


def test_counts_per_image_and_object_and_distinguishes_label_errors(tmp_path):
    write_example(tmp_path, "valid", "1 .5 .5 .2 .2\n1 .6 .6 .1 .1\n3 .5 .5 .2 .2")
    write_example(tmp_path, "empty", " \n")
    write_example(tmp_path, "missing", None)
    write_example(tmp_path, "mixed", "4 .5 .5 .2 .2\n4 nan .5 .2 .2\n9 .5 .5 .2 .2")
    write_example(tmp_path, "badbox", "1 .9 .5 .8 .2\n1 .5 .5 0 .2")
    write_example(tmp_path, "encoding", "")
    (tmp_path / "labels/train/encoding.txt").write_bytes(b"\xff")
    (tmp_path / "labels/train/orphan.txt").write_text("")
    counts = split_stats(
        tmp_path, "train", {0: "person", 1: "helmet", 3: "respirator", 4: "cigarette"}
    )
    assert counts["images"] == 6
    assert counts["empty"] == counts["missing"] == counts["orphan"] == 1
    assert counts["broken"] == 3
    assert counts["invalid_rows"] == 4
    assert counts["classes"][1] == {"images": 1, "objects": 2}
    assert counts["classes"][3] == {"images": 1, "objects": 1}
    assert counts["classes"][4] == {"images": 1, "objects": 1}
    assert counts["classes"][0] == {"images": 0, "objects": 0}


def test_report_handles_empty_split_and_prints_all_classes(tmp_path, capsys):
    write_example(tmp_path, "valid", "1 .5 .5 .2 .2")
    result = report(tmp_path, {1: "helmet", 4: "cigarette"})
    assert result["val"]["images"] == 0
    assert "| train | 1: helmet | 1 | 1 |" in capsys.readouterr().out
