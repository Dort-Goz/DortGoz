"""Sürümlü, deterministik event risk değerlendirmesi."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..domain.event import EventStatus, RiskAssessment, RiskLevel, VerifiedEvent
from ..domain.evidence import VerifiedEventType


class RiskRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(min_length=1)
    event_types: list[VerifiedEventType] = Field(default_factory=list)
    level: RiskLevel
    reason: str = Field(min_length=1)
    priority: int = Field(ge=0)


class RiskRuleset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    low_confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    rules: list[RiskRule] = Field(min_length=1)


class RiskEngine:
    def __init__(self, ruleset: RiskRuleset) -> None:
        self.ruleset = ruleset

    def assess(self, event: VerifiedEvent) -> RiskAssessment:
        if event.status in {EventStatus.HUMAN_REVIEW, EventStatus.PROCESSING_FAILED}:
            return self._assessment(RiskLevel.REVIEW_REQUIRED, "RSK-REVIEW-STATUS", "Olay insan incelemesi gerektiren terminal durumdadır.", review=True)
        if event.status != EventStatus.CONFIRMED:
            return self._assessment(RiskLevel.UNDETERMINED, "RSK-NONCONFIRMED", "Doğrulanmamış olay için otomatik risk üretilmez.", review=True)
        if event.validation is None or not event.validation.permits_confirmation:
            return self._assessment(RiskLevel.REVIEW_REQUIRED, "RSK-EVIDENCE-GATE", "Evidence doğrulama kapısı riski güvenle hesaplamaya izin vermiyor.", review=True)
        if event.confidence is None or event.confidence < self.ruleset.low_confidence_threshold:
            return self._assessment(RiskLevel.UNDETERMINED, "RSK-LOW-CONFIDENCE", "Doğrulanmış olayın güveni otomatik risk eşiğinin altında.", review=True)
        matches = sorted(
            (rule for rule in self.ruleset.rules if event.event_type in rule.event_types),
            key=lambda rule: (-rule.priority, rule.rule_id),
        )
        if not matches:
            return self._assessment(RiskLevel.UNDETERMINED, "RSK-NO-RULE", "Olay türü için sürümlü risk kuralı bulunamadı.", review=True)
        rule = matches[0]
        return self._assessment(rule.level, rule.rule_id, rule.reason, review=rule.level in {RiskLevel.REVIEW_REQUIRED, RiskLevel.UNDETERMINED})

    def _assessment(self, level: RiskLevel, rule_id: str, reason: str, *, review: bool) -> RiskAssessment:
        return RiskAssessment(level=level, reasons=[reason], rule_ids=[rule_id], review_required=review, ruleset_version=self.ruleset.version)


def load_risk_ruleset(path: Path) -> RiskRuleset:
    """JSON, YAML'in geçerli alt kümesidir; ekstra parser/dependency gerekmez."""

    try:
        return RiskRuleset.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ValueError(f"risk ruleset yüklenemedi: {path}") from exc


__all__ = ["RiskEngine", "RiskRule", "RiskRuleset", "load_risk_ruleset"]
