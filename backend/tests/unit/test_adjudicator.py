from pathlib import Path

import pytest

from dortgoz.agent.memory import Incident, Ledger
from dortgoz.config import settings
from dortgoz.pipeline import interpret, runner


class _FakeRec:
    feed = "TEST"

    def __init__(self) -> None:
        self.emitted = []

    async def emit(self, payload) -> None:
        self.emitted.append(payload)


def _ledger_with(anomaly_type: str) -> Ledger:
    ledger = Ledger()
    ledger.incidents["abc"] = Incident(
        incident_id="abc", title="test olayı",
        first_seen=10.0, last_seen=20.0, phase="sonuclandi",
        anomaly_type=anomaly_type)
    return ledger


@pytest.mark.asyncio
async def test_hakem_confusable_sinifi_duzeltir(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adjudicate_confusable", "hirsizlik,kavga")

    async def fake_adjudicate(_video, _span, _keyframes, **_kwargs):
        return "vehicle_collision", 0.95

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("hirsizlik")
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "arac_kazasi"
    steps = [e for e in rec.emitted if getattr(e, "node", "") == "hakem"]
    assert steps and "hirsizlik → arac_kazasi" in steps[-1].detail


@pytest.mark.asyncio
async def test_hakem_kume_disinda_calismaz(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adjudicate_confusable", "hirsizlik,kavga")

    async def fake_adjudicate(*_a, **_k):
        raise AssertionError("hakem küme dışında çağrıldı")

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("yangin")
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "yangin"
    assert not rec.emitted


@pytest.mark.asyncio
async def test_hakem_ayni_sinifta_dokunmaz(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adjudicate_confusable", "hirsizlik,kavga")

    async def fake_adjudicate(_video, _span, _keyframes, **_kwargs):
        return "possible_theft", 0.95

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("hirsizlik")
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "hirsizlik"
    steps = [e for e in rec.emitted if getattr(e, "node", "") == "hakem"]
    assert steps and "korundu" in steps[-1].detail


@pytest.mark.asyncio
async def test_hakem_bos_anahtar_kapali(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adjudicate_confusable", "")

    async def fake_adjudicate(*_a, **_k):
        raise AssertionError("hakem kapalıyken çağrıldı")

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("hirsizlik")
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "hirsizlik"
    assert not rec.emitted


@pytest.mark.asyncio
async def test_hakem_dusuk_guvende_degistirmez(monkeypatch) -> None:
    monkeypatch.setattr(settings, "adjudicate_confusable", "hirsizlik,kavga")
    monkeypatch.setattr(settings, "adjudicate_min_conf", 0.60)

    async def fake_adjudicate(_video, _span, _keyframes, **_kwargs):
        return "vehicle_collision", 0.35

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("hirsizlik")
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "hirsizlik"
    steps = [e for e in rec.emitted if getattr(e, "node", "") == "hakem"]
    assert steps and "düşük güven" in steps[-1].detail


@pytest.mark.asyncio
async def test_hakem_olay_degilse_geri_ceker(monkeypatch) -> None:
    """Hakem bir anomali sınıfı seçmek zorunda değildir: olay yoksa geri çeker."""
    monkeypatch.setattr(settings, "adjudicate_confusable", "hirsizlik,bilinmeyen")
    monkeypatch.setattr(settings, "adjudicate_min_conf", 0.60)

    async def fake_adjudicate(_video, _span, _keyframes, **_kwargs):
        return "normal", 0.88

    monkeypatch.setattr(interpret, "adjudicate_category", fake_adjudicate)
    ledger = _ledger_with("hirsizlik")
    ledger.incidents["abc"].risk = "orta"
    rec = _FakeRec()
    await runner._adjudicate_if_confusable(
        rec, ledger, "abc", Path("yok.mp4"), [], "", None, 30.0)
    assert ledger.incidents["abc"].anomaly_type == "normal"
    assert ledger.incidents["abc"].risk == "dusuk"


def test_hakem_null_siniflari_sunar() -> None:
    assert "normal" in interpret.ADJUDICATE_TYPES
    assert "unknown_anomaly" in interpret.ADJUDICATE_TYPES
