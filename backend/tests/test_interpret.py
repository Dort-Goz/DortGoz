import json

from dortgoz.pipeline.interpret import (
    _to_report,
    repair_truncated_json,
    report_schema,
    review_schema,
    tier_schema,
)


def test_report_schema_is_flat_and_strict():
    s = report_schema()
    assert "$defs" not in json.dumps(s)
    assert s["additionalProperties"] is False
    for banned in ("type", "window_start", "window_end"):
        assert banned not in s["properties"]


def test_tier_schema_two_branches():
    s = tier_schema()
    olagan, dikkat = s["oneOf"]
    assert olagan["properties"]["durum"]["enum"] == ["olagan"]
    assert list(olagan["properties"]) == ["summary", "durum"]
    assert list(dikkat["properties"])[:2] == ["summary", "durum"]
    assert dikkat["properties"]["durum"]["enum"] == ["dikkat"]
    assert "events" in dikkat["properties"]
    for branch in (olagan, dikkat):
        assert branch["additionalProperties"] is False
        assert "durum" in branch["required"]


def test_to_report_olagan_branch():
    r = _to_report(30.0, 60.0, '{"durum": "olagan", "summary": "Sahne sakin."}')
    assert r.anomaly_type == "normal" and r.events == []
    assert r.summary == "Sahne sakin."
    assert (r.window_start, r.window_end) == (30.0, 60.0)


def test_to_report_dikkat_branch():
    raw = json.dumps({
        "durum": "dikkat", "anomaly_type": "kavga", "summary": "İki kişi kavga ediyor.",
        "events": [{"t": 42.0, "desc": "yumruk", "severity_hint": "yuksek"}],
        "uncertainties": [],
    })
    r = _to_report(30.0, 60.0, raw)
    assert r.anomaly_type == "kavga"
    assert r.events[0].severity_hint == "yuksek"


def test_to_report_single_tier_backcompat():
    raw = json.dumps({"anomaly_type": "normal", "summary": "s", "events": [],
                      "uncertainties": []})
    r = _to_report(0.0, 30.0, raw)
    assert r.anomaly_type == "normal" and r.summary == "s"


def test_repair_truncated_event_list():
    raw = ('{"summary": "İki kişi tartışıyor", "durum": "dikkat", '
           '"anomaly_type": "kavga", "uncertainties": [], "events": ['
           '{"t": 5.0, "desc": "itişme", "severity_hint": "orta"}, '
           '{"t": 9.0, "desc": "yumruk atıl')
    fixed = repair_truncated_json(raw)
    data = json.loads(fixed)
    assert len(data["events"]) == 1
    assert data["events"][0]["desc"] == "itişme"
    assert data["anomaly_type"] == "kavga"


def test_to_report_recovers_and_flags_truncation():
    raw = ('{"summary": "kalabalık", "durum": "dikkat", "anomaly_type": "kavga", '
           '"uncertainties": [], "events": ['
           '{"t": 3.0, "desc": "koşuşturma", "severity_hint": "orta"}, '
           '{"t": 7.0, "desc": "yar')
    r = _to_report(0.0, 30.0, raw)
    assert len(r.events) == 1
    assert any("kesildi" in u for u in r.uncertainties)


def test_to_report_flags_length_finish_even_if_valid():
    raw = '{"summary": "s", "durum": "olagan"}'
    r = _to_report(0.0, 30.0, raw, truncated=True)
    assert any("token sınırı" in u for u in r.uncertainties)


def test_repair_returns_none_for_hopeless_input():
    assert repair_truncated_json('{"summary": "yarım') is None


def test_continuity_hint_empty_when_no_open_incident():
    from dortgoz.agent.memory import Ledger
    assert Ledger().continuity_hint() == ""


def test_continuity_hint_carries_state_and_guards_anchoring():
    from dortgoz.agent.memory import Ledger
    from dortgoz.events import WindowEvent, WindowReport
    led = Ledger()
    led.ingest(WindowReport(window_start=0, window_end=30, anomaly_type="kavga",
                            summary="kavga",
                            events=[WindowEvent(t=12.0, desc="İki kişi kavga ediyor",
                                                severity_hint="yuksek")]))
    hint = led.continuity_hint()
    assert "SÜREGELEN OLAY" in hint and "12. saniyede" in hint
    assert "BİTTİYSE" in hint and "uydurma" in hint


def test_review_schema_uses_canonical_taxonomy_and_evidence_contract():
    from dortgoz.domain.taxonomy import CanonicalEventType

    s = review_schema()
    assert s["properties"]["event_type"]["enum"] == [item.value for item in CanonicalEventType]
    assert s["properties"]["evidence"]["minItems"] == 1
    assert set(s["required"]) >= {
        "baslangic",
        "zirve",
        "sonuc",
        "event_type",
        "risk",
        "evidence",
    }


def test_apply_review_rewrites_incident():
    from dortgoz.agent.memory import Ledger
    from dortgoz.events import WindowEvent, WindowReport
    led = Ledger()
    ups = led.ingest(WindowReport(window_start=0, window_end=30, anomaly_type="bilinmeyen",
                                  summary="?",
                                  events=[WindowEvent(t=5.0, desc="itişme",
                                                      severity_hint="orta")]))
    iid = ups[0].incident_id
    rev = led.apply_review(iid, {"baslangic": "Grup toplandı", "zirve": "Kişi yere düşürüldü",
                                 "sonuc": "Grup dağıldı", "zirve_t": 42.0,
                                 "anomaly_type": "saldiri", "risk": "yuksek",
                                 "belirsizlikler": ["yaralı mı belirsiz"]})
    assert rev.anomaly_type == "saldiri" and rev.risk == "orta"
    assert rev.t == 42.0
    assert "Başlangıç: Grup toplandı" in rev.detail
    assert "Zirve: Kişi yere düşürüldü" in rev.detail
    assert "? yaralı mı belirsiz" in rev.detail
    assert led.incidents[iid].anomaly_type == "saldiri"


def test_review_detail_trims_at_sentence_boundary():
    from dortgoz.agent.memory import _trim
    long = ("Olay başladı. " * 120)
    out = _trim(long)
    assert len(out) <= 1210
    assert out.endswith(". …")
    assert _trim("kısa metin") == "kısa metin"


def test_ledger_grace_keeps_incident_open_across_one_quiet_window():
    from dortgoz.agent.memory import Ledger
    from dortgoz.events import WindowEvent, WindowReport
    def rep(t, sev=None):
        return WindowReport(window_start=t, window_end=t + 30, summary="",
                            events=[] if sev is None else
                            [WindowEvent(t=t + 5, desc="olay", severity_hint=sev)])
    led = Ledger(grace_windows=1)
    led.ingest(rep(0, "orta"))
    assert led.open_incident is not None
    assert led.ingest(rep(30)) == []
    assert led.open_incident is not None
    ups = led.ingest(rep(60, "orta"))
    assert ups and ups[0].phase == "gelisiyor"
    assert len({u.incident_id for u in ups}) == 1
    led.ingest(rep(90))
    closed = led.ingest(rep(120))
    assert closed and closed[0].phase == "sonuclandi"


def test_ledger_grace_zero_is_old_behaviour():
    from dortgoz.agent.memory import Ledger
    from dortgoz.events import WindowEvent, WindowReport
    led = Ledger(grace_windows=0)
    led.ingest(WindowReport(window_start=0, window_end=30, summary="",
                            events=[WindowEvent(t=5, desc="x", severity_hint="orta")]))
    out = led.ingest(WindowReport(window_start=30, window_end=60, summary=""))
    assert out and out[0].phase == "sonuclandi"


def test_title_strips_leading_timestamps():
    from dortgoz.agent.memory import _title_text
    assert _title_text("t=1147s ile t=1200s arasında zirve yaptı") == "Zirve yaptı"
    assert _title_text("t=1102s civarında, merdiven kenarında duran kişi düştü") \
        == "Merdiven kenarında duran kişi düştü"
    assert _title_text("İki kişi kavga ediyor") == "İki kişi kavga ediyor"


def _prof(vals, step=1.0):
    from dortgoz.pipeline.ingest import MotionSample
    return [MotionSample(t=i * step, changed=v, fg=0.0, mad=v) for i, v in enumerate(vals)]


def test_activity_windows_skip_dead_footage_and_anchor_on_onset():
    from dortgoz.pipeline.windowing import activity_windows
    prof = _prof([0.0] * 10 + [0.5] * 5 + [0.0] * 20 + [0.6] * 3 + [0.0] * 10)
    w = activity_windows(prof, duration=48, gate=0.1, preroll=3.0)
    assert len(w) == 2
    assert 6.0 <= w[0][0] <= 8.0
    assert w[0][1] < w[1][0]
    covered = sum(b - a for a, b in w)
    assert covered < 48 * 0.6


def test_activity_windows_do_not_split_on_short_pause():
    from dortgoz.pipeline.windowing import activity_windows
    w = activity_windows(_prof([0.5] * 5 + [0.0] * 3 + [0.5] * 5),
                         duration=13, gate=0.1, quiet_tail=6.0)
    assert len(w) == 1


def test_activity_windows_cap_long_activity():
    from dortgoz.pipeline.windowing import activity_windows
    w = activity_windows(_prof([0.5] * 120), duration=120, gate=0.1, max_len=45)
    assert len(w) >= 3
    assert all(b - a <= 46 for a, b in w)


def test_activity_windows_empty_profile_is_safe():
    from dortgoz.pipeline.windowing import activity_windows
    assert activity_windows([], duration=10, gate=0.1) == []
    assert activity_windows(_prof([0.0] * 20), duration=20, gate=0.1) == []
