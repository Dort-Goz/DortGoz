from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..events import IncidentUpdate, RunStatus
from .runtime_postprocess import RuntimeValidationStatus, RuntimeWindowValidation

Clock = Callable[[], float]


@dataclass(slots=True)
class CanonicalRunMetrics:

    run_id: str
    clock: Clock = field(default=time.monotonic, repr=False)
    windows_seen: int = 0
    windows_screened: int = 0
    windows_skipped_before_vlm: int = 0
    siglip_calls: int = 0
    siglip_total_ms: float = 0.0
    dfine_calls: int = 0
    dfine_total_ms: float = 0.0
    dfine_rescue_count: int = 0
    keyframes_selected_total: int = 0
    qwen_calls: int = 0
    qwen_total_ms: float = 0.0
    second_pass_calls: int = 0
    second_pass_total_ms: float = 0.0
    evidence_validation_count: int = 0
    evidence_valid_count: int = 0
    evidence_invalid_count: int = 0
    evidence_human_review_count: int = 0
    evidence_undetermined_count: int = 0
    incidents_created: int = 0
    incidents_updated: int = 0
    terminal_status: str = "interrupted"
    _started_at: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_at = self.clock()

    @contextmanager
    def siglip_call(self) -> Iterator[None]:
        self.siglip_calls += 1
        started = self.clock()
        try:
            yield
        finally:
            self.siglip_total_ms += _elapsed_ms(self.clock(), started)

    @contextmanager
    def dfine_call(self) -> Iterator[None]:
        self.dfine_calls += 1
        started = self.clock()
        try:
            yield
        finally:
            self.dfine_total_ms += _elapsed_ms(self.clock(), started)

    @contextmanager
    def second_pass_call(self) -> Iterator[None]:
        self.second_pass_calls += 1
        started = self.clock()
        try:
            yield
        finally:
            self.second_pass_total_ms += _elapsed_ms(self.clock(), started)

    def record_qwen_timing(self, timing: dict[str, float | int]) -> None:

        self.qwen_calls += int(timing.get("calls", 0))
        self.qwen_total_ms += float(timing.get("total_ms", 0.0))

    def record_validation(self, validation: RuntimeWindowValidation | None) -> None:
        if validation is None:
            return
        for event in validation.events:
            self.evidence_validation_count += 1
            if event.status == RuntimeValidationStatus.VALIDATED:
                self.evidence_valid_count += 1
            elif event.status == RuntimeValidationStatus.INVALID_EVIDENCE:
                self.evidence_invalid_count += 1
            elif event.status == RuntimeValidationStatus.HUMAN_REVIEW:
                self.evidence_human_review_count += 1
            elif event.status == RuntimeValidationStatus.UNDETERMINED:
                self.evidence_undetermined_count += 1

    def observe_emitted(self, payload: object) -> None:

        if isinstance(payload, IncidentUpdate):
            if payload.phase == "basladi":
                self.incidents_created += 1
            else:
                self.incidents_updated += 1
        elif isinstance(payload, RunStatus):
            terminal = {
                "done": "completed",
                "error": "failed",
                "idle": "cancelled",
            }.get(payload.state)
            if terminal is not None:
                self.terminal_status = terminal

    def to_payload(self) -> dict[str, object]:

        return {
            "type": "run_metrics",
            "run_id": self.run_id,
            "total_runtime_ms": _rounded(_elapsed_ms(self.clock(), self._started_at)),
            "windows_seen": self.windows_seen,
            "windows_screened": self.windows_screened,
            "windows_skipped_before_vlm": self.windows_skipped_before_vlm,
            "siglip_calls": self.siglip_calls,
            "siglip_total_ms": _rounded(self.siglip_total_ms),
            "dfine_calls": self.dfine_calls,
            "dfine_total_ms": _rounded(self.dfine_total_ms),
            "dfine_rescue_count": self.dfine_rescue_count,
            "keyframes_selected_total": self.keyframes_selected_total,
            "qwen_calls": self.qwen_calls,
            "qwen_total_ms": _rounded(self.qwen_total_ms),
            "second_pass_calls": self.second_pass_calls,
            "second_pass_total_ms": _rounded(self.second_pass_total_ms),
            "evidence_validation_count": self.evidence_validation_count,
            "evidence_valid_count": self.evidence_valid_count,
            "evidence_invalid_count": self.evidence_invalid_count,
            "evidence_human_review_count": self.evidence_human_review_count,
            "evidence_undetermined_count": self.evidence_undetermined_count,
            "incidents_created": self.incidents_created,
            "incidents_updated": self.incidents_updated,
            "terminal_status": self.terminal_status,
        }


def _elapsed_ms(now: float, started: float) -> float:
    return max(0.0, (now - started) * 1000.0)


def _rounded(value: float) -> float:
    return round(value, 3)


__all__ = ["CanonicalRunMetrics"]
