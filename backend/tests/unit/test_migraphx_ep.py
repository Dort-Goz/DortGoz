from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from dortgoz.config import settings
from dortgoz.pipeline import migraphx_ep


@pytest.fixture(autouse=True)
def _clean():
    migraphx_ep.reset_cache()
    yield
    migraphx_ep.reset_cache()


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "model.onnx"
    source.write_bytes(b"fixture-weights")
    return source


def test_disabled_when_directory_is_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "migraphx_dir", "")

    assert migraphx_ep.load("dfine", _source(tmp_path)) is None


def test_stale_artifact_source_is_rejected(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "gpu"
    artifacts.mkdir()
    (artifacts / "dfine.mxr").write_bytes(b"compiled")
    (artifacts / "manifest.json").write_text(
        json.dumps({"dfine": {"source_sha256": "0" * 64}}), encoding="utf-8"
    )
    monkeypatch.setattr(settings, "migraphx_dir", str(artifacts))

    assert migraphx_ep.load("dfine", _source(tmp_path)) is None


def test_missing_artifact_falls_back(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "gpu"
    artifacts.mkdir()
    monkeypatch.setattr(settings, "migraphx_dir", str(artifacts))

    assert migraphx_ep.load("siglip", _source(tmp_path)) is None


class _Fixed:
    input_shape = (4, 3, 2, 2)
    batch = 4

    def __init__(self) -> None:
        self.calls: list[np.ndarray] = []

    def _run_fixed(self, block):
        self.calls.append(block.copy())
        return [block.reshape(block.shape[0], -1)[:, :1].copy()]


def test_partial_batches_are_padded_and_truncated() -> None:
    session = _Fixed()
    data = np.arange(6 * 3 * 2 * 2, dtype=np.float32).reshape(6, 3, 2, 2)

    out = migraphx_ep.GpuSession.run(session, None, {"pixel_values": data})

    assert len(session.calls) == 2
    assert all(block.shape[0] == 4 for block in session.calls)
    assert out[0].shape[0] == 6
    assert float(session.calls[1][2].sum()) == 0.0
