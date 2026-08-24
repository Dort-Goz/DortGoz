from __future__ import annotations

import json
import zipfile

import pytest

from dortgoz import session
from dortgoz.config import settings
from dortgoz.services import analysis_package as ap


def _line(payload: dict) -> str:
    return json.dumps({"seq": 0, "ts": 0.0, "feed": "", "payload": payload},
                      ensure_ascii=False)


REPORT = {
    "type": "window_report", "window_start": 30.0, "window_end": 60.0,
    "anomaly_type": "kavga", "summary": "İki kişi arasında fiziksel kavga.",
    "events": [{
        "t": 42.0, "desc": "Yumruklaşma görülüyor.", "severity_hint": "yuksek",
        "event_type": "physical_fight",
        "evidence": [{"frame_id": "f_001", "timestamp": 41.5,
                      "claim": "İki kişinin yumruklaştığı görülmektedir."}],
    }],
    "uncertainties": [],
}
INCIDENT = {
    "type": "incident_update", "incident_id": "inc-1", "t": 42.0,
    "phase": "sonuclandi", "title": "Fiziksel kavga", "anomaly_type": "kavga",
    "risk": "yuksek", "detail": "Pencere 30-60 raporundan.",
    "needs_review": False, "olay_baslangic": 40.5, "olay_bitis": 42.5,
}


@pytest.fixture
def run_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runs_dir", tmp_path / "runs")
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    settings.runs_dir.mkdir()
    (settings.runs_dir / "test1.jsonl").write_text(
        _line(REPORT) + "\n" + _line(INCIDENT) + "\n", encoding="utf-8")
    (settings.runs_dir / "test1.meta.json").write_text(json.dumps(
        {"video": "yok.mp4", "model": "test-model", "mode": "dengeli"}))
    session.clear()
    yield "test1"
    session.clear()


def test_export_produces_valid_package(run_fixture):
    pkg = ap.export_analysis(run_fixture)
    with zipfile.ZipFile(pkg) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "analiz.jsonl", "meta.json", "ozet.md"} <= names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format"] == "dortgoz-analiz"
        assert manifest["kanit_zamanlari"] == [41.5]
        ozet = zf.read("ozet.md").decode()
        assert "Fiziksel kavga" in ozet and "kavga" in ozet


def test_roundtrip_rebuilds_chat_context(run_fixture):
    pkg = ap.export_analysis(run_fixture)
    ctx = ap.import_analysis(pkg)

    assert ctx.run_id == "ithal-test1"
    assert ctx.finished is True
    assert len(ctx.reports) == 1
    assert ctx.reports[0].events[0].evidence[0].frame_id == "f_001"
    assert ctx.duration == 60.0

    incidents = ctx.incidents
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.anomaly_type == "kavga"
    assert (inc.olay_baslangic, inc.olay_bitis) == (40.5, 42.5)
    assert inc.first_seen == 40.5 and inc.last_seen == 42.5

    assert session.current() is ctx
    briefing = ctx.briefing()
    assert "Fiziksel kavga" in briefing and "yumruklaşma" in briefing.lower()
    assert "1 olay tespit edildi" in ctx.verdict()

    assert ap.export_analysis(ctx.run_id).is_file()


def test_import_rejects_wrong_format(run_fixture, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "baska"}))
        zf.writestr("analiz.jsonl", "")
    with pytest.raises(ValueError, match="biçimi"):
        ap.import_analysis(bad)


def test_import_rejects_checksum_mismatch(run_fixture, tmp_path):
    pkg = ap.export_analysis(run_fixture)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(pkg) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "analiz.jsonl":
                data += b"\n"
            dst.writestr(name, data)
    with pytest.raises(ValueError, match="sağlama"):
        ap.import_analysis(tampered)


def test_import_rejects_newer_version(run_fixture, tmp_path):
    bad = tmp_path / "yeni.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format": "dortgoz-analiz", "surum": ap.FORMAT_VERSION + 1, "sha256": {}}))
        zf.writestr("analiz.jsonl", "")
    with pytest.raises(ValueError, match="çok yeni"):
        ap.import_analysis(bad)


@pytest.mark.parametrize("run_id", ["../../outside", r"..\..\outside", "C:outside"])
def test_import_rejects_manifest_run_id_traversal(run_fixture, tmp_path, run_id):
    bad = tmp_path / "traversal.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": "dortgoz-analiz",
            "surum": ap.FORMAT_VERSION,
            "run_id": run_id,
            "sha256": {},
        }))
        zf.writestr("analiz.jsonl", "")

    with pytest.raises(ValueError, match="koşu kimliği"):
        ap.import_analysis(bad)

    assert not (tmp_path / "outside.jsonl").exists()


def test_export_rejects_run_id_traversal(run_fixture):
    with pytest.raises(ValueError, match="koşu kimliği"):
        ap.export_analysis(r"..\..\secret")


def test_export_survives_partial_final_jsonl_line(run_fixture):

    jsonl = settings.runs_dir / "test1.jsonl"
    with jsonl.open("a", encoding="utf-8") as stream:
        stream.write('{"seq": 2, "ts": 0.0, "payload": {"type": "window_re')

    pkg = ap.export_analysis(run_fixture)
    ctx = ap.import_analysis(pkg)

    assert len(ctx.reports) == 1
    assert len(ctx.incidents) == 1


def test_export_leaves_no_part_file_behind(run_fixture):

    pkg = ap.export_analysis(run_fixture)

    assert pkg.is_file()
    assert list(pkg.parent.glob("*.part")) == []


def test_failed_export_writes_no_target_package(run_fixture):

    (settings.runs_dir / "test1.jsonl").unlink()

    with pytest.raises(FileNotFoundError):
        ap.export_analysis(run_fixture)

    paketler = settings.runs_dir / "paketler"
    assert list(paketler.glob("*")) == []
