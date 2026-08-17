"""Ağırlık sayfa-önbelleği nöbetçisi — tespit, eşik, iyileşme."""

from __future__ import annotations

import pytest

from dortgoz.config import settings
from dortgoz.services import weight_guard as wg


@pytest.fixture
def guard():
    return wg.WeightGuard()


def test_turkish_output_is_clean(guard):
    assert guard.record("Sahnede iki kişi görülüyor; şüpheli hareket yok.") is False
    assert guard.total_hits == 0
    assert guard.drain_alerts() == []


@pytest.mark.parametrize("leak", ["或用 bir hareket", "kapı 入門 açık", "テスト"])
def test_cjk_leak_detected_and_alert_queued(guard, leak):
    assert guard.record(leak) is True
    alerts = guard.drain_alerts()
    assert len(alerts) == 1 and "CJK" in alerts[0]
    assert guard.drain_alerts() == []          # tek seferlik kuyruk


def test_single_hit_does_not_demand_heal_two_do(guard):
    guard.record("或 tek isabet")
    assert guard.needs_heal is False           # örnekleme gürültüsü tamponu
    guard.record("temiz çıktı")
    guard.record("用 ikinci isabet")
    assert guard.needs_heal is True


def test_hits_age_out_of_window(guard):
    guard.record("或 isabet")
    for _ in range(wg.WINDOW):
        guard.record("temiz")
    assert guard.needs_heal is False


@pytest.mark.asyncio
async def test_heal_unloads_and_drops_pages(guard, tmp_path, monkeypatch):
    gguf = tmp_path / "model.gguf"
    gguf.write_bytes(b"x" * 4096)
    monkeypatch.setattr(settings, "gguf_paths", str(gguf))

    calls = {}

    class FakeClient:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            calls["unload"] = url

    dropped = []
    monkeypatch.setattr(wg.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(wg.os, "POSIX_FADV_DONTNEED", 4, raising=False)
    monkeypatch.setattr(
        wg.os,
        "posix_fadvise",
        lambda fd, off, ln, advice: dropped.append(advice),
        raising=False,
    )

    guard.record("或")
    guard.record("用")
    assert guard.needs_heal
    await guard.heal()

    assert calls["unload"].endswith("/unload")
    assert dropped == [wg.os.POSIX_FADV_DONTNEED]
    assert guard.needs_heal is False           # pencere sıfırlandı
    assert guard.heals == 1
