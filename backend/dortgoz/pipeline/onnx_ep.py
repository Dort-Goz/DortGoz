from __future__ import annotations

import logging

from ..config import settings

log = logging.getLogger(__name__)

CPU = "CPUExecutionProvider"
_warned: set[str] = set()


def providers() -> list[str]:
    """İstenen yürütme sağlayıcılarını, yoksa CPU'ya düşerek döndürür.

    Varsayılan CPU'dur: masaüstü AMD'dir, CUDA yalnız dizüstünde vardır.
    Taşınabilirlik için asla sert CUDA bağımlılığı kurma.
    """
    want = [p.strip() for p in settings.onnx_providers.split(",") if p.strip()]
    if not want:
        return [CPU]

    import onnxruntime as ort

    available = set(ort.get_available_providers())
    usable = [p for p in want if p in available]
    missing = [p for p in want if p not in available]
    for p in missing:
        if p not in _warned:
            _warned.add(p)
            log.warning("onnx sağlayıcı yok, atlandı: %s (mevcut: %s)",
                        p, sorted(available))
    if CPU not in usable:
        usable.append(CPU)
    return usable
