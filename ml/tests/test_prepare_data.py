import json
import zipfile

from train import prepare_data as pd

CATEGORIES = [
    {"id": 0, "name": "hard-hat"},
    {"id": 1, "name": "head"},
    {"id": 2, "name": "helmet"},
    {"id": 3, "name": "person"},
]


def test_coco_to_yolo_normalizes_and_maps_classes():
    cats = {c["id"]: c["name"] for c in CATEGORIES}
    anns = [
        {"category_id": 2, "bbox": [10, 20, 20, 40]},
        {"category_id": 1, "bbox": [0, 0, 100, 50]},
        {"category_id": 0, "bbox": [0, 0, 5, 5]},  # чужой класс
        {"category_id": 3, "bbox": [90, 40, 50, 50]},  # выходит за кадр -> обрезается
    ]
    lines = pd.coco_to_yolo_lines(anns, cats, 100, 100)
    assert lines[0] == "1 0.200000 0.400000 0.200000 0.400000"
    assert lines[1] == "2 0.500000 0.250000 1.000000 0.500000"
    assert len(lines) == 3
    assert lines[2].startswith("0 0.950000 0.650000 0.100000 0.500000")


def test_huggingface_actual_categories_produce_helmet_and_head():
    categories = {0: "hardhat", 1: "no-hardhat"}
    annotations = [{"category_id": category, "bbox": [10, 10, 20, 20]} for category in categories]
    lines = pd.coco_to_yolo_lines(annotations, categories, 100, 100)
    assert [line.split()[0] for line in lines] == ["1", "2"]


def make_zip(path, n):
    images = [{"id": i, "file_name": f"i{i}.jpg", "width": 64, "height": 48} for i in range(n)]
    anns = [{"id": i, "image_id": i, "category_id": 2, "bbox": [8, 8, 16, 16]} for i in range(n)]
    coco = {"images": images, "annotations": anns, "categories": CATEGORIES}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("_annotations.coco.json", json.dumps(coco))
        for i in range(n):
            img = path.parent / f"i{i}.jpg"
            img.write_bytes(b"fake")
            z.write(img, f"i{i}.jpg")


def test_convert_zip_makes_yolo_layout_and_respects_limit(tmp_path):
    zip_path = tmp_path / "train.zip"
    make_zip(zip_path, 5)
    out = tmp_path / "out"
    assert pd.convert_zip(zip_path, out, limit=3) == 3
    assert len(list((out / "images").iterdir())) == 3
    label = (out / "labels" / "i0.txt").read_text(encoding="utf-8")
    assert label.split()[0] == "1"
