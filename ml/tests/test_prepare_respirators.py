import zipfile

from train.prepare_respirators import image_split, merge, voc_to_yolo


def annotation(filename, label="Gas_Mask"):
    return f"""<annotation><filename>{filename}</filename><size><width>100</width>
    <height>50</height></size><object><name>{label}</name><bndbox>
    <xmin>10</xmin><ymin>5</ymin><xmax>50</xmax><ymax>25</ymax>
    </bndbox></object></annotation>""".encode()


def test_voc_maps_only_gas_mask():
    filename, labels = voc_to_yolo(annotation("sample.jpg"))
    assert filename == "sample.jpg"
    assert labels == ["3 0.300000 0.300000 0.400000 0.400000"]
    assert voc_to_yolo(annotation("sample.jpg", "Mask"))[1] == []


def test_nearby_frames_stay_in_same_split():
    assert image_split("20211102_160400.jpg") == image_split("20211102_160459.jpg")


def test_merge_preserves_existing_data_and_limits_each_split(tmp_path):
    original = tmp_path / "ppe/images/train/helmet.jpg"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"existing")
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(100):
            filename = f"20211102_{index:04d}00.jpg"
            archive.writestr(f"data/Annotations/{index}.xml", annotation(filename))
            archive.writestr(f"data/JPEGImages/{filename}", b"image")
    with zipfile.ZipFile(source) as archive:
        assert merge(archive, tmp_path / "ppe", limit=2) == {"train": 2, "val": 2}
    assert original.read_bytes() == b"existing"
    labels = list((tmp_path / "ppe/labels").rglob("*.txt"))
    assert len(labels) == 4
    assert all(p.read_text().startswith("3 ") for p in labels)


def test_merge_matches_voc_filename_case_insensitively(tmp_path):
    source = tmp_path / "source.zip"
    # В настоящем PPED XML содержит IMG_1494.JPG, а JPEG в ZIP — img_1494.jpg.
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(100):
            filename = f"20211102_{index:04d}00.JPG"
            archive.writestr(f"data/Annotations/{index}.xml", annotation(filename))
            archive.writestr(f"data/JPEGImages/{filename.lower()}", b"image")
    with zipfile.ZipFile(source) as archive:
        assert merge(archive, tmp_path / "ppe", limit=1) == {"train": 1, "val": 1}


def test_remote_member_checks_compression_and_crc(tmp_path, monkeypatch):
    import io

    import pytest

    from train import prepare_respirators as pr

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("image.jpg", b"real image bytes")
    data = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo("image.jpg")
    source = type("Source", (), {"url": "unused", "size": len(data)})()
    monkeypatch.setattr(pr, "RemoteZip", lambda *args, **kwargs: io.BytesIO(data))
    assert pr.remote_member(source, info) == b"real image bytes"
    info.CRC = 0
    with pytest.raises(ValueError, match="CRC"):
        pr.remote_member(source, info)


def test_parallel_remote_merge_does_not_share_archive_pointer(tmp_path, monkeypatch):
    import io
    import threading

    from train import prepare_respirators as pr

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(100):
            filename = f"20211102_{index:04d}00.jpg"
            archive.writestr(f"data/Annotations/{index}.xml", annotation(filename))
            archive.writestr(f"data/JPEGImages/{filename}", b"image")
    data = buffer.getvalue()

    class FakeRemote(pr.RemoteZip):
        def __init__(self):
            self.buffer = io.BytesIO(data)
            self.main_thread = threading.get_ident()

        def read(self, size=-1):
            assert threading.get_ident() == self.main_thread
            return self.buffer.read(size)

        def seek(self, offset, whence=0):
            return self.buffer.seek(offset, whence)

        def tell(self):
            return self.buffer.tell()

    def independent_member(source, info, end_offset=None):
        with zipfile.ZipFile(io.BytesIO(data)) as reader:
            return reader.read(info.filename)

    monkeypatch.setattr(pr, "remote_member", independent_member)
    with zipfile.ZipFile(FakeRemote()) as archive:
        assert pr.merge(archive, tmp_path, limit=2, download_workers=4) == {"train": 2, "val": 2}
    assert len(list((tmp_path / "images").rglob("*.jpg"))) == 4


def test_429_shared_cooldown_and_three_attempt_limit(monkeypatch):
    import urllib.error
    from unittest.mock import Mock

    import pytest

    from train import prepare_respirators as pr

    clock = [0.0]
    monkeypatch.setattr(pr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pr.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    cooldown = pr.Cooldown()
    monkeypatch.setattr(pr, "RATE_LIMIT", cooldown)
    calls = []

    def reject(*args, **kwargs):
        calls.append(clock[0])
        raise urllib.error.HTTPError("unused", 429, "rate limit", {"Retry-After": "7"}, None)

    monkeypatch.setattr(pr.urllib.request, "urlopen", reject)
    with pytest.raises(urllib.error.HTTPError):
        pr.fetch_range(Mock(), 1)
    assert calls == [0, 60, 120]
    assert cooldown.until == 180
    # Следующий независимый reader обязан ждать остаток общей задержки.
    cooldown.wait()
    assert clock[0] == 180


def test_retry_after_http_date_and_missing_header(monkeypatch):
    from train import prepare_respirators as pr

    monkeypatch.setattr(pr.time, "time", lambda: 0)
    monkeypatch.setattr(pr.time, "monotonic", lambda: 0)
    cooldown = pr.Cooldown()
    cooldown.defer("Thu, 01 Jan 1970 00:02:00 GMT")
    assert cooldown.until == 120
    for header in (None, "invalid", "nan", "-1"):
        cooldown = pr.Cooldown()
        cooldown.defer(header)
        assert cooldown.until == 60


def test_global_rate_limit_never_exceeds_100_requests_per_minute(monkeypatch):
    from train import prepare_respirators as pr

    clock = [0.0]
    monkeypatch.setattr(pr.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(pr.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    limiter = pr.Cooldown()
    for _ in range(100):
        limiter.wait()
    assert clock[0] == 0
    limiter.wait()
    assert clock[0] == 60
    limiter.defer("0")
    assert limiter.until == 120


def test_remote_member_fetches_header_and_payload_in_one_request(monkeypatch):
    import io

    from train import prepare_respirators as pr

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.jpg", b"image")
    data = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info, end = archive.getinfo("sample.jpg"), archive.start_dir
    requests = []

    class Reader(io.BytesIO):
        def read(self, size=-1):
            requests.append((self.tell(), size))
            return super().read(size)

    monkeypatch.setattr(pr, "RemoteZip", lambda *args, **kwargs: Reader(data))
    source = type("Source", (), {"url": "unused", "size": len(data)})()
    assert pr.remote_member(source, info, end) == b"image"
    assert requests == [(info.header_offset, end - info.header_offset)]
