"""İnsan onaylı mock aktüatör isteklerinin süreç içi kayıt defteri."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from uuid import uuid4

from ..events import ActuatorRequest, ActuatorResult


class ActuatorRequestState(StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"


@dataclass(slots=True)
class ActuatorRecord:
    request: ActuatorRequest
    state: ActuatorRequestState
    requested_at: datetime
    result: ActuatorResult | None = None
    resolved_at: datetime | None = None


class ActuatorApprovalRegistry:
    """Bir aktüatörü yalnız kayıtlı operatör kararından sonra mock olarak uygula."""

    def __init__(self) -> None:
        self._records: dict[str, ActuatorRecord] = {}
        self._lock = RLock()

    def request(
        self,
        actuator: str,
        reason: str,
        incident_id: str | None,
    ) -> ActuatorRequest:
        with self._lock:
            request_id = self._new_id()
            request = ActuatorRequest(
                request_id=request_id,
                actuator=actuator,
                reason=reason,
                incident_id=incident_id,
            )
            self._records[request_id] = ActuatorRecord(
                request=request,
                state=ActuatorRequestState.PENDING,
                requested_at=datetime.now(UTC),
            )
            return request

    def resolve(self, request_id: str, approved: bool) -> ActuatorResult:
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                raise KeyError(f"bekleyen aktüatör isteği bulunamadı: {request_id}")
            if record.result is not None:
                if record.result.approved != approved:
                    raise ValueError("aktüatör isteği için çelişkili ikinci karar reddedildi")
                return record.result

            actuator = record.request.actuator
            detail = (
                f"Operatör onayladı; {actuator} mock aktüatörü uygulandı."
                if approved
                else f"Operatör reddetti; {actuator} çalıştırılmadı."
            )
            result = ActuatorResult(
                request_id=request_id,
                actuator=actuator,
                approved=approved,
                detail=detail,
            )
            record.result = result
            record.resolved_at = datetime.now(UTC)
            record.state = (
                ActuatorRequestState.EXECUTED
                if approved
                else ActuatorRequestState.REJECTED
            )
            return result

    def status_text(self, request_id: str) -> str:
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return f"HATA: aktüatör isteği bulunamadı: {request_id}"
            if record.state == ActuatorRequestState.PENDING:
                return (
                    f"{request_id}: {record.request.actuator} operatör kararı bekliyor; "
                    "henüz çalıştırılmadı."
                )
            assert record.result is not None
            return f"{request_id}: {record.result.detail}"

    def briefing(self, *, limit: int = 20) -> str:
        with self._lock:
            records = list(self._records.values())[-limit:]
            if not records:
                return ""
            lines = ["\n\n### Güvenilir aktüatör karar defteri"]
            for record in records:
                lines.append(
                    f"- {record.request.request_id} · {record.request.actuator} · "
                    f"{record.state.value} · {record.request.reason}"
                )
            return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def _new_id(self) -> str:
        for _ in range(20):
            candidate = uuid4().hex[:12]
            if candidate not in self._records:
                return candidate
        raise RuntimeError("benzersiz aktüatör istek kimliği üretilemedi")


registry = ActuatorApprovalRegistry()


__all__ = [
    "ActuatorApprovalRegistry",
    "ActuatorRecord",
    "ActuatorRequestState",
    "registry",
]
