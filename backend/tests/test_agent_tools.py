"""Ajan araç katmanı: şema kuralları + dispatcher davranışı (LLM'siz, deterministik)."""

import asyncio

import pytest

from dortgoz import session
from dortgoz.agent import tools
from dortgoz.agent.memory import Incident
from dortgoz.events import WindowEvent, WindowReport


class FakeManager:
    """ConnectionManager yerine geçen olay toplayıcı."""

    def __init__(self) -> None:
        self.events = []

    async def broadcast(self, event) -> None:
        self.events.append(event.payload)

    def payloads(self, type_: str):
        return [p for p in self.events if p.type == type_]


@pytest.fixture()
def ctx():
    c = session.start("test-run", "clip.mp4")
    c.duration = 120.0
    c.reports.append(WindowReport(
        window_start=30.0, window_end=60.0, anomaly_type="kavga",
        summary="İki kişi tartışıyor.",
        events=[WindowEvent(t=42.0, desc="Yumruk atıldı", severity_hint="yuksek")],
        uncertainties=["yüzler seçilemiyor"],
    ))
    inc = Incident(incident_id="abc123", title="Kavga",
                   first_seen=42.0, last_seen=55.0, risk="yuksek")
    c.ledger.incidents[inc.incident_id] = inc
    yield c
    session.clear()


# ---- şema kuralları (2026-08-03 araç çağırma ölçümleri) ----

def test_tool_schemas_are_strict():
    assert tools.TOOLS, "araç listesi boş olmamalı"
    for t in tools.TOOLS:
        fn = t["function"]
        params = fn["parameters"]
        assert fn["strict"] is True
        assert params["additionalProperties"] is False
        # strict mod: TÜM alanlar required
        assert sorted(params["required"]) == sorted(params["properties"])
        # Qwen'de array<object> parametre yasak (llama.cpp #21771)
        for prop in params["properties"].values():
            assert not (prop.get("type") == "array"
                        and prop.get("items", {}).get("type") == "object"), fn["name"]
        # açıklanabilirlik: her araç gerekçe ister
        assert "gerekce" in params["properties"], fn["name"]


def test_every_tool_call_emits_toolcall_event(ctx):
    m = FakeManager()
    asyncio.run(tools.execute("videoya_git", {"t": 42.0, "gerekce": "göster"}, m))
    calls = m.payloads("tool_call")
    assert len(calls) == 1 and calls[0].tool == "videoya_git"
    assert calls[0].rationale == "göster"
    # arayüz komutu da gitmiş olmalı
    ui = m.payloads("ui_command")
    assert ui and ui[0].action == "seek_video" and ui[0].args["t"] == 42.0


def test_pencere_sorgula_returns_report(ctx):
    m = FakeManager()
    out = asyncio.run(tools.execute("pencere_sorgula", {"t": 45, "gerekce": "?"}, m))
    assert "Yumruk atıldı" in out and "yuksek" in out
    assert "belirsiz" in out


def test_pencere_sorgula_out_of_range(ctx):
    m = FakeManager()
    out = asyncio.run(tools.execute("pencere_sorgula", {"t": 200, "gerekce": "?"}, m))
    assert "kapsayan pencere yok" in out


def test_olayi_vurgula_unknown_id_lists_known(ctx):
    m = FakeManager()
    out = asyncio.run(tools.execute("olayi_vurgula",
                                    {"incident_id": "yok", "gerekce": "?"}, m))
    assert "HATA" in out and "abc123" in out
    assert not m.payloads("ui_command")   # hatalı kimlikte arayüz komutu gitmez


def test_critical_actuator_requires_approval(ctx):
    m = FakeManager()
    out = asyncio.run(tools.execute("aktuator_calistir", {
        "actuator": "alarm_ver", "incident_id": "abc123",
        "gerekce": "yüksek riskli kavga"}, m))
    reqs = m.payloads("actuator_request")
    assert len(reqs) == 1 and reqs[0].actuator == "alarm_ver"
    assert reqs[0].incident_id == "abc123"
    assert "onay" in out
    # kritik OLMAYAN aktüatör doğrudan çalışır (mock), onay istemez
    out2 = asyncio.run(tools.execute("aktuator_calistir", {
        "actuator": "kayit_baslat", "incident_id": "", "gerekce": "kayıt"}, m))
    assert "mock" in out2 and len(m.payloads("actuator_request")) == 1


def test_tool_errors_return_text_not_raise(ctx):
    m = FakeManager()
    out = asyncio.run(tools.execute("bilinmeyen_arac", {"gerekce": "?"}, m))
    assert out.startswith("HATA")
    # bozuk argüman JSON'ı da patlatmaz
    assert tools.parse_args("{bozuk json") == {}
    assert tools.parse_args('"liste degil"') == {}


def test_no_session_tools_fail_gracefully():
    session.clear()
    m = FakeManager()
    out = asyncio.run(tools.execute("pencere_sorgula", {"t": 5, "gerekce": "?"}, m))
    assert "HATA" in out


def test_history_resets_on_new_run():
    from dortgoz.agent import graph
    graph._history.append({"role": "user", "content": "eski"})
    session.start("yeni-run", "v.mp4")
    assert graph._history == []
    session.clear()
