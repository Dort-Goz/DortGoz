import json

from dortgoz.config import settings
from dortgoz.pipeline.runner import RunRecorder
from dortgoz.services.runtime_metrics import CanonicalRunMetrics


def test_record_diagnostic_writes_parseable_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "runs_dir", tmp_path)
    rec = RunRecorder(manager=object(), run_id="diag-test",
                      metrics=CanonicalRunMetrics(run_id="diag-test"), feed="f1")
    rec.record_diagnostic({
        "type": "screening_scores",
        "model": "siglip-test",
        "start_threshold": 0.80,
        "continue_threshold": 0.48,
        "windows": [{"start": 0.0, "end": 30.0, "score": 0.91},
                    {"start": 30.0, "end": 60.0, "score": None}],
    })
    rec.close()
    lines = (tmp_path / "diag-test.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])["payload"]
    assert payload["type"] == "screening_scores"
    assert payload["windows"][0]["score"] == 0.91
    assert payload["windows"][1]["score"] is None
