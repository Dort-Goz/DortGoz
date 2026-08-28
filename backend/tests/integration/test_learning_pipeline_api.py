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
    assert payload["stages"][0]["count"] == 0
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


def test_it_review_and_fine_tune_decision_move_event_into_dfine_queue(monkeypatch) -> None:
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

        invalid_maintenance = client.post(
            f"/api/events/{event_id}/maintenance-review",
            json={
                "operator_review_id": review.json()["review_id"],
                "decision": "confirm",
                "reviewer": "it-operator",
                "note": "Kategori olmadan kaydedilmemeli.",
            },
        )
        assert invalid_maintenance.status_code == 422

        maintenance = client.post(
            f"/api/events/{event_id}/maintenance-review",
            json={
                "operator_review_id": review.json()["review_id"],
                "decision": "confirm",
                "event_type": "possible_theft",
                "reviewer": "it-operator",
                "note": "IT olayı bağımsız doğruladı.",
            },
        )
        assert maintenance.status_code == 200

        approved = client.post(
            f"/api/events/{event_id}/development-approval",
            json={
                "review_id": review.json()["review_id"],
                "maintenance_review_id": maintenance.json()["maintenance_review_id"],
                "status": "approved",
                "approved_uses": ["d_fine_training"],
                "reviewer": "it-operator",
                "note": "D-FINE fine-tune kuyruğuna gönderildi.",
            },
        )
        assert approved.status_code == 200

        pipeline = client.get("/api/learning/pipeline").json()

    queue = {group["use"]: group["count"] for group in pipeline["queue"]}
    assert queue == {"d_fine_training": 1}
    stages = {stage["stage"]: stage["count"] for stage in pipeline["stages"]}
    assert stages["queue"] == 1
    assert stages["review"] == 0


def test_rejected_learning_decision_leaves_the_approval_queue(monkeypatch) -> None:
    runtime = ApiRuntime()
    monkeypatch.setattr(api_module, "runtime", runtime)
    event_id = _seed_reviewed_event(runtime, 4)

    with TestClient(app) as client:
        review = client.post(
            f"/api/events/{event_id}/review",
            json={
                "decision": "confirm",
                "reviewer": "operator",
                "note": "Olay kararı doğrulandı.",
            },
        )
        assert review.status_code == 200
        assert review.json()["decision"] == "edit"
        assert review.json()["event_type"] == "possible_theft"
        assert review.json()["start_time"] == 1
        assert review.json()["peak_time"] == 2
        assert review.json()["end_time"] == 3

        before = client.get("/api/learning/pipeline").json()
        assert event_id in {item["event_id"] for item in before["review_items"]}

        maintenance = client.post(
            f"/api/events/{event_id}/maintenance-review",
            json={
                "operator_review_id": review.json()["review_id"],
                "decision": "reject",
                "reviewer": "it-operator",
                "note": "IT anomali bulmadı.",
                "false_alarm_reason": "normal_activity",
            },
        )
        assert maintenance.status_code == 200

        awaiting = client.get("/api/learning/pipeline").json()
        assert event_id in {item["event_id"] for item in awaiting["approval_items"]}

        rejected = client.post(
            f"/api/events/{event_id}/development-approval",
            json={
                "review_id": review.json()["review_id"],
                "maintenance_review_id": maintenance.json()["maintenance_review_id"],
                "status": "rejected",
                "approved_uses": [],
                "reviewer": "operator",
                "note": "Bu olay geliştirme hazırlığında kullanılmayacak.",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        after = client.get("/api/learning/pipeline").json()

    assert event_id not in {item["event_id"] for item in after["approval_items"]}
    stages = {stage["stage"]: stage["count"] for stage in after["stages"]}
    assert stages["approval"] == 0
    assert stages["queue"] == 0


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
