

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..domain.event import VerifiedEvent
from ..domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
)
from ..domain.learning import (
    DriftMetric,
    DriftSnapshot,
    DriftState,
    LearningBand,
    LearningCandidateSummary,
    LearningOrchestratorOverview,
    LearningPlan,
    LearningRoute,
    LearningRouteItem,
    LearningRouteQueue,
    LearningRouteSummary,
    LearningValueComponents,
)
from ..domain.provenance import HumanReview, ReviewDecision
from ..errors import RepositoryNotFoundError
from ..repositories.protocols import EventRepository

MINIMUM_DRIFT_REVIEWS = 8

_DOWNSTREAM = {
    DevelopmentUse.CAMERA_RULE: "süreli kamera kuralı önerisi",
    DevelopmentUse.PROMPT_EXAMPLE: "onaylı istem örneği havuzu",
    DevelopmentUse.THRESHOLD_CALIBRATION: "gölge eşik kalibrasyonu",
    DevelopmentUse.SIGLIP_TRAINING: "çevrimdışı SigLIP aday havuzu",
    DevelopmentUse.D_FINE_TRAINING: "insan kutu doğrulamalı D-FINE havuzu",
    DevelopmentUse.EVALUATION: "sabit değerlendirme ve gölge koşu havuzu",
}

_GATES = {
    DevelopmentUse.CAMERA_RULE: "ayrı kural onayı, süre sonu ve kritik sınıf tabanı",
    DevelopmentUse.PROMPT_EXAMPLE: "kanıt doğrulaması ve ayrı geliştirme izni",
    DevelopmentUse.THRESHOLD_CALIBRATION: "iki sınıfta asgari örnek ve gölge ölçüm",
    DevelopmentUse.SIGLIP_TRAINING: "lisanslı veri, çevrimdışı eğitim ve sabit eval",
    DevelopmentUse.D_FINE_TRAINING: "kare kutu doğrulaması, sabit eval ve üç gölge koşu",
    DevelopmentUse.EVALUATION: "değişmez veri parmak izi ve eğitimden ayrı split",
}

_CRITICAL_RULE_TYPES = frozenset({
    "physical_fight",
    "assault",
    "possible_armed_incident",
    "fire_smoke",
    "explosion",
    "vehicle_collision",
})

_BLOCKER_LABELS = {
    "review_required": "İnsan incelemesi gerekli",
    "approval_required": "Geliştirme kullanımı onayı gerekli",
    "not_approved": "Bu kullanım için açık izin yok",
    "rejected": "Geliştirme kullanımı reddedildi",
    "revoked": "Geliştirme kullanımı geri alındı",
    "stale": "Yeni inceleme nedeniyle izin yenilenmeli",
}


@dataclass(frozen=True)
class _PlanningContext:
    reviews: dict[str, HumanReview | None]
    approvals: dict[str, DevelopmentApproval | None]
    categories: dict[str, str]
    category_counts: Counter[str]
    covered_category_counts: Counter[str]
    video_category_counts: Counter[tuple[str, str]]


def learning_band_for_score(score: int) -> LearningBand:
    if score >= 75:
        return LearningBand.PRIORITY
    if score >= 55:
        return LearningBand.HIGH
    if score >= 30:
        return LearningBand.MEDIUM
    return LearningBand.LOW


class LearningOrchestrator:


    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def drift_snapshot(self) -> DriftSnapshot:
        reviewed = self._reviewed_events()
        total = len(reviewed)
        if total < MINIMUM_DRIFT_REVIEWS:
            return DriftSnapshot(
                state=DriftState.INSUFFICIENT_DATA,
                score=0,
                reviewed_events=total,
                baseline_size=total // 2,
                current_size=total - total // 2,
                minimum_required=MINIMUM_DRIFT_REVIEWS,
            )

        midpoint = total // 2
        baseline = reviewed[:midpoint]
        current = reviewed[midpoint:]
        metrics = [
            self._rate_metric(
                "operator_rejection_rate",
                baseline,
                current,
                lambda pair: pair[1].decision == ReviewDecision.REJECT,
                35,
                "Operatör ret oranındaki değişim",
            ),
            self._category_metric(baseline, current),
            self._confidence_metric(baseline, current),
            self._rate_metric(
                "uncertainty_rate",
                baseline,
                current,
                lambda pair: bool(pair[0].uncertainties),
                20,
                "Model belirsizliği oranındaki değişim",
            ),
        ]
        score = min(100, sum(metric.points for metric in metrics))
        state = (
            DriftState.DRIFT
            if score >= 50
            else DriftState.WATCH
            if score >= 25
            else DriftState.STABLE
        )
        return DriftSnapshot(
            state=state,
            score=score,
            reviewed_events=total,
            baseline_size=len(baseline),
            current_size=len(current),
            minimum_required=MINIMUM_DRIFT_REVIEWS,
            metrics=metrics,
        )

    def plan(self, event_id: str) -> LearningPlan:
        event = self.repository.get_event(event_id)
        if event is None:
            raise RepositoryNotFoundError(f"event bulunamadı: {event_id}")
        events = self.repository.list_all_events()
        context = self._planning_context(events)
        return self._plan(event, self.drift_snapshot(), context)

    def route_queue(self, use: DevelopmentUse) -> LearningRouteQueue:
        events = self.repository.list_all_events()
        drift = self.drift_snapshot()
        context = self._planning_context(events)
        items: list[LearningRouteItem] = []
        for event in events:
            review = context.reviews[event.event_id]
            approval = context.approvals[event.event_id]
            if review is None or approval is None:
                continue
            plan = self._plan(event, drift, context)
            route = next(item for item in plan.routes if item.use == use)
            if not route.ready:
                continue
            items.append(
                LearningRouteItem(
                    event_id=event.event_id,
                    event_revision=event.revision,
                    review_id=review.review_id,
                    approval_id=approval.approval_id,
                    use=use,
                    learning_score=plan.learning_score,
                    learning_band=plan.learning_band,
                    downstream=route.downstream,
                )
            )
        items.sort(key=lambda item: (-item.learning_score, item.event_id))
        return LearningRouteQueue(use=use, items=items, count=len(items))

    def overview(self, *, candidate_limit: int = 12) -> LearningOrchestratorOverview:

        if candidate_limit < 1 or candidate_limit > 100:
            raise ValueError("candidate_limit 1 ile 100 arasında olmalıdır")
        events = self.repository.list_all_events()
        drift = self.drift_snapshot()
        context = self._planning_context(events)
        planned = [(event, self._plan(event, drift, context)) for event in events]
        route_summaries: list[LearningRouteSummary] = []
        for use in DevelopmentUse:
            routes = [
                next(route for route in plan.routes if route.use == use)
                for _, plan in planned
            ]
            recommended_count = sum(route.recommended for route in routes)
            ready_count = sum(route.ready for route in routes)
            route_summaries.append(
                LearningRouteSummary(
                    use=use,
                    recommended_count=recommended_count,
                    ready_count=ready_count,
                    awaiting_gate_count=recommended_count - ready_count,
                    downstream=_DOWNSTREAM[use],
                    safety_gate=_GATES[use],
                )
            )

        pending_review = sum(plan.latest_review_id is None for _, plan in planned)
        pending_approval = sum(
            any(
                route.recommended
                and route.approval_state in {"approval_required", "not_approved"}
                for route in plan.routes
            )
            for _, plan in planned
        )
        stale_approval = sum(
            any(route.recommended and route.approval_state == "stale" for route in plan.routes)
            for _, plan in planned
        )
        candidates = sorted(
            planned,
            key=lambda item: (-item[1].learning_score, item[0].event_id),
        )[:candidate_limit]
        priority_candidates = [
            self._candidate_summary(event, plan, context.reviews[event.event_id])
            for event, plan in candidates
        ]
        return LearningOrchestratorOverview(
            total_events=len(events),
            reviewed_events=len(events) - pending_review,
            pending_review_events=pending_review,
            pending_approval_events=pending_approval,
            stale_approval_events=stale_approval,
            ready_routes=sum(summary.ready_count for summary in route_summaries),
            route_summaries=route_summaries,
            priority_candidates=priority_candidates,
            drift=drift,
        )

    def _plan(
        self,
        event: VerifiedEvent,
        drift: DriftSnapshot,
        context: _PlanningContext,
    ) -> LearningPlan:
        review = context.reviews[event.event_id]
        approval = context.approvals[event.event_id]
        category = context.categories[event.event_id]
        same_category = context.category_counts[category]
        covered = context.covered_category_counts[category]
        same_video = context.video_category_counts[(event.video_id, category)]
        uncertainty = self._uncertainty(event)
        disagreement = self._disagreement(review)
        novelty = max(0, 100 - max(0, same_category - 1) * 12)
        coverage_gap = max(0, 100 - covered * 20)
        redundancy = min(100, max(0, same_video - 1) * 25)
        annotation_cost = self._annotation_cost(event)
        components = LearningValueComponents(
            uncertainty=uncertainty,
            disagreement=disagreement,
            novelty=novelty,
            drift=drift.score,
            coverage_gap=coverage_gap,
            redundancy=redundancy,
            annotation_cost=annotation_cost,
        )
        score = round(
            0.24 * uncertainty
            + 0.22 * disagreement
            + 0.18 * novelty
            + 0.16 * drift.score
            + 0.20 * coverage_gap
            - 0.10 * redundancy
            - 0.08 * annotation_cost
        )
        score = min(100, max(0, score))
        priority = self.repository.get_intervention_priority_for_event(event.event_id)
        reasons = self._reasons(components)
        return LearningPlan(
            event_id=event.event_id,
            event_revision=event.revision,
            latest_review_id=review.review_id if review is not None else None,
            learning_score=score,
            learning_band=learning_band_for_score(score),
            components=components,
            reasons=reasons,
            intervention_score=priority.score if priority is not None else None,
            intervention_band=priority.band.value if priority is not None else None,
            drift_state=drift.state,
            routes=self._routes(event, review, approval, coverage_gap),
        )

    def _candidate_summary(
        self,
        event: VerifiedEvent,
        plan: LearningPlan,
        review: HumanReview | None,
    ) -> LearningCandidateSummary:
        recommended = [route for route in plan.routes if route.recommended]
        blocker_states = {
            route.approval_state
            for route in recommended
            if not route.ready and route.approval_state != "approved"
        }
        if review is None:
            blocker_states.add("review_required")
        return LearningCandidateSummary(
            event_id=event.event_id,
            event_type=self._category(event, review),
            video_id=event.video_id,
            learning_score=plan.learning_score,
            learning_band=plan.learning_band,
            intervention_score=plan.intervention_score,
            recommended_uses=[route.use for route in recommended],
            ready_uses=[route.use for route in recommended if route.ready],
            blockers=[_BLOCKER_LABELS[state] for state in sorted(blocker_states)],
        )

    def _routes(
        self,
        event: VerifiedEvent,
        review: HumanReview | None,
        approval: DevelopmentApproval | None,
        coverage_gap: int,
    ) -> list[LearningRoute]:
        media = self.repository.get_incident_media_for_event(event.event_id)
        recommended = {
            DevelopmentUse.EVALUATION: review is not None,
            DevelopmentUse.THRESHOLD_CALIBRATION: (
                review is not None and event.confidence is not None
            ),
            DevelopmentUse.PROMPT_EXAMPLE: bool(
                review is not None
                and review.decision in {ReviewDecision.CONFIRM, ReviewDecision.EDIT}
                and event.evidence
                and self._category(event, review) not in {"normal", "uncertain"}
            ),
            DevelopmentUse.SIGLIP_TRAINING: bool(
                review is not None
                and media is not None
                and (
                    review.decision == ReviewDecision.EDIT
                    or bool(event.uncertainties)
                    or coverage_gap >= 50
                )
            ),
            DevelopmentUse.D_FINE_TRAINING: bool(
                review is not None
                and media is not None
                and all(
                    value is not None
                    for value in (event.start_time, event.peak_time, event.end_time)
                )
            ),
            DevelopmentUse.CAMERA_RULE: self._camera_rule_candidate(event, review),
        }
        reasons = {
            DevelopmentUse.EVALUATION: "İnsan kararı sabit değerlendirme örneği sağlar.",
            DevelopmentUse.THRESHOLD_CALIBRATION: (
                "Model skoru ile insan kararı birlikte kalibrasyon etiketi sağlar."
            ),
            DevelopmentUse.PROMPT_EXAMPLE: (
                "Doğrulanmış olay ve kanıt, istem örneği için uygundur."
            ),
            DevelopmentUse.SIGLIP_TRAINING: (
                "Sınıf düzeltmesi, belirsizlik veya kapsama açığı anlamsal örnek değeri taşır."
            ),
            DevelopmentUse.D_FINE_TRAINING: (
                "Yerel olay medyası ve doğrulanmış zamanlar kare incelemesine uygundur."
            ),
            DevelopmentUse.CAMERA_RULE: (
                "Düşük riskli olağan hareket, yalnız süreli kural önerisine adaydır."
            ),
        }
        return [
            self._route(review, approval, use, recommended[use], reasons[use])
            for use in DevelopmentUse
        ]

    def _route(
        self,
        review: HumanReview | None,
        approval: DevelopmentApproval | None,
        use: DevelopmentUse,
        recommended: bool,
        reason: str,
    ) -> LearningRoute:
        if review is None:
            state = "review_required"
        elif approval is None:
            state = "approval_required"
        elif approval.review_id != review.review_id:
            state = "stale"
        elif approval.status == DevelopmentApprovalStatus.REJECTED:
            state = "rejected"
        elif approval.status == DevelopmentApprovalStatus.REVOKED:
            state = "revoked"
        elif use in approval.approved_uses:
            state = "approved"
        else:
            state = "not_approved"
        ready = bool(recommended and state == "approved")
        return LearningRoute(
            use=use,
            recommended=recommended,
            approval_state=state,
            ready=ready,
            downstream=_DOWNSTREAM[use],
            reason=reason if recommended else "Bu olay bu kullanım için öncelikli değil.",
            safety_gate=_GATES[use],
        )

    def _planning_context(self, events: list[VerifiedEvent]) -> _PlanningContext:
        reviews = {event.event_id: self._latest_review(event) for event in events}
        approvals = {
            event.event_id: self._latest_approval(event.event_id)
            for event in events
        }
        categories = {
            event.event_id: self._category(event, reviews[event.event_id])
            for event in events
        }
        category_counts: Counter[str] = Counter(categories.values())
        covered_category_counts: Counter[str] = Counter()
        video_category_counts: Counter[tuple[str, str]] = Counter()
        for event in events:
            category = categories[event.event_id]
            video_category_counts[(event.video_id, category)] += 1
            approval = approvals[event.event_id]
            review = reviews[event.event_id]
            if (
                review is not None
                and approval is not None
                and approval.review_id == review.review_id
                and approval.status == DevelopmentApprovalStatus.APPROVED
                and approval.approved_uses
            ):
                covered_category_counts[category] += 1
        return _PlanningContext(
            reviews=reviews,
            approvals=approvals,
            categories=categories,
            category_counts=category_counts,
            covered_category_counts=covered_category_counts,
            video_category_counts=video_category_counts,
        )

    def _reviewed_events(self) -> list[tuple[VerifiedEvent, HumanReview]]:
        reviewed = [
            (event, review)
            for event in self.repository.list_all_events()
            if (review := self._latest_review(event)) is not None
        ]
        return sorted(reviewed, key=lambda pair: (pair[1].created_at, pair[0].event_id))

    def _latest_review(self, event: VerifiedEvent) -> HumanReview | None:
        reviews = self.repository.list_reviews(event.event_id)
        return reviews[-1] if reviews else None

    def _latest_approval(self, event_id: str) -> DevelopmentApproval | None:
        approvals = self.repository.list_development_approvals(event_id)
        return approvals[-1] if approvals else None

    @staticmethod
    def _category(event: VerifiedEvent, review: HumanReview | None) -> str:
        return review.event_type if review is not None and review.event_type else event.event_type.value

    @staticmethod
    def _uncertainty(event: VerifiedEvent) -> int:
        if event.confidence is None:
            ambiguity = 50
        else:
            ambiguity = round((1.0 - abs(event.confidence - 0.5) * 2.0) * 70)
        return min(100, max(0, ambiguity) + min(30, len(event.uncertainties) * 15))

    @staticmethod
    def _disagreement(review: HumanReview | None) -> int:
        if review is None:
            return 0
        if review.decision == ReviewDecision.EDIT:
            return 100
        if review.decision == ReviewDecision.REJECT:
            return 90
        return 10

    @staticmethod
    def _annotation_cost(event: VerifiedEvent) -> int:
        span = (
            max(0.0, event.end_time - event.start_time)
            if event.start_time is not None and event.end_time is not None
            else 30.0
        )
        return min(100, round(min(60.0, span * 2.0) + min(40, len(event.evidence) * 8)))

    @staticmethod
    def _reasons(components: LearningValueComponents) -> list[str]:
        positive = [
            (components.uncertainty, "model belirsizliği"),
            (components.disagreement, "insan-model uyuşmazlığı"),
            (components.novelty, "nadir olay örüntüsü"),
            (components.drift, "veri kayması sinyali"),
            (components.coverage_gap, "geliştirme kapsama açığı"),
        ]
        reasons = [
            f"{label}: {value}/100"
            for value, label in sorted(positive, reverse=True)[:3]
        ]
        if components.redundancy:
            reasons.append(f"tekrar cezası: -{components.redundancy}/100")
        if components.annotation_cost:
            reasons.append(f"etiketleme maliyeti: -{components.annotation_cost}/100")
        return reasons

    @staticmethod
    def _camera_rule_candidate(
        event: VerifiedEvent,
        review: HumanReview | None,
    ) -> bool:
        if review is None or review.decision != ReviewDecision.REJECT:
            return False
        risk = event.risk.level.value if event.risk is not None else "undetermined"
        return bool(
            review.false_alarm_reason == FalseAlarmReason.NORMAL_ACTIVITY
            and event.event_type.value not in _CRITICAL_RULE_TYPES
            and risk not in {"high", "critical"}
        )

    @staticmethod
    def _rate_metric(
        name: str,
        baseline: list[tuple[VerifiedEvent, HumanReview]],
        current: list[tuple[VerifiedEvent, HumanReview]],
        predicate,
        weight: int,
        detail: str,
    ) -> DriftMetric:
        old = sum(predicate(item) for item in baseline) / len(baseline)
        new = sum(predicate(item) for item in current) / len(current)
        delta = new - old
        return DriftMetric(
            name=name,
            baseline=round(old, 6),
            current=round(new, 6),
            delta=round(delta, 6),
            points=round(abs(delta) * weight),
            detail=detail,
        )

    @classmethod
    def _category_metric(
        cls,
        baseline: list[tuple[VerifiedEvent, HumanReview]],
        current: list[tuple[VerifiedEvent, HumanReview]],
    ) -> DriftMetric:
        old_counts = Counter(cls._category(*item) for item in baseline)
        new_counts = Counter(cls._category(*item) for item in current)
        labels = set(old_counts) | set(new_counts)
        distance = 0.5 * sum(
            abs(old_counts[label] / len(baseline) - new_counts[label] / len(current))
            for label in labels
        )
        return DriftMetric(
            name="category_distribution",
            baseline=0.0,
            current=round(distance, 6),
            delta=round(distance, 6),
            points=round(distance * 25),
            detail="Olay sınıfı dağılımındaki toplam değişim",
        )

    @staticmethod
    def _confidence_metric(
        baseline: list[tuple[VerifiedEvent, HumanReview]],
        current: list[tuple[VerifiedEvent, HumanReview]],
    ) -> DriftMetric:
        old_values = [event.confidence for event, _ in baseline if event.confidence is not None]
        new_values = [event.confidence for event, _ in current if event.confidence is not None]
        old = sum(old_values) / len(old_values) if old_values else 0.0
        new = sum(new_values) / len(new_values) if new_values else 0.0
        delta = new - old if old_values and new_values else 0.0
        return DriftMetric(
            name="mean_model_confidence",
            baseline=round(old, 6),
            current=round(new, 6),
            delta=round(delta, 6),
            points=round(abs(delta) * 20),
            detail="Ortalama model skorundaki değişim",
        )


__all__ = ["LearningOrchestrator", "learning_band_for_score"]
