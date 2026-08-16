"""Nöbet kuyruğu, kalıcı feedback ve kontrollü kural yaşam döngüsü."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from dortgoz.api.contracts import TriageDecisionInput
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.feedback import FalseAlarmReason, RuleProposalStatus
from dortgoz.domain.media import IncidentMedia
from dortgoz.domain.provenance import AnalysisProvenance, ReviewDecision
from dortgoz.domain.taxonomy import VerifiedEventType, canonical_event_type_from_ws_label
from dortgoz.domain.video import VideoMetadata
from dortgoz.events import Event, IncidentUpdate, RunStatus
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services import triage


class CanonicalTriageStore(triage.TriageStore):
    """Her test incident'ını gerçek canonical parent kayıtlarıyla hazırlar."""

    def __init__(self, clock=None, *, virtual_source: bool = False) -> None:
        self.repo = InMemoryEventRepository()
        self.ids: dict[tuple[str, str], str] = {}
        self.virtual_source = virtual_source
        super().__init__(
            self.repo,
            event_id_resolver=lambda feed, incident_id: self.ids.get((feed, incident_id)),
            clock=clock,
        )

    def observe(self, event: Event) -> None:
        if isinstance(event.payload, IncidentUpdate):
            self._ensure_event(event)
        super().observe(event)

    def _ensure_event(self, envelope: Event) -> None:
        payload = envelope.payload
        assert isinstance(payload, IncidentUpdate)
        scope = f"{envelope.feed}:{payload.incident_id}"
        event_id = f"event:{scope}"
        self.ids[(envelope.feed, payload.incident_id)] = event_id
        if self.repo.get_event(event_id) is not None:
            return
        video_uuid = str(uuid5(NAMESPACE_URL, scope))
        video = VideoMetadata(
            video_id=video_uuid,
            original_filename=f"{payload.incident_id}.mp4",
            stored_filename=f"{video_uuid}.mp4",
            media_path=f"{video_uuid}.mp4",
            file_size_bytes=100,
            file_hash_sha256=hashlib.sha256(scope.encode()).hexdigest(),
            container="mp4",
            codec="h264",
            width=640,
            height=360,
            fps=25,
            duration_seconds=60,
            has_audio=False,
            time_base="1/25",
            warnings=["MOCK_VIRTUAL_SOURCE"] if self.virtual_source else [],
        )
        self.repo.create_video(video)
        analysis_id = f"analysis:{scope}"
        self.repo.create_analysis(
            video.video_id,
            AnalysisProvenance(
                contract_version="1", config_version="test", code_revision="test"
            ),
            analysis_id=analysis_id,
        )
        candidate_id = f"candidate:{scope}"
        self.repo.save_candidate(
            CandidateEvent(
                candidate_id=candidate_id,
                analysis_id=analysis_id,
                video_id=video.video_id,
                start_time=payload.t,
                peak_time=payload.t,
                end_time=payload.t + 0.001,
                candidate_type=CandidateType.UNKNOWN_ANOMALY,
                peak_score=0.5,
                anomaly_score=0.5,
                trigger_signals=["triage-test"],
                screening_model_id="test",
                threshold_version="test",
            )
        )
        self.repo.save_event(
            VerifiedEvent(
                event_id=event_id,
                analysis_id=analysis_id,
                video_id=video.video_id,
                candidate_id=candidate_id,
                status=EventStatus.HUMAN_REVIEW,
                event_type=VerifiedEventType(
                    canonical_event_type_from_ws_label(payload.anomaly_type).value
                ),
                start_time=payload.t,
                peak_time=payload.t,
                end_time=payload.t + 0.001,
                legacy_event_type=payload.anomaly_type,
            )
        )


@pytest.fixture
def store() -> CanonicalTriageStore:
    return CanonicalTriageStore()


def _incident(
    feed="KAM-1",
    incident_id="inc-1",
    risk="orta",
    anomaly_type="vandalizm",
    phase="basladi",
) -> Event:
    return Event.wrap(
        IncidentUpdate(
            incident_id=incident_id,
            t=42.0,
            phase=phase,
            title="Şüpheli olay",
            anomaly_type=anomaly_type,
            risk=risk,
        ),
        feed=feed,
    )


def _dismiss(store: CanonicalTriageStore, incident_id: str) -> None:
    store.observe(_incident(incident_id=incident_id))
    store.decide(f"KAM-1:{incident_id}", "sorun_degil")


def _propose(store: CanonicalTriageStore):
    for index in range(triage.RULE_THRESHOLD):
        _dismiss(store, f"i{index}")
    proposals = store.repo.list_rule_proposals()
    assert len(proposals) == 1
    return proposals[0]


def test_incident_update_lands_in_pending(store):
    store.observe(_incident())
    item = store.snapshot()["pending"][0]
    assert item["feed"] == "KAM-1"
    assert item["model_category"] == "vandalizm"
    assert item["event_id"] == "event:KAM-1:inc-1"
    assert item["clip_url"] is None
    assert item["intervention_score"] == 45
    assert item["intervention_band"] == "review"
    assert item["priority_ruleset_version"] == "intervention-priority-v1"


def test_pending_card_exposes_persisted_incident_clip(store):
    store.observe(_incident())
    event = store.repo.get_event("event:KAM-1:inc-1")
    assert event is not None
    store.repo.save_incident_media(
        IncidentMedia(
            media_id="triage-incident-media",
            event_id=event.event_id,
            analysis_id=event.analysis_id,
            video_id=event.video_id,
            event_revision=event.revision,
            source_refs=[f"{event.video_id}.mp4"],
            source_file_sha256=store.repo.get_video(event.video_id).file_hash_sha256,
            clip_ref="_incident_media/triage/incident.mp4",
            thumbnail_ref="_incident_media/triage/thumbnail.jpg",
            clip_start=34,
            clip_end=50,
            peak_time=42,
            pre_capture_seconds=8,
            post_capture_seconds=8,
            clip_sha256="c" * 64,
            thumbnail_sha256="d" * 64,
            clip_size_bytes=200,
            thumbnail_size_bytes=50,
        )
    )

    item = store.snapshot()["pending"][0]

    assert item["clip_url"] == "/media/_incident_media/triage/incident.mp4"
    assert item["media_thumbnail_url"].endswith("thumbnail.jpg")


def test_lifecycle_update_refreshes_not_duplicates(store):
    store.observe(_incident(phase="basladi", risk="orta"))
    store.observe(_incident(phase="sonuclandi", risk="kritik"))
    pending = store.snapshot()["pending"]
    assert len(pending) == 1
    assert pending[0]["risk"] == "kritik"
    assert pending[0]["phase"] == "sonuclandi"
    assert pending[0]["intervention_score"] == 75
    priority = store.repo.get_intervention_priority_for_event(pending[0]["event_id"])
    assert priority is not None
    assert priority.revision == 2


def test_pending_queue_is_sorted_by_intervention_score(store):
    store.observe(
        _incident(
            incident_id="routine",
            risk="dusuk",
            anomaly_type="vandalizm",
            phase="sonuclandi",
        )
    )
    store.observe(
        _incident(
            incident_id="urgent",
            risk="dusuk",
            anomaly_type="silahli_olay",
            phase="sonuclandi",
        )
    )
    store.observe(
        _incident(
            incident_id="review",
            risk="orta",
            anomaly_type="hirsizlik",
            phase="sonuclandi",
        )
    )

    pending = store.snapshot()["pending"]

    assert [item["incident_id"] for item in pending] == ["urgent", "review", "routine"]
    assert [item["intervention_score"] for item in pending] == [80, 40, 15]


def test_non_incident_events_ignored(store):
    store.observe(Event.wrap(RunStatus(run_id="r", state="processing")))
    assert store.snapshot()["pending"] == []


def test_operator_decision_is_saved_as_canonical_human_review(store):
    store.observe(_incident())
    item = store.decide(
        "KAM-1:inc-1", "anomali", category="hirsizlik", note="Kasaya uzanıyor"
    )
    reviews = store.repo.list_reviews(item.event_id)
    assert item.operator_category == "hirsizlik"
    assert item.review_ids == [reviews[0].review_id]
    assert reviews[0].decision == ReviewDecision.EDIT
    assert reviews[0].event_type == "possible_theft"
    assert reviews[0].note == "Kasaya uzanıyor"


def test_structured_anomaly_feedback_saves_corrections(store):
    store.observe(_incident())
    item = store.decide(
        "KAM-1:inc-1",
        "anomali",
        category="hirsizlik",
        risk_level="kritik",
        start_time=40,
        peak_time=42,
        end_time=45,
        intervention_required=False,
        note="Olay gerçekti fakat müdahale gerektirmedi.",
        reviewer="operator-1",
    )
    review = store.repo.list_reviews(item.event_id)[0]
    event = store.repo.get_event(item.event_id)

    assert review.event_type == "possible_theft"
    assert review.risk_level == "kritik"
    assert (review.start_time, review.peak_time, review.end_time) == (40, 42, 45)
    assert review.intervention_required is False
    assert review.reviewer == "operator-1"
    assert event is not None
    assert (event.start_time, event.peak_time, event.end_time) == (40, 42, 45)
    assert item.operator_risk == "kritik"
    assert item.intervention_required is False


def test_structured_feedback_rejects_time_outside_video(store):
    store.observe(_incident())

    with pytest.raises(ValueError, match="video süresini aşamaz"):
        store.decide(
            "KAM-1:inc-1",
            "anomali",
            category="vandalizm",
            risk_level="orta",
            start_time=40,
            peak_time=42,
            end_time=61,
            intervention_required=True,
        )

    assert len(store.snapshot()["pending"]) == 1


def test_virtual_live_source_accepts_stream_timeline_times() -> None:
    store = CanonicalTriageStore(virtual_source=True)
    store.observe(_incident())

    item = store.decide(
        "KAM-1:inc-1",
        "anomali",
        category="vandalizm",
        risk_level="orta",
        start_time=40,
        peak_time=42,
        end_time=75,
        intervention_required=True,
    )

    review = store.repo.list_reviews(item.event_id)[0]
    assert review.end_time == 75


def test_dismissal_is_saved_and_does_not_leave_jsonl_side_channel(store):
    store.observe(_incident())
    item = store.decide("KAM-1:inc-1", "sorun_degil")
    review = store.repo.list_reviews(item.event_id)[0]
    assert review.decision == ReviewDecision.REJECT
    assert review.intervention_required is False
    assert store.snapshot()["dismissed_count"] == 1


def test_non_normal_false_alarm_does_not_create_camera_suppression(store):
    for index in range(triage.RULE_THRESHOLD):
        incident_id = f"camera-{index}"
        store.observe(_incident(incident_id=incident_id))
        item = store.decide(
            f"KAM-1:{incident_id}",
            "sorun_degil",
            false_alarm_reason=FalseAlarmReason.CAMERA_CONDITION,
            intervention_required=False,
            note="Işık geçişi modeli yanılttı.",
        )
        review = store.repo.list_reviews(item.event_id)[0]
        assert review.false_alarm_reason == FalseAlarmReason.CAMERA_CONDITION

    assert store.repo.list_rule_proposals() == []


def test_triage_decision_contract_requires_structured_feedback() -> None:
    valid = TriageDecisionInput.model_validate(
        {
            "key": "KAM-1:inc-1",
            "verdict": "anomali",
            "category": "vandalizm",
            "risk_level": "orta",
            "start_time": 40,
            "peak_time": 42,
            "end_time": 45,
            "intervention_required": True,
            "reviewer": "operator-1",
        }
    )
    assert valid.category == "vandalizm"

    with pytest.raises(ValidationError, match="yanlış alarm nedeni gerektirir"):
        TriageDecisionInput.model_validate(
            {
                "key": "KAM-1:inc-1",
                "verdict": "sorun_degil",
                "intervention_required": False,
            }
        )
    with pytest.raises(ValidationError, match="açıklama gerektirir"):
        TriageDecisionInput.model_validate(
            {
                "key": "KAM-1:inc-1",
                "verdict": "sorun_degil",
                "false_alarm_reason": "other",
                "intervention_required": False,
            }
        )


def test_decided_incident_does_not_requeue(store):
    store.observe(_incident())
    store.decide("KAM-1:inc-1", "sorun_degil")
    store.observe(_incident(phase="sonuclandi"))
    assert store.snapshot()["pending"] == []


def test_invalid_decision_keeps_item_pending(store):
    store.observe(_incident())
    with pytest.raises(ValueError):
        store.decide("KAM-1:inc-1", "anomali", category="normal")
    assert len(store.snapshot()["pending"]) == 1


def test_missing_canonical_event_refuses_to_drop_decision():
    store = triage.TriageStore()
    store.observe(_incident())
    with pytest.raises(triage.TriagePersistenceError):
        store.decide("KAM-1:inc-1", "sorun_degil")
    assert len(store.snapshot()["pending"]) == 1


def test_distinct_incidents_keep_distinct_feedback_cards(store):
    for incident_id in ("a", "b", "c"):
        store.observe(_incident(incident_id=incident_id))
    assert len(store.snapshot()["pending"]) == 3


def test_three_dismissals_only_create_proposal(store):
    proposal = _propose(store)
    assert proposal.status == RuleProposalStatus.PROPOSED
    store.observe(_incident(incident_id="sonraki"))
    assert len(store.snapshot()["pending"]) == 1
    assert store.snapshot()["auto_dismissed"] == 0


def test_separate_approval_enables_temporary_rule(store):
    proposal = _propose(store)
    approved = store.approve_rule(proposal.proposal_id, "operator-1", 24)
    assert approved.status == RuleProposalStatus.APPROVED
    assert len(approved.development_approval_ids) == triage.RULE_THRESHOLD
    for event_id in approved.source_event_ids:
        approval = store.repo.list_development_approvals(event_id)[0]
        assert approval.approved_uses == ["camera_rule"]
    assert store.feed_note("KAM-1")
    store.observe(_incident(incident_id="sonraki", risk="dusuk"))
    assert store.snapshot()["pending"] == []
    assert store.snapshot()["auto_dismissed"] == 1
    event_id = store.ids[("KAM-1", "sonraki")]
    assert store.repo.list_reviews(event_id)[0].reviewer.startswith("approved-rule:")


@pytest.mark.parametrize(
    "category", ["kavga", "saldiri", "silahli_olay", "yangin", "patlama", "arac_kazasi"]
)
def test_critical_categories_never_create_rule_proposal(store, category):
    for index in range(4):
        event = _incident(incident_id=f"critical-{index}", anomaly_type=category, risk="dusuk")
        store.observe(event)
        store.decide(f"KAM-1:critical-{index}", "sorun_degil")
    assert store.repo.list_rule_proposals() == []


def test_high_risk_dismissal_never_trains_suppression(store):
    for index in range(4):
        event = _incident(incident_id=f"high-{index}", risk="yuksek")
        store.observe(event)
        store.decide(f"KAM-1:high-{index}", "sorun_degil")
    assert store.repo.list_rule_proposals() == []


def test_rule_can_be_revoked_and_future_event_requeues(store):
    proposal = _propose(store)
    approved = store.approve_rule(proposal.proposal_id, "operator-1")
    store.revoke_rule(proposal.proposal_id, "operator-1")
    for event_id in approved.source_event_ids:
        assert store.repo.list_development_approvals(event_id)[-1].status == "revoked"
    store.observe(_incident(incident_id="yeni", risk="dusuk"))
    assert len(store.snapshot()["pending"]) == 1
    assert store.feed_note("KAM-1") == ""


def test_true_anomaly_revokes_collecting_scope(store):
    _dismiss(store, "first")
    _dismiss(store, "second")
    store.observe(_incident(incident_id="gercek"))
    store.decide("KAM-1:gercek", "anomali", category="vandalizm")
    proposal = store.repo.list_rule_proposals()[0]
    assert proposal.status == RuleProposalStatus.REVOKED


def test_expired_rule_stops_automatic_suppression():
    now = datetime(2026, 8, 16, 10, tzinfo=UTC)
    current = [now]
    store = CanonicalTriageStore(clock=lambda: current[0])
    proposal = _propose(store)
    store.approve_rule(proposal.proposal_id, "operator-1", 1)
    current[0] = now + timedelta(hours=2)
    store.observe(_incident(incident_id="gec", risk="dusuk"))
    assert len(store.snapshot()["pending"]) == 1
    assert store.repo.get_rule_proposal(proposal.proposal_id).status == RuleProposalStatus.EXPIRED


def test_pending_is_budgeted(store):
    for index in range(triage.MAX_PENDING + 20):
        store.observe(_incident(feed=f"KAM-{index}", incident_id=f"inc-{index}"))
    snapshot = store.snapshot()
    assert len(snapshot["pending"]) == triage.MAX_PENDING
    assert snapshot["queue_overflow_count"] == 20


def test_queue_overflow_removes_lowest_score_not_critical(monkeypatch):
    monkeypatch.setattr(triage, "MAX_PENDING", 2)
    store = CanonicalTriageStore()
    store.observe(
        _incident(incident_id="low-1", risk="dusuk", phase="sonuclandi")
    )
    store.observe(
        _incident(incident_id="low-2", risk="orta", phase="sonuclandi")
    )
    store.observe(
        _incident(
            incident_id="critical",
            risk="kritik",
            anomaly_type="silahli_olay",
        )
    )

    snapshot = store.snapshot()

    assert len(snapshot["pending"]) == 2
    assert {item["incident_id"] for item in snapshot["pending"]} == {
        "low-2",
        "critical",
    }
    assert snapshot["queue_overflow_count"] == 1


def test_queue_can_temporarily_exceed_budget_to_keep_critical_events(monkeypatch):
    monkeypatch.setattr(triage, "MAX_PENDING", 2)
    store = CanonicalTriageStore()
    for index in range(3):
        store.observe(
            _incident(
                incident_id=f"critical-{index}",
                risk="kritik",
                anomaly_type="yangin",
            )
        )

    snapshot = store.snapshot()

    assert len(snapshot["pending"]) == 3
    assert snapshot["critical_overflow_count"] == 1
    assert snapshot["queue_overflow_count"] == 0
