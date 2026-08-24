

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from ..domain.priority import InterventionPriority, intervention_band_for_score
from ..domain.taxonomy import CanonicalEventType, canonical_event_type_from_ws_label
from ..repositories.protocols import EventRepository

RULESET_VERSION = "intervention-priority-v1"

RISK_POINTS = {"dusuk": 10, "orta": 30, "yuksek": 50, "kritik": 70}
RISK_LABELS = {
    "dusuk": "Düşük etki seviyesi",
    "orta": "Orta etki seviyesi",
    "yuksek": "Yüksek etki seviyesi",
    "kritik": "Kritik etki seviyesi",
}
EVENT_POINTS = {
    CanonicalEventType.NORMAL: 0,
    CanonicalEventType.UNCERTAIN: 10,
    CanonicalEventType.UNKNOWN_ANOMALY: 10,
    CanonicalEventType.PHYSICAL_FIGHT: 15,
    CanonicalEventType.ASSAULT: 20,
    CanonicalEventType.POSSIBLE_THEFT: 10,
    CanonicalEventType.POSSIBLE_ARMED_INCIDENT: 25,
    CanonicalEventType.FIRE_SMOKE: 20,
    CanonicalEventType.EXPLOSION: 25,
    CanonicalEventType.VEHICLE_COLLISION: 18,
    CanonicalEventType.VANDALISM: 5,
}
EVENT_FLOORS = {
    CanonicalEventType.POSSIBLE_ARMED_INCIDENT: 80,
    CanonicalEventType.FIRE_SMOKE: 80,
    CanonicalEventType.EXPLOSION: 80,
    CanonicalEventType.ASSAULT: 70,
    CanonicalEventType.VEHICLE_COLLISION: 70,
    CanonicalEventType.PHYSICAL_FIGHT: 60,
}
EVENT_LABELS = {
    CanonicalEventType.PHYSICAL_FIGHT: "Fiziksel kavga bağlamı",
    CanonicalEventType.ASSAULT: "Saldırı şüphesi",
    CanonicalEventType.POSSIBLE_THEFT: "Olası hırsızlık bağlamı",
    CanonicalEventType.POSSIBLE_ARMED_INCIDENT: "Olası silahlı olay güvenlik tabanı",
    CanonicalEventType.FIRE_SMOKE: "Yangın veya duman güvenlik tabanı",
    CanonicalEventType.EXPLOSION: "Patlama güvenlik tabanı",
    CanonicalEventType.VEHICLE_COLLISION: "Araç çarpışması güvenlik tabanı",
    CanonicalEventType.VANDALISM: "Vandalizm bağlamı",
    CanonicalEventType.UNKNOWN_ANOMALY: "Sınıflandırılamayan anomali",
    CanonicalEventType.UNCERTAIN: "Belirsiz olay türü",
}


@dataclass(frozen=True, slots=True)
class PriorityScore:
    score: int
    reasons: tuple[str, ...]
    event_type: CanonicalEventType


def calculate_priority_score(
    *,
    risk: str,
    event_type: str,
    phase: str,
    needs_review: bool,
) -> PriorityScore:


    if risk not in RISK_POINTS:
        raise ValueError(f"geçersiz risk girdisi: {risk}")
    if phase not in {"basladi", "gelisiyor", "sonuclandi"}:
        raise ValueError(f"geçersiz olay fazı: {phase}")
    canonical = canonical_event_type_from_ws_label(event_type)
    points = RISK_POINTS[risk]
    reasons = [f"{RISK_LABELS[risk]}: +{RISK_POINTS[risk]}"]
    event_points = EVENT_POINTS[canonical]
    if event_points:
        points += event_points
        reasons.append(f"{EVENT_LABELS[canonical]}: +{event_points}")
    if phase in {"basladi", "gelisiyor"}:
        points += 10
        reasons.append("Olay devam ediyor: +10")
    if needs_review:
        points += 5
        reasons.append("İnsan incelemesi gerekli: +5")
    floor = EVENT_FLOORS.get(canonical)
    if floor is not None and points < floor:
        points = floor
        reasons.append(f"Kritik olay türü güvenlik tabanı: {floor}")
    score = min(100, points)
    if points > 100:
        reasons.append("Puan güvenlik tavanında sınırlandı: 100")
    return PriorityScore(score=score, reasons=tuple(reasons), event_type=canonical)


class InterventionPriorityService:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

    def assess_and_save(
        self,
        event_id: str,
        *,
        risk: str,
        event_type: str,
        phase: str,
        needs_review: bool,
    ) -> InterventionPriority:
        event = self.repository.get_event(event_id)
        if event is None:
            raise ValueError(f"priority için event bulunamadı: {event_id}")
        result = calculate_priority_score(
            risk=risk,
            event_type=event_type,
            phase=phase,
            needs_review=needs_review,
        )
        current = self.repository.get_intervention_priority_for_event(event_id)
        significant = {
            "event_revision": event.revision,
            "score": result.score,
            "band": intervention_band_for_score(result.score),
            "reasons": list(result.reasons),
            "risk_input": risk,
            "event_type_input": result.event_type.value,
            "phase_input": phase,
            "needs_review_input": needs_review,
            "model_confidence": event.confidence,
            "ruleset_version": RULESET_VERSION,
        }
        if current is not None and all(
            getattr(current, field) == value for field, value in significant.items()
        ):
            return current
        now = datetime.now(UTC)
        priority = InterventionPriority(
            priority_id=str(uuid5(NAMESPACE_URL, f"dortgoz-priority:{event_id}")),
            event_id=event_id,
            analysis_id=event.analysis_id,
            created_at=current.created_at if current is not None else now,
            calculated_at=now,
            revision=current.revision + 1 if current is not None else 1,
            **significant,
        )
        return self.repository.save_intervention_priority(priority)


__all__ = [
    "RULESET_VERSION",
    "InterventionPriorityService",
    "PriorityScore",
    "calculate_priority_score",
]
