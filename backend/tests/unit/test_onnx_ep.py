from __future__ import annotations

import pytest

from dortgoz.pipeline import onnx_ep

CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_device", "cpu")
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers", "")
    monkeypatch.setattr(onnx_ep, "_preloaded", True)
    onnx_ep._warned.clear()


def _available(monkeypatch, providers: list[str]) -> None:
    monkeypatch.setattr("onnxruntime.get_available_providers", lambda: providers)


def test_default_is_cpu(monkeypatch):
    assert onnx_ep.providers() == [CPU]


@pytest.mark.parametrize("value", ["gpu", "GPU", "cuda", " gpu "])
def test_device_gpu_selects_cuda(monkeypatch, value):
    monkeypatch.setattr(onnx_ep.settings, "onnx_device", value)
    _available(monkeypatch, [CUDA, CPU])

    assert onnx_ep.providers() == [CUDA, CPU]


def test_gpu_falls_back_to_cpu_when_cuda_missing(monkeypatch):

    monkeypatch.setattr(onnx_ep.settings, "onnx_device", "gpu")
    _available(monkeypatch, [CPU])

    assert onnx_ep.providers() == [CPU]


def test_auto_uses_gpu_when_present(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_device", "auto")
    _available(monkeypatch, [CUDA, CPU])

    assert onnx_ep.providers() == [CUDA, CPU]


def test_auto_is_silent_when_gpu_absent(monkeypatch, caplog):
    monkeypatch.setattr(onnx_ep.settings, "onnx_device", "auto")
    _available(monkeypatch, [CPU])

    with caplog.at_level("WARNING"):
        assert onnx_ep.providers() == [CPU]

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_explicit_providers_override_device(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_device", "cpu")
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers",
                        "TensorrtExecutionProvider")
    _available(monkeypatch, ["TensorrtExecutionProvider", CPU])

    assert onnx_ep.providers() == ["TensorrtExecutionProvider", CPU]


def test_cpu_is_always_the_last_resort(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers", f"{CUDA}")
    _available(monkeypatch, [CUDA, CPU])

    assert onnx_ep.providers()[-1] == CPU


def test_session_options_apply_configured_thread_limit(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_intra_threads", 4)

    options = onnx_ep.session_options()

    assert options.intra_op_num_threads == 4
    assert options.inter_op_num_threads == 1


def test_session_options_keep_runtime_defaults_when_limit_is_zero(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_intra_threads", 0)

    options = onnx_ep.session_options()

    assert options.intra_op_num_threads == 0
    assert options.inter_op_num_threads == 0
