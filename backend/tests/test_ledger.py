"""Olay defteri — yaşam döngüsü ve birleştirme kuralları."""

from dortgoz.agent.memory import Ledger
from dortgoz.events import WindowEvent, WindowReport


def report(start: float, *events: tuple[float, str, str], summary: str = "özet") -> WindowReport:
    return WindowReport(
        window_start=start, window_end=start + 30, summary=summary,
        events=[WindowEvent(t=t, desc=d, severity_hint=s) for t, d, s in events],
    )


def test_dusuk_events_never_create_incidents():
    """Ölçüm bulgusu: normal kliplerin ürettiği olayların tamamı `dusuk` idi.

    Bunlar sahne betimlemesi; alarm üretirlerse yanlış alarm oranı patlar.
    """
    led = Ledger()
    updates = led.ingest(report(0, (3, "Bir kişi kapıdan girdi", "dusuk"),
                                   (8, "Araç park etti", "dusuk")))
    assert updates == []
    assert led.incidents == {}


def test_incident_opens_then_develops_then_closes():
    led = Ledger()
    a = led.ingest(report(0, (10, "İki kişi itişiyor", "orta")))
    assert [u.phase for u in a] == ["basladi"]
    inc_id = a[0].incident_id

    b = led.ingest(report(30, (35, "Kavga büyüdü", "yuksek")))
    assert [u.phase for u in b] == ["gelisiyor"]
    assert b[0].incident_id == inc_id          # aynı olayın devamı

    # 2026-08-05: tek sessiz pencere artık KAPATMAZ (grace) — olayın ortasındaki
    # sınırdaki bir pencere uzun olayları ikiye bölüyordu. Kapanış için gerçek
    # bir boşluk (grace+1 sessiz pencere) gerekir.
    assert led.ingest(report(60, (65, "Ortam sakin", "dusuk"))) == []
    c = led.ingest(report(90, (95, "Ortam sakin", "dusuk")))
    assert [u.phase for u in c] == ["sonuclandi"]
    assert c[0].incident_id == inc_id


def test_risk_escalates_but_never_downgrades():
    """Risk yalnız yukarı revize edilir — bir kez 'yuksek' görülen olay geri düşmez."""
    led = Ledger()
    led.ingest(report(0, (5, "Şüpheli hareket", "orta")))
    led.ingest(report(30, (35, "Silah görüldü", "kritik")))
    up = led.ingest(report(60, (65, "Hafif hareket", "orta")))
    assert up[0].risk == "kritik"
    assert "Silah görüldü" in up[0].title       # başlık en ciddi olayı yansıtır


def test_gap_splits_into_two_incidents():
    """GERÇEK boşluk (grace'i aşan sessizlik) ikinci olayı AYRI kayıt yapmalı.

    2026-08-05: tolerans eklendikten sonra tek sessiz pencere bölmez; ayrı olay
    için grace+1 sessiz pencere gerekir (bkz. test_ledger_grace_* testleri).
    """
    led = Ledger()
    first = led.ingest(report(0, (10, "Kavga", "orta")))[0]
    led.ingest(report(30))                     # 1. sessiz — tolere edilir
    led.ingest(report(60))                     # 2. sessiz → kapanış
    second = led.ingest(report(90, (100, "Hırsızlık", "yuksek")))[0]
    assert second.incident_id != first.incident_id
    assert second.phase == "basladi"
    assert len(led.incidents) == 2


def test_finalize_closes_incident_open_at_video_end():
    led = Ledger()
    led.ingest(report(0, (10, "Yangın başladı", "yuksek")))
    closing = led.finalize()
    assert [u.phase for u in closing] == ["sonuclandi"]
    assert led.finalize() == []                # ikinci kez çağrılınca boş


def test_empty_report_without_open_incident_is_noop():
    led = Ledger()
    assert led.ingest(report(0)) == []
    assert led.finalize() == []


def test_title_is_truncated_first_sentence():
    led = Ledger()
    long = "Bir kişi yerde hareketsiz yatıyor" + " ve çevrede kimse yok" * 5
    up = led.ingest(report(0, (5, long + ". İkinci cümle.", "yuksek")))[0]
    assert len(up.title) <= 70
    assert up.title.endswith("…")
    assert "İkinci cümle" not in up.title


# ---- insan incelemesi bayrağı (needs_review) ----

def _serious_report(start=0.0, cls="kavga", uncertainties=None):
    from dortgoz.events import WindowEvent, WindowReport
    return WindowReport(
        window_start=start, window_end=start + 30, anomaly_type=cls,
        summary="özet",
        events=[WindowEvent(t=start + 5, desc="ciddi olay", severity_hint="orta")],
        uncertainties=uncertainties or [],
    )


def test_review_flag_from_uncertain_source():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    ups = led.ingest(_serious_report(), uncertain="tırmandırmayla kurtarıldı")
    assert ups[0].needs_review is True
    assert "tırmandırma" in ups[0].review_reason


def test_review_flag_from_unknown_class_and_uncertainties():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    ups = led.ingest(_serious_report(cls="normal", uncertainties=["yüz seçilemiyor"]))
    # ciddi olay + normal sınıfı → bilinmeyen'e düşer → bayrak
    assert ups[0].anomaly_type == "bilinmeyen"
    assert ups[0].needs_review is True


def test_confident_incident_not_flagged():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    ups = led.ingest(_serious_report())
    assert ups[0].needs_review is False and ups[0].review_reason == ""


def test_review_pass_clears_or_keeps_flag():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    iid = led.ingest(_serious_report(), uncertain="sınırda")[0].incident_id
    # bütünü gören geçiş emin → bayrak kalkar
    up = led.apply_review(iid, {"anomaly_type": "kavga", "risk": "yuksek",
                                "zirve": "Kavga zirvesi", "zirve_t": 6.0,
                                "baslangic": "b", "sonuc": "s", "belirsizlikler": []})
    assert up.needs_review is False
    # bütünde de belirsizse kalır
    up = led.apply_review(iid, {"anomaly_type": "kavga", "risk": "yuksek",
                                "zirve": "Kavga", "zirve_t": 6.0, "baslangic": "b",
                                "sonuc": "s", "belirsizlikler": ["kim başlattı belirsiz"]})
    assert up.needs_review is True and "2. geçiş" in up.review_reason
