"""Koşu bağlamı — sohbetin analizden sonra devam edebilmesinin dayanağı."""

import pytest

from dortgoz import session
from dortgoz.agent.graph import build_system_prompt
from dortgoz.events import WindowEvent, WindowReport


@pytest.fixture(autouse=True)
def clean_session():
    session.clear()
    yield
    session.clear()


def analysed_run() -> session.RunContext:
    ctx = session.start("test-1", "Fighting023_x264.mp4")
    ctx.duration = 64.0
    report = WindowReport(
        window_start=30, window_end=60, anomaly_type="kavga",
        summary="Girişte iki kişi arasında fiziksel kavga.",
        events=[
            WindowEvent(t=11, desc="Bir kişi içeri girdi", severity_hint="dusuk"),
            WindowEvent(t=48, desc="İki kişi boğuşmaya başladı", severity_hint="yuksek"),
        ],
        uncertainties=["Kavganın nedeni görüntüden anlaşılmıyor"],
    )
    ctx.reports.append(report)
    ctx.ledger.ingest(report)
    ctx.finished = True
    return ctx


def test_verdict_states_the_anomaly_type_and_time():
    ctx = analysed_run()
    verdict = ctx.verdict()
    assert "kavga" in verdict
    assert "00:48" in verdict           # operatör videoda bulabilsin
    assert "yüksek" in verdict          # operatöre dönük metin Türkçe yazımla


def test_verdict_when_nothing_found():
    ctx = session.start("test-2", "Normal_Videos_885_x264.mp4")
    assert "tespit edilmedi" in ctx.verdict()


def test_briefing_carries_decision_events_and_uncertainty():
    brief = analysed_run().briefing()
    assert "Fighting023_x264.mp4" in brief
    assert "kavga" in brief                        # sınıflandırma kararı
    assert "İki kişi boğuşmaya başladı" in brief   # ciddi gözlem
    assert "Bir kişi içeri girdi" in brief         # dusuk gözlem de bağlamda
    assert "belirsiz" in brief.lower()             # belirsizlik aktarılıyor
    assert "00:30–01:00" in brief                  # pencere aralığı okunur biçimde


def test_system_prompt_includes_run_context_after_analysis():
    analysed_run()
    prompt = build_system_prompt()
    assert "Fighting023_x264.mp4" in prompt
    assert "UYDURMA" in prompt                     # halüsinasyon kuralı
    assert "kavga" in prompt


def test_system_prompt_without_a_run_tells_operator_to_start_one():
    prompt = build_system_prompt()
    assert "Henüz çözümlenmiş bir kayıt yok" in prompt
    assert "Fighting" not in prompt


def test_context_survives_after_run_finishes():
    """Asıl mesele: analiz bittikten SONRA da bağlam duruyor mu?"""
    ctx = analysed_run()
    assert ctx.finished
    assert session.current() is ctx
    assert len(session.current().incidents) == 1


def test_new_run_replaces_previous_context():
    analysed_run()
    session.start("test-3", "Explosion019_x264.mp4")
    assert session.current().video == "Explosion019_x264.mp4"
    assert session.current().incidents == []
