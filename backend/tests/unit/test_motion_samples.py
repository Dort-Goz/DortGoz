"""Vektörleştirilmiş hareket profili, eski saf Python sürümüyle birebir aynıdır."""
from __future__ import annotations

import random

from dortgoz.pipeline.ingest import (
    BG_ALPHA,
    PIXEL_TAU,
    _FRAME_BYTES,
    _motion_samples,
)


def _referans(raw: bytes, base_fps: float):
    """Değiştirilmeden önceki uygulama. Karşılaştırma tabanıdır."""
    profile = []
    prev = None
    background = None
    for idx in range(0, len(raw) - _FRAME_BYTES + 1, _FRAME_BYTES):
        frame = raw[idx:idx + _FRAME_BYTES]
        t = (idx // _FRAME_BYTES) / base_fps
        if prev is None:
            mad = changed = 0.0
            background = [float(p) for p in frame]
        else:
            total = 0
            hits = 0
            for a, b in zip(frame, prev):
                d = a - b if a > b else b - a
                total += d
                if d > PIXEL_TAU:
                    hits += 1
            mad = total / (_FRAME_BYTES * 255)
            changed = hits / _FRAME_BYTES
        assert background is not None
        fg = sum(1 for a, b in zip(frame, background)
                 if abs(a - b) > PIXEL_TAU) / _FRAME_BYTES
        background = [b + BG_ALPHA * (a - b) for a, b in zip(frame, background)]
        profile.append((t, changed, fg, mad, frame))
        prev = frame
    return profile


def _rastgele(kare: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(kare * _FRAME_BYTES))


def test_motion_samples_saf_python_ile_ayni() -> None:
    raw = _rastgele(12, seed=7)
    yeni = _motion_samples(raw, 1.0)
    eski = _referans(raw, 1.0)
    assert len(yeni) == len(eski) == 12
    for s, (t, changed, fg, mad, grid) in zip(yeni, eski):
        assert s.t == t
        assert s.grid == grid
        assert abs(s.changed - changed) < 1e-12
        assert abs(s.mad - mad) < 1e-12
        # fg arka plan birikimine bağlıdır; kayan nokta sırası ufak fark verebilir.
        assert abs(s.fg - fg) < 1e-9


def test_motion_samples_gercekci_sahnede_de_ayni() -> None:
    """Rastgele gürültü yerine yavaş değişen sahne: arka plan yolu zorlanır."""
    taban = bytearray(_rastgele(1, seed=3))
    parcalar = []
    for adim in range(10):
        kare = bytearray(taban)
        for i in range(0, _FRAME_BYTES, 97):
            kare[i] = (kare[i] + adim * 5) % 256
        parcalar.append(bytes(kare))
    raw = b"".join(parcalar)
    for s, (t, changed, fg, mad, grid) in zip(_motion_samples(raw, 2.0),
                                              _referans(raw, 2.0)):
        assert s.t == t and s.grid == grid
        assert abs(s.changed - changed) < 1e-12
        assert abs(s.mad - mad) < 1e-12
        assert abs(s.fg - fg) < 1e-9


def test_bos_girdi_bos_profil() -> None:
    assert _motion_samples(b"", 1.0) == []
    assert _motion_samples(b"\x00" * (_FRAME_BYTES - 1), 1.0) == []
