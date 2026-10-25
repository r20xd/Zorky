from export.bench import benchmark, main, table_row


def test_benchmark_reports_fps_and_latency():
    calls = []
    result = benchmark(lambda: calls.append(1), warmup=2, iters=5)
    assert len(calls) == 7
    assert result["fps"] > 0
    assert 0 <= result["p50_ms"] <= result["p95_ms"]


def test_table_row_format():
    row = table_row("m.onnx", "CPU", 640, {"fps": 12.34, "p50_ms": 80.0, "p95_ms": 95.0})
    assert row == "| m.onnx | CPU | 640 | 12.3 | 80.0 | 95.0 |"


def test_cli_run_with_fake_model(monkeypatch, capsys):
    import export.bench as bench

    monkeypatch.setattr(bench, "make_cpu_infer", lambda path, imgsz: lambda: None)
    main(["run", "dir/fake.onnx", "--warmup", "1", "--iters", "3"])
    out = capsys.readouterr().out
    assert out.startswith("| fake.onnx | CPU (onnxruntime) | 640 |")


class FakeSession:
    class _In:
        name = "images"

    def get_inputs(self):
        return [self._In()]

    def run(self, _outputs, _feed):
        import numpy as np

        return [np.zeros((1, 84, 8400), dtype=np.float32)]


def test_bench_all_fake_engines_and_linq_skipped(tmp_path):
    import json

    from export.bench import bench_all

    out = tmp_path / "b.json"
    lines = bench_all(
        "m.onnx", out=str(out), session_factory=lambda path, providers: FakeSession(), frames=3
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    engines = {r["engine"]: r for r in data["results"]}
    assert [r["streams"] for r in engines["cpu"]["runs"]] == [1, 2, 4]
    run = engines["gpu"]["runs"][2]
    assert run["fps"] > 0 and run["latency"]["p50_ms"] >= run["infer"]["p50_ms"] >= 0
    assert {"pre", "post"} <= run.keys()
    assert engines["linq"]["skipped"] and engines["linq"]["runs"] == []
    assert lines[0].startswith("| движок") and len(lines) == 1 + 3 + 3 + 1
    assert "пропущен" in lines[-1]
