from __future__ import annotations

from dataclasses import dataclass

from ..domain.event import RiskAssessment
from ..domain.taxonomy import legacy_ws_label_from_canonical
from ..events import WindowReport
from .procedure_service import ProcedureRecommendation, ProcedureService
from .risk_engine import RiskEngine, RuntimeRiskDisposition
from .runtime_postprocess import RuntimeValidationStatus, RuntimeWindowValidation

_RISK_ORDER = {"dusuk": 0, "orta": 1, "yuksek": 2, "kritik": 3}


@dataclass(frozen=True, slots=True)
class RuntimePolicyDecision:

    ledger_report: WindowReport | None
    admitted_event_indices: tuple[int, ...]
    held_event_indices: tuple[int, ...]
    review_reason: str
    risk: RiskAssessment | None
    procedure: ProcedureRecommendation | None

    @property
    def observation_held(self) -> bool:
        return bool(self.held_event_indices) and self.ledger_report is None


def decide_runtime_policy(
    report: WindowReport,
    validation: RuntimeWindowValidation | None,
) -> RuntimePolicyDecision:

    if not report.events:
        return RuntimePolicyDecision(
            ledger_report=report,
            admitted_event_indices=(),
            held_event_indices=(),
            review_reason="",
            risk=None,
            procedure=None,
        )

    by_index = (
        {item.event_index: item for item in validation.events} if validation is not None else {}
    )
    admitted: list[int] = []
    held: list[int] = []
    statuses: dict[int, RuntimeValidationStatus] = {}
    for event_index in range(len(report.events)):
        item = by_index.get(event_index)
        status = item.status if item is not None else RuntimeValidationStatus.UNDETERMINED
        if status in {
            RuntimeValidationStatus.VALIDATED,
            RuntimeValidationStatus.HUMAN_REVIEW,
        }:
            if item is None or item.event_type is None:
                status = RuntimeValidationStatus.UNDETERMINED
            elif not (
                item.validation.schema_valid
                and item.validation.timestamps_valid
                and item.validation.evidence_valid
            ):
                status = RuntimeValidationStatus.INVALID_EVIDENCE
        statuses[event_index] = status
        if status in {
            RuntimeValidationStatus.VALIDATED,
            RuntimeValidationStatus.HUMAN_REVIEW,
        }:
            admitted.append(event_index)
        else:
            held.append(event_index)

    ledger_report: WindowReport | None = None
    if admitted:
        selected = [report.events[index] for index in admitted]
        peak_index = max(
            admitted,
            key=lambda index: _RISK_ORDER[report.events[index].severity_hint],
        )
        peak_validation = by_index[peak_index]
        ledger_report = report.model_copy(
            deep=True,
            update={
                "events": selected,
                "anomaly_type": legacy_ws_label_from_canonical(peak_validation.event_type).value,
            },
        )
        disposition = RuntimeRiskDisposition.PROVISIONAL_GROUNDED
    elif any(status == RuntimeValidationStatus.UNDETERMINED for status in statuses.values()):
        disposition = RuntimeRiskDisposition.UNDETERMINED
    else:
        disposition = RuntimeRiskDisposition.INVALID_EVIDENCE

    risk = RiskEngine.assess_runtime(disposition)
    procedure = ProcedureService.recommend_runtime(risk)
    return RuntimePolicyDecision(
        ledger_report=ledger_report,
        admitted_event_indices=tuple(admitted),
        held_event_indices=tuple(held),
        review_reason=_review_reason(statuses),
        risk=risk,
        procedure=procedure,
    )


def _review_reason(statuses: dict[int, RuntimeValidationStatus]) -> str:


    flagged = sorted(
        (index, status)
        for index, status in statuses.items()
        if status != RuntimeValidationStatus.VALIDATED
    )
    if not flagged:
        return ""
    details = ", ".join(f"event[{index}]={status.value}" for index, status in flagged)
    return (
        "Runtime evidence yalnız provisional kullanıma izin veriyor; "
        f"automatic confirmation kapalı ({details})."
    )


__all__ = ["RuntimePolicyDecision", "decide_runtime_policy"]
