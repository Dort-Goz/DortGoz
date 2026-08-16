"""Nöbet kuyruğu, kalıcı feedback ve kontrollü kural yaşam döngüsü."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.feedback import RuleProposalStatus
from dortgoz.domain.provenance import AnalysisProvenance, ReviewDecision
from dortgoz.domain.taxonomy import VerifiedEventType, canonical_event_type_from_ws_label
from dortgoz.domain.video import VideoMetadata
from dortgoz.events import Event, IncidentUpdate, RunStatus
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services import triage


class CanonicalTriageStore(triage.TriageStore):
    """Her test incident'ını gerçek canonical parent kayıtlarıyla hazırlar."""

    def __init__(self, clock=None) -> None:
        self.repo = InMemoryEventRepository()
        self.ids: dict[tuple[str, str], str] = {}
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


def test_lifecycle_update_refreshes_not_duplicates(store):
    store.observe(_incident(phase="basladi", risk="orta"))
    store.observe(_incident(phase="sonuclandi", risk="kritik"))
    pending = store.snapshot()["pending"]
    assert len(pending) == 1
    assert pending[0]["risk"] == "kritik"
    assert pending[0]["phase"] == "sonuclandi"


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


def test_dismissal_is_saved_and_does_not_leave_jsonl_side_channel(store):
    store.observe(_incident())
    item = store.decide("KAM-1:inc-1", "sorun_degil")
    review = store.repo.list_reviews(item.event_id)[0]
    assert review.decision == ReviewDecision.REJECT
    assert review.intervention_required is False
    assert store.snapshot()["dismissed_count"] == 1


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
    assert len(store.snapshot()["pending"]) == triage.MAX_PENDING
