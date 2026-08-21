from __future__ import annotations

from dortgoz.pipeline import onnx_ep


def test_default_is_cpu_only(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers", "")

    assert onnx_ep.providers() == ["CPUExecutionProvider"]


def test_requested_provider_is_used_when_available(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers",
                        "CUDAExecutionProvider")
    monkeypatch.setattr(
        "onnxruntime.get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])

    assert onnx_ep.providers() == ["CUDAExecutionProvider",
                                   "CPUExecutionProvider"]


def test_unavailable_provider_falls_back_to_cpu_instead_of_crashing(monkeypatch):
    """Masaustu AMD: CUDA istense bile calismali."""
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers",
                        "CUDAExecutionProvider")
    monkeypatch.setattr("onnxruntime.get_available_providers",
                        lambda: ["CPUExecutionProvider"])
    onnx_ep._warned.clear()

    assert onnx_ep.providers() == ["CPUExecutionProvider"]


def test_cpu_is_always_appended_as_last_resort(monkeypatch):
    monkeypatch.setattr(onnx_ep.settings, "onnx_providers",
                        "TensorrtExecutionProvider,CUDAExecutionProvider")
    monkeypatch.setattr(
        "onnxruntime.get_available_providers",
        lambda: ["TensorrtExecutionProvider", "CUDAExecutionProvider",
                 "CPUExecutionProvider"])

    assert onnx_ep.providers()[-1] == "CPUExecutionProvider"
