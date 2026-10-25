"""eval.videos: нарезка, кадры, SHA256, каталог, страница проверки (на синтетическом ролике)."""

import hashlib

import cv2
import numpy as np
import pytest

from eval import videos


def make_video(path, seconds=25, fps=10, size=(64, 48)):
    wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(seconds * fps):
        frame = np.full((size[1], size[0], 3), (i * 2) % 255, np.uint8)
        wr.write(frame)
    wr.release()


def test_plan_chunks_bounds():
    for d in (5, 12, 30, 31, 45, 61, 95.5, 200):
        ch = videos.plan_chunks(d)
        assert abs(ch[-1][1] - d) < 0.01
        if d >= 10:
            assert all(10 - 0.01 <= b - a <= 30 + 0.01 for a, b in ch), (d, ch)


def test_cut_frames_sha_review(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    make_video(src, seconds=70)
    parts = videos.cut(src, tmp_path / "out", "syn")
    assert len(parts) == 3
    for p in parts:
        assert 10 <= videos.probe(p)[0] <= 30.5
    imgs = videos.frames(parts[0], tmp_path / "img", 4)
    assert len(imgs) == 4 and all(i.stat().st_size > 0 for i in imgs)
    ivs = [{"type": "fall", "start_s": 2.0, "end_s": 4.0}]
    lab = videos.frames_in_intervals(parts[0], ivs, tmp_path / "img2", 2)
    assert len(lab) == 2 and all("fall" in i.name for i in lab)
    assert videos.sha256_file(parts[0]) == hashlib.sha256(parts[0].read_bytes()).hexdigest()

    sha = videos.sha256_file(parts[0])
    md = (
        "| файл | ссылка | sha256 | автор | лицензия | Release | сценарий | интервалы |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"| {parts[0].name} | file:///x | {sha} | я | CC0 | да | fall | fall 2.0-4.5 (черновик) |\n"
    )
    rows = videos.parse_catalog(md)
    assert len(rows) == 1 and rows[0]["sha256"] == sha
    assert videos.parse_intervals(rows[0]["intervals"]) == [
        {"type": "fall", "start_s": 2.0, "end_s": 4.5}
    ]
    assert videos.fetch(rows, tmp_path / "out") == 0
    rows[0]["sha256"] = "0" * 64
    assert videos.fetch(rows, tmp_path / "out") == 1
    rows[0]["sha256"] = sha

    page = videos.build_review(rows, tmp_path / "out")
    assert "data:image/jpeg;base64" in page and "CC0" in page and "черновик" in page
    assert page.count("<img") >= 2 * videos.probe(parts[0])[0]
    assert "class='t in'" in page


def test_can_release():
    assert videos.can_release("CC0") and videos.can_release("CC BY 4.0")
    assert not videos.can_release("Pexels License")


def test_fetch_zip_member(tmp_path):
    import zipfile

    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("dir/x.mp4", b"data")
    row = {
        "file": "x.mp4",
        "url": f"{z.as_uri()}#dir/x.mp4",
        "sha256": hashlib.sha256(b"data").hexdigest(),
    }
    assert videos.fetch([row], tmp_path / "v") == 0
    assert (tmp_path / "v" / "x.mp4").read_bytes() == b"data"


def test_pixabay_search_no_key_leak(monkeypatch, capsys):
    monkeypatch.setenv("PIXABAY_API_KEY", "SECRETKEY123")
    seen = []

    def fake(url):
        seen.append(url)
        return {
            "hits": [
                {
                    "id": 1,
                    "pageURL": "https://pixabay.com/videos/x-1/",
                    "user": "u",
                    "duration": 12,
                    "tags": "a, b",
                    "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4"}},
                }
            ]
        }

    res = videos.pixabay_search(["climbing"], get=fake)
    assert len(res) == 2 and all("SECRETKEY123" in u for u in seen)
    assert "SECRETKEY123" not in repr(res)
    assert res[0]["mp4"].endswith(".mp4") and "нельзя" in res[0]["license"]
    monkeypatch.setattr(videos, "pixabay_get", fake)
    assert videos.main(["search", "--pixabay", "--scenario", "climbing"]) == 0
    assert "SECRETKEY123" not in capsys.readouterr().out


def test_pixabay_no_key(monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        videos.pixabay_search(["climbing"])


def test_pixabay_http_error_no_key_leak(monkeypatch):
    import io
    import urllib.error

    seen = {}

    def boom(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b"[ERROR 400] Invalid key")
        )

    monkeypatch.setenv("PIXABAY_API_KEY", "SECRETKEY123")
    monkeypatch.setattr(videos.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit) as ei:
        videos.main(["search", "--pixabay", "--scenario", "climbing"])
    msg = str(ei.value)
    assert "400" in msg and "Invalid key" in msg and "SECRETKEY123" not in msg
    assert seen["ua"].startswith("Mozilla/5.0")
