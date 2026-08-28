from dortgoz.agent.memory import Ledger
from dortgoz.events import EventEvidenceRef, WindowEvent, WindowReport


def report(start: float, *events: tuple[float, str, str], summary: str = "özet") -> WindowReport:
    return WindowReport(
        window_start=start, window_end=start + 30, summary=summary,
        events=[WindowEvent(t=t, desc=d, severity_hint=s) for t, d, s in events],
    )


def test_dusuk_events_never_create_incidents():
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
    assert b[0].incident_id == inc_id

    assert led.ingest(report(60, (65, "Ortam sakin", "dusuk"))) == []
    c = led.ingest(report(90, (95, "Ortam sakin", "dusuk")))
    assert [u.phase for u in c] == ["sonuclandi"]
    assert c[0].incident_id == inc_id


def test_risk_escalates_but_never_downgrades():
    led = Ledger()
    led.ingest(report(0, (5, "Şüpheli hareket", "orta")))
    led.ingest(report(30, (35, "Silah görüldü", "kritik")))
    up = led.ingest(report(60, (65, "Hafif hareket", "orta")))
    assert up[0].risk == "kritik"
    assert "Silah görüldü" in up[0].title


def test_gap_splits_into_two_incidents():
    led = Ledger()
    first = led.ingest(report(0, (10, "Kavga", "orta")))[0]
    led.ingest(report(30))
    led.ingest(report(60))
    second = led.ingest(report(90, (100, "Hırsızlık", "yuksek")))[0]
    assert second.incident_id != first.incident_id
    assert second.phase == "basladi"
    assert len(led.incidents) == 2


def test_finalize_closes_incident_open_at_video_end():
    led = Ledger()
    led.ingest(report(0, (10, "Yangın başladı", "yuksek")))
    closing = led.finalize()
    assert [u.phase for u in closing] == ["sonuclandi"]
    assert led.finalize() == []


def test_incident_keeps_deduplicated_grounded_evidence_refs():
    led = Ledger()
    evidence = EventEvidenceRef(
        frame_id="f_001",
        timestamp=8.5,
        claim="Bir kişi diğer kişiyi kuvvetle itiyor.",
    )
    first = WindowReport(
        window_start=0,
        window_end=30,
        anomaly_type="kavga",
        summary="Fiziksel temas var.",
        events=[WindowEvent(
            t=8.5,
            desc="İtişme görülüyor.",
            severity_hint="orta",
            evidence=[evidence],
        )],
    )

    opened = led.ingest(first)[0]
    developed = led.ingest(first.model_copy(update={"window_start": 30, "window_end": 60}))[0]

    assert opened.evidence is None
    assert developed.evidence is None
    assert led.open_incident.evidence_refs == [evidence]
    assert led.open_incident.evidence_ts == [8.5]


def test_empty_report_without_open_incident_is_noop():
    led = Ledger()
    assert led.ingest(report(0)) == []
    assert led.finalize() == []


def test_title_keeps_the_whole_first_sentence():
    # Görüntüleme kesmesi arayüzdedir; başlık tam gelmeli ki ipucu işe yarasın.
    led = Ledger()
    long = "Bir kişi yerde hareketsiz yatıyor" + " ve çevrede kimse yok" * 5
    up = led.ingest(report(0, (5, long + ". İkinci cümle.", "yuksek")))[0]
    assert up.title == long
    assert not up.title.endswith("…")
    assert "İkinci cümle" not in up.title


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
    assert ups[0].anomaly_type == "bilinmeyen"
    assert ups[0].needs_review is True


def test_confident_incident_not_flagged():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    ups = led.ingest(_serious_report())
    assert ups[0].needs_review is False and ups[0].review_reason == ""


def test_review_pass_never_clears_sticky_flag():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    iid = led.ingest(_serious_report(), uncertain="sınırda")[0].incident_id
    up = led.apply_review(iid, {"anomaly_type": "kavga", "risk": "yuksek",
                                "zirve": "Kavga zirvesi", "zirve_t": 6.0,
                                "baslangic": "b", "sonuc": "s", "belirsizlikler": []})
    assert up.needs_review is True
    assert up.risk == "orta"
    up = led.apply_review(iid, {"anomaly_type": "kavga", "risk": "yuksek",
                                "zirve": "Kavga", "zirve_t": 6.0, "baslangic": "b",
                                "sonuc": "s", "belirsizlikler": ["kim başlattı belirsiz"]})
    assert up.needs_review is True and "sınırda" in up.review_reason


def test_event_window_never_runs_past_video_duration():
    led = Ledger(duration=9.4)
    evidence = EventEvidenceRef(frame_id="f_009", timestamp=9.0, claim="Kişi yere düşüyor.")
    opened = led.ingest(WindowReport(
        window_start=0, window_end=30, anomaly_type="kavga", summary="Kavga var.",
        events=[WindowEvent(t=9.0, desc="Düşme.", severity_hint="orta", evidence=[evidence])],
    ))[0]

    assert opened.olay_bitis == 9.4

    reviewed = led.apply_review(opened.incident_id, {
        "anomaly_type": "kavga", "zirve": "Kavga zirvesi", "zirve_t": 9.0,
        "baslangic": "b", "sonuc": "s", "belirsizlikler": [],
        "baslangic_t": 8.0, "bitis_t": 12.0,
    })
    assert reviewed.olay_bitis <= 9.4


def test_title_truncation_cuts_on_a_word_boundary():
    # 300 karakter kayıt sağlığı sınırıdır; o sınırda da kelime ortasından kesmez.
    led = Ledger()
    long_title = "Kasaya gelen kişi masanın arkasındaki çalışanla konuşuyor " * 8
    opened = led.ingest(report(0, (5, long_title, "orta")))[0]

    assert len(opened.title) <= 300
    assert opened.title.endswith("…")
    assert not opened.title.rstrip("…").endswith(" ")
    assert long_title.startswith(opened.title.rstrip("… "))


def test_normal_second_pass_clears_the_first_pass_review_flag():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    iid = led.ingest(_serious_report(), uncertain="sınırda")[0].incident_id

    up = led.apply_review(iid, {"anomaly_type": "normal", "risk": "orta",
                                "zirve": "Kamera açısı değişti, sahne oturdu",
                                "zirve_t": 6.0, "baslangic": "b", "sonuc": "s",
                                "belirsizlikler": []})

    assert up.needs_review is False
    assert up.review_reason == ""


def test_normal_second_pass_keeps_its_own_uncertainty():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    iid = led.ingest(_serious_report(), uncertain="sınırda")[0].incident_id

    up = led.apply_review(iid, {"anomaly_type": "normal", "risk": "orta",
                                "zirve": "Sahne belirsiz kaldı", "zirve_t": 6.0,
                                "baslangic": "b", "sonuc": "s",
                                "belirsizlikler": ["görüntü kalitesi düşük"]})

    assert up.needs_review is True
    assert "sınırda" in up.review_reason


def test_review_to_normal_drops_alarm_risk():
    from dortgoz.agent.memory import Ledger
    led = Ledger()
    iid = led.ingest(_serious_report())[0].incident_id
    up = led.apply_review(iid, {"anomaly_type": "normal", "risk": "orta",
                                "zirve": "Trafik olağan akıyor", "zirve_t": 6.0,
                                "baslangic": "b", "sonuc": "s", "belirsizlikler": []})
    assert up.risk == "dusuk"
