"""İki kademeli yorumlama şeması — GPU'suz birim testleri."""

import json

from dortgoz.pipeline.interpret import _to_report, report_schema, tier_schema


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
