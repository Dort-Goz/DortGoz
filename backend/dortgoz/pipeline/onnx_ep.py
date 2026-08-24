from __future__ import annotations

import logging

from ..config import settings

log = logging.getLogger(__name__)

CPU = "CPUExecutionProvider"
CUDA = "CUDAExecutionProvider"
_warned: set[str] = set()
_preloaded = False


def _preload_cuda_libs() -> None:

    global _preloaded
    if _preloaded:
        return
    _preloaded = True
    import onnxruntime as ort

    if not hasattr(ort, "preload_dlls"):
        return
    try:
        ort.preload_dlls()
    except Exception as exc:
        log.warning("CUDA kitaplıkları önceden yüklenemedi: %s", exc)


def _requested() -> list[str]:
    explicit = [p.strip() for p in settings.onnx_providers.split(",") if p.strip()]
    if explicit:
        return explicit
    device = settings.onnx_device.strip().casefold()
    if device in {"gpu", "cuda"}:
        return [CUDA]
    if device == "auto":
        return [CUDA]
    return []


def providers() -> list[str]:

    want = _requested()
    if not want:
        return [CPU]

    if CUDA in want:
        _preload_cuda_libs()

    import onnxruntime as ort

    available = set(ort.get_available_providers())
    usable = [p for p in want if p in available]
    quiet = settings.onnx_device.strip().casefold() == "auto"
    for p in (p for p in want if p not in available):
        if p not in _warned:
            _warned.add(p)
            level = log.info if quiet else log.warning
            level("onnx sağlayıcı yok, atlandı: %s (mevcut: %s)",
                  p, sorted(available))
    if CPU not in usable:
        usable.append(CPU)
    return usable


def session_options():

    import onnxruntime as ort

    opts = ort.SessionOptions()
    n = settings.onnx_intra_threads
    if n > 0:
        opts.intra_op_num_threads = n
        opts.inter_op_num_threads = 1
    return opts
