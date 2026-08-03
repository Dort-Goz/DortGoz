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

    c = led.ingest(report(60, (65, "Ortam sakin", "dusuk")))
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
    """Araya olaysız pencere girerse ikinci olay AYRI kayıt olmalı."""
    led = Ledger()
    first = led.ingest(report(0, (10, "Kavga", "orta")))[0]
    led.ingest(report(30))                     # olaysız pencere → kapanış
    second = led.ingest(report(60, (70, "Hırsızlık", "yuksek")))[0]
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
