from __future__ import annotations

from fastapi.testclient import TestClient
from test_task06_api import metadata

from dortgoz.api import router as api_module
from dortgoz.api.router import ApiRuntime
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.provenance import AnalysisProvenance
from dortgoz.main import app

ANALYSIS_ID = "analysis-pipeline-api"


def _seed_reviewed_event(runtime: ApiRuntime, index: int) -> str:
    video = metadata(f"00000000-0000-0000-0000-0000000009{index:02d}")
    runtime.repository.create_video(video)
    runtime.repository.create_analysis(
        video.video_id,
        AnalysisProvenance(
            contract_version="1",
            config_version="pipeline-api",
            code_revision="test",
        ),
        analysis_id=f"{ANALYSIS_ID}-{index}",
    )
    candidate_id = f"candidate-pipeline-{index}"
    runtime.repository.save_candidate(
        CandidateEvent(
            candidate_id=candidate_id,
            analysis_id=f"{ANALYSIS_ID}-{index}",
            video_id=video.video_id,
            start_time=1,
            peak_time=2,
            end_time=3,
            candidate_type=CandidateType.UNKNOWN_ANOMALY,
            peak_score=0.5,
            anomaly_score=0.5,
            trigger_signals=["test"],
            screening_model_id="screening-test",
            threshold_version="test",
        )
    )
    event = runtime.repository.save_event(
        VerifiedEvent(
            event_id=f"event-pipeline-{index}",
            analysis_id=f"{ANALYSIS_ID}-{index}",
            video_id=video.video_id,
            candidate_id=candidate_id,
            status=EventStatus.HUMAN_REVIEW,
            event_type=VerifiedEventType.POSSIBLE_THEFT,
            start_time=1,
            peak_time=2,
            end_time=3,
            confidence=0.5,
        )
    )
    return event.event_id


def test_pipeline_endpoint_reports_every_stage(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    _seed_reviewed_event(runtime, 1)

    with TestClient(app) as client:
        response = client.get("/api/learning/pipeline")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pipeline_version"] == "dortgoz-learning-pipeline-v1"
    assert payload["mode"] == "human_gated"
    assert payload["automatic_training"] is False
    assert payload["automatic_promotion"] is False
    assert [stage["stage"] for stage in payload["stages"]] == [
        "review",
        "approval",
        "queue",
        "training",
        "measurement",
        "promotion",
    ]
    assert payload["stages"][0]["count"] == 1
    assert payload["jobs"] == []
    assert payload["candidates"] == []
    assert payload["champion"] is None


def test_batch_approval_reports_events_without_review(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    event_id = _seed_reviewed_event(runtime, 2)

    with TestClient(app) as client:
        response = client.post(
            "/api/learning/approvals/batch",
            json={
                "event_ids": [event_id, "event-yok"],
                "approved_uses": ["evaluation"],
                "reviewer": "muhendis",
                "note": "Toplu onay denemesi.",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["approved_event_ids"] == []
    reasons = {failure["event_id"]: failure["reason"] for failure in payload["failures"]}
    assert reasons[event_id] == "olay için insan incelemesi yok"
    assert "event-yok" in reasons


def test_batch_approval_moves_reviewed_events_into_the_queue(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    event_id = _seed_reviewed_event(runtime, 3)

    with TestClient(app) as client:
        review = client.post(
            f"/api/events/{event_id}/review",
            json={
                "decision": "edit",
                "reviewer": "operator",
                "note": "Olay zamanları doğrulandı.",
                "start_time": 1,
                "peak_time": 2,
                "end_time": 3,
            },
        )
        assert review.status_code == 200

        approved = client.post(
            "/api/learning/approvals/batch",
            json={
                "event_ids": [event_id],
                "approved_uses": ["evaluation"],
                "reviewer": "muhendis",
                "note": "Değerlendirme havuzu için onaylandı.",
            },
        )
        assert approved.status_code == 200
        assert approved.json()["approved_event_ids"] == [event_id]

        pipeline = client.get("/api/learning/pipeline").json()

    queue = {group["use"]: group["count"] for group in pipeline["queue"]}
    assert queue["evaluation"] == 1
    stages = {stage["stage"]: stage["count"] for stage in pipeline["stages"]}
    assert stages["queue"] == 1
    assert stages["review"] == 0


def test_training_endpoints_refuse_when_the_machine_is_not_configured(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)

    with TestClient(app) as client:
        planned = client.post(
            "/api/learning/jobs",
            json={"architecture": "dfine_n", "requested_by": "muhendis"},
        )
        missing_job = client.get("/api/learning/jobs/dfine-job-yok")

    assert planned.status_code == 409
    assert planned.json()["error"]["code"] == "TRAINING_NOT_CONFIGURED"
    assert missing_job.status_code == 404
