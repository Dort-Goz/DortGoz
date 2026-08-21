from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.event import ProcedureAction, RiskAssessment, RiskLevel, VerifiedEvent
from ..domain.provenance import ProcedureSource
from .procedure_index import LocalProcedureIndex


class ProcedureRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ProcedureAction] = Field(default_factory=list)
    sources: list[ProcedureSource] = Field(default_factory=list)
    reason: str | None = None


class ProcedureService:
    def __init__(self, index: LocalProcedureIndex) -> None:
        self.index = index

    def recommend(self, event: VerifiedEvent, risk: RiskAssessment) -> ProcedureRecommendation:
        if risk.review_required or risk.level in {RiskLevel.REVIEW_REQUIRED, RiskLevel.UNDETERMINED}:
            return ProcedureRecommendation(reason="Risk insan incelemesi gerektirdiği için prosedür önerisi üretilmedi.")
        matches = self.index.find(event.event_type, risk.level)
        if not matches:
            return ProcedureRecommendation(reason="Eşleşen sürümlü ve geçerli yerel prosedür bulunamadı.")
        actions = [ProcedureAction(priority=index + 1, action=section.action, document_id=document.document_id, section=section.section, version=document.version, content_hash=document.content_hash, requires_human_approval=True) for index, (document, section, _) in enumerate(matches)]
        return ProcedureRecommendation(actions=actions, sources=[source for _, _, source in matches])

    @staticmethod
    def recommend_runtime(risk: RiskAssessment) -> ProcedureRecommendation:

        if risk.review_required or risk.level in {
            RiskLevel.REVIEW_REQUIRED,
            RiskLevel.UNDETERMINED,
        }:
            return ProcedureRecommendation(
                reason="Runtime risk automatic prosedür üretimine izin vermiyor."
            )
        raise ValueError("runtime procedure guard yalnız fail-closed risk kabul eder")


__all__ = ["ProcedureRecommendation", "ProcedureService"]
