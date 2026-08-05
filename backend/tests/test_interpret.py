"""İki kademeli yorumlama şeması — GPU'suz birim testleri."""

import json

from dortgoz.pipeline.interpret import (
    _to_report, repair_truncated_json, report_schema, tier_schema)


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
    # Gözlem-önce sırası: summary her iki dalda İLK alan, durum ondan sonra
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
    """two_tier kapalıyken model `durum` üretmez — düz rapor aynen çalışmalı."""
    raw = json.dumps({"anomaly_type": "normal", "summary": "s", "events": [],
                      "uncertainties": []})
    r = _to_report(0.0, 30.0, raw)
    assert r.anomaly_type == "normal" and r.summary == "s"


# ---- kesilmiş çıktı kurtarma (2026-08-05: bir koşu 19. dakikada bu yüzden düştü) ----

def test_repair_truncated_event_list():
    """Olay listesi ortasında kesilen JSON, son TAM olaya kadar kurtarılmalı."""
    raw = ('{"summary": "İki kişi tartışıyor", "durum": "dikkat", '
           '"anomaly_type": "kavga", "uncertainties": [], "events": ['
           '{"t": 5.0, "desc": "itişme", "severity_hint": "orta"}, '
           '{"t": 9.0, "desc": "yumruk atıl')
    fixed = repair_truncated_json(raw)
    data = json.loads(fixed)          # geçerli JSON üretmeli
    assert len(data["events"]) == 1   # yarım olay atıldı
    assert data["events"][0]["desc"] == "itişme"
    assert data["anomaly_type"] == "kavga"


def test_to_report_recovers_and_flags_truncation():
    raw = ('{"summary": "kalabalık", "durum": "dikkat", "anomaly_type": "kavga", '
           '"uncertainties": [], "events": ['
           '{"t": 3.0, "desc": "koşuşturma", "severity_hint": "orta"}, '
           '{"t": 7.0, "desc": "yar')
    r = _to_report(0.0, 30.0, raw)
    assert len(r.events) == 1
    assert any("kesildi" in u for u in r.uncertainties)   # operatör görmeli


def test_to_report_flags_length_finish_even_if_valid():
    raw = '{"summary": "s", "durum": "olagan"}'
    r = _to_report(0.0, 30.0, raw, truncated=True)
    assert any("token sınırı" in u for u in r.uncertainties)


def test_repair_returns_none_for_hopeless_input():
    assert repair_truncated_json('{"summary": "yarım') is None
