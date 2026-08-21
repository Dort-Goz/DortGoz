from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .. import session
from ..agent.memory import RISK_ORDER
from ..config import settings
from ..events import ActuatorRequest, ActuatorResult, AnomalyType, Risk
from . import triage


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    label: str
    min_risk: Risk
    allowed_types: frozenset[AnomalyType]


@dataclass(slots=True)
class ActionRecord:
    request: ActuatorRequest
    result: ActuatorResult | None = None
    artifact_path: str | None = None


CRIME_TYPES: frozenset[AnomalyType] = frozenset({
    "kavga", "saldiri", "hirsizlik", "silahli_olay", "vandalizm",
})
SERIOUS_TYPES: frozenset[AnomalyType] = frozenset({
    "kavga", "saldiri", "hirsizlik", "silahli_olay", "yangin",
    "patlama", "arac_kazasi", "vandalizm",
})
HEALTH_TYPES: frozenset[AnomalyType] = frozenset({
    "kavga", "saldiri", "silahli_olay", "yangin", "patlama", "arac_kazasi",
})

ACTION_SPECS: dict[str, ActionSpec] = {
    "emniyet_bildirimi_hazirla": ActionSpec(
        name="emniyet_bildirimi_hazirla",
        label="Emniyet bildirimi",
        min_risk="orta",
        allowed_types=CRIME_TYPES,
    ),
    "acil_saglik_bildirimi_hazirla": ActionSpec(
        name="acil_saglik_bildirimi_hazirla",
        label="Acil sağlık bildirimi",
        min_risk="yuksek",
        allowed_types=HEALTH_TYPES,
    ),
    "guvenlik_uyarisi_hazirla": ActionSpec(
        name="guvenlik_uyarisi_hazirla",
        label="Güvenlik uyarısı",
        min_risk="orta",
        allowed_types=SERIOUS_TYPES,
    ),
    "alan_guvenligi_iste": ActionSpec(
        name="alan_guvenligi_iste",
        label="Alan güvenliği talebi",
        min_risk="yuksek",
        allowed_types=SERIOUS_TYPES,
    ),
}

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _clean_text(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text[:limit]


class ActionDispatcher:
    def __init__(self, runs_dir: Path | None = None) -> None:
        self._runs_dir = runs_dir
        self._records: dict[str, ActionRecord] = {}
        self._loaded_path: Path | None = None
        self._lock = RLock()

    def request(
        self,
        action: str,
        incident_id: str,
        feed: str,
        reason: str,
    ) -> tuple[ActuatorRequest, bool]:
        with self._lock:
            self._ensure_loaded()
            spec = self._spec(action)
            ctx, incident = self._incident(feed, incident_id)
            decision = triage.store.decision_for(ctx.feed, incident_id)
            if decision is not None and decision.verdict == "sorun_degil":
                raise ValueError("operatör bu olayı sorun değil olarak işaretledi")
            if incident.needs_review and (
                decision is None or decision.verdict != "anomali"
            ):
                raise ValueError("olay insan incelemesi bekliyor")
            anomaly_type = (
                decision.operator_category
                if decision is not None and decision.operator_category
                else incident.anomaly_type
            )
            if anomaly_type not in spec.allowed_types:
                raise ValueError(
                    f"{spec.label.lower()} bu olay türü için uygun değil: {anomaly_type}"
                )
            if RISK_ORDER.index(incident.risk) < RISK_ORDER.index(spec.min_risk):
                raise ValueError(
                    f"{spec.label.lower()} için risk en az {spec.min_risk} olmalı"
                )
            evidence = sorted({round(t, 3) for t in incident.evidence_ts})
            if not evidence:
                raise ValueError("olayın doğrulanmış video kanıt zamanı yok")
            for record in self._records.values():
                req = record.request
                if (
                    record.result is None
                    and req.actuator == action
                    and req.run_id == ctx.run_id
                    and req.incident_id == incident_id
                ):
                    return req, False
            requested_at = time.time()
            request = ActuatorRequest(
                request_id=self._new_id(),
                actuator=spec.name,
                action_label=spec.label,
                reason=_clean_text(reason, 500),
                incident_id=incident_id,
                incident_title=_clean_text(incident.title, 300),
                run_id=ctx.run_id,
                feed=ctx.feed,
                anomaly_type=anomaly_type,
                risk=incident.risk,
                evidence_timestamps=evidence,
                requested_at=requested_at,
            )
            record = ActionRecord(request=request)
            self._records[request.request_id] = record
            self._append(record)
            return request, True

    def register_ui_fixture(
        self, request: ActuatorRequest
    ) -> tuple[ActuatorRequest, bool]:
        """Register one explicit UI-only preview without bypassing the real incident gate."""
        with self._lock:
            self._ensure_loaded()
            self._validate_request_id(request.request_id)
            if not request.run_id.startswith("fixture-ui-"):
                raise ValueError("arayüz test isteği fixture koşusuna bağlı olmalı")
            if not request.request_id.startswith("fixture-req-"):
                raise ValueError("arayüz test isteğinin kimliği geçersiz")
            if not request.incident_id:
                raise ValueError("arayüz test isteği olay kimliği taşımalı")
            spec = self._spec(request.actuator)
            if request.anomaly_type not in spec.allowed_types:
                raise ValueError(
                    f"{spec.label.lower()} bu olay türü için uygun değil: "
                    f"{request.anomaly_type}"
                )
            if request.risk is None or (
                RISK_ORDER.index(request.risk) < RISK_ORDER.index(spec.min_risk)
            ):
                raise ValueError(
                    f"{spec.label.lower()} için risk en az {spec.min_risk} olmalı"
                )
            evidence = sorted({round(t, 3) for t in request.evidence_timestamps})
            if not evidence:
                raise ValueError("arayüz test isteğinin video kanıt zamanı yok")
            safe_request = request.model_copy(update={
                "action_label": spec.label,
                "reason": _clean_text(request.reason, 500),
                "incident_title": _clean_text(request.incident_title, 300),
                "evidence_timestamps": evidence,
                "requested_at": request.requested_at or time.time(),
            })
            existing = self._records.get(safe_request.request_id)
            if existing is not None:
                if existing.request != safe_request:
                    raise ValueError("arayüz test aksiyon kimliği başka bir isteğe ait")
                return existing.request, False
            record = ActionRecord(request=safe_request)
            self._records[safe_request.request_id] = record
            self._append(record)
            return safe_request, True

    def resolve(self, request_id: str, approved: bool, operator: str = "") -> ActuatorResult:
        with self._lock:
            self._ensure_loaded()
            self._validate_request_id(request_id)
            record = self._records.get(request_id)
            if record is None:
                raise KeyError(f"bekleyen aksiyon isteği bulunamadı: {request_id}")
            if record.result is not None:
                if record.result.approved != approved:
                    raise ValueError("aksiyon isteği için çelişkili ikinci karar reddedildi")
                return record.result
            req = record.request
            resolved_at = time.time()
            artifact_url = None
            artifact_path = None
            if approved:
                try:
                    artifact_path = self._write_artifact(req, operator, resolved_at)
                    artifact_url = f"/api/actions/{request_id}/artifact"
                    status = "prepared"
                    detail = (
                        f"{req.action_label} hazırlandı. Bu demo kapsamında dış kuruma "
                        "gönderilmedi."
                    )
                except OSError as exc:
                    status = "failed"
                    detail = f"{req.action_label} hazırlanamadı: {exc}"
            else:
                status = "rejected"
                detail = f"{req.action_label} hazırlama isteğinden vazgeçildi."
            result = ActuatorResult(
                request_id=req.request_id,
                actuator=req.actuator,
                action_label=req.action_label,
                approved=approved,
                status=status,
                detail=detail,
                incident_id=req.incident_id,
                run_id=req.run_id,
                feed=req.feed,
                artifact_url=artifact_url,
                operator=_clean_text(operator, 120),
                resolved_at=resolved_at,
            )
            record.result = result
            record.artifact_path = str(artifact_path) if artifact_path else None
            self._append(record)
            return result

    def status_text(self, request_id: str) -> str:
        with self._lock:
            self._ensure_loaded()
            record = self._records.get(request_id)
            if record is None:
                return f"HATA: aksiyon isteği bulunamadı: {request_id}"
            if record.result is None:
                return (
                    f"{record.request.action_label} operatör kararı bekliyor; "
                    "henüz hazırlanmadı ve dış kuruma gönderilmedi."
                )
            return record.result.detail

    def suggestions(self, feed: str, incident_id: str) -> list[dict[str, str | None]]:
        with self._lock:
            self._ensure_loaded()
            ctx, incident = self._incident(feed, incident_id)
            decision = triage.store.decision_for(ctx.feed, incident_id)
            if decision is None or decision.verdict != "anomali":
                return []
            anomaly_type = decision.operator_category or incident.anomaly_type
            if not incident.evidence_ts:
                return []
            suggestions: list[dict[str, str | None]] = []
            for spec in ACTION_SPECS.values():
                if anomaly_type not in spec.allowed_types:
                    continue
                if RISK_ORDER.index(incident.risk) < RISK_ORDER.index(spec.min_risk):
                    continue
                matching = next(
                    (
                        record
                        for record in reversed(list(self._records.values()))
                        if record.request.actuator == spec.name
                        and record.request.run_id == ctx.run_id
                        and record.request.incident_id == incident_id
                    ),
                    None,
                )
                suggestions.append({
                    "action": spec.name,
                    "label": spec.label,
                    "status": (
                        matching.result.status
                        if matching is not None and matching.result is not None
                        else "pending" if matching is not None else "available"
                    ),
                    "request_id": matching.request.request_id if matching is not None else None,
                })
            return suggestions

    async def create_report(self, feed: str, incident_id: str) -> tuple[Path, str]:
        ctx, incident = self._incident(feed, incident_id)
        decision = triage.store.decision_for(ctx.feed, incident_id)
        if decision is not None and decision.verdict == "sorun_degil":
            raise ValueError("operatör bu olayı sorun değil olarak işaretledi")
        if incident.needs_review and (
            decision is None or decision.verdict != "anomali"
        ):
            raise ValueError("olay insan incelemesi bekliyor")
        if not incident.evidence_ts:
            raise ValueError("olayın doğrulanmış video kanıt zamanı yok")
        if not ctx.finished:
            raise ValueError("olay raporu için video analizinin tamamlanması gerekli")
        from .analysis_package import export_with_evidence

        path = await export_with_evidence(ctx.run_id)
        return path, f"/api/runs/{ctx.run_id}/export"

    def snapshot(
        self,
        limit: int = 500,
        *,
        fixture_only: bool | None = None,
    ) -> dict[str, list[dict]]:
        with self._lock:
            self._ensure_loaded()
            records = list(self._records.values())
            if fixture_only is not None:
                records = [
                    record
                    for record in records
                    if record.request.run_id.startswith("fixture-ui-") == fixture_only
                ]
            records = records[-limit:]
            return {
                "requests": [r.request.model_dump(mode="json") for r in records],
                "results": [
                    r.result.model_dump(mode="json")
                    for r in records
                    if r.result is not None
                ],
            }

    def artifact(self, request_id: str) -> Path:
        with self._lock:
            self._ensure_loaded()
            self._validate_request_id(request_id)
            record = self._records.get(request_id)
            if (
                record is None
                or record.result is None
                or record.result.status != "prepared"
            ):
                raise KeyError(f"aksiyon çıktısı bulunamadı: {request_id}")
            path = self._root() / "aksiyonlar" / request_id / "bildirim_ozeti.md"
            if not path.is_file():
                raise FileNotFoundError(f"aksiyon çıktısı bulunamadı: {request_id}")
            return path

    def reset_memory(self) -> None:
        with self._lock:
            self._records.clear()
            self._loaded_path = None

    def _root(self) -> Path:
        return self._runs_dir or settings.runs_dir

    def _ledger_path(self) -> Path:
        return self._root() / "aksiyonlar" / "aksiyon_defteri.jsonl"

    def _ensure_loaded(self) -> None:
        path = self._ledger_path().absolute()
        if self._loaded_path == path:
            return
        self._records.clear()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                    request = ActuatorRequest.model_validate(raw["request"])
                    self._validate_request_id(request.request_id)
                except (KeyError, TypeError, ValueError):
                    continue
                result = (
                    ActuatorResult.model_validate(raw["result"])
                    if raw.get("result") is not None
                    else None
                )
                self._records[request.request_id] = ActionRecord(
                    request=request,
                    result=result,
                    artifact_path=raw.get("artifact_path"),
                )
        self._loaded_path = path

    def _append(self, record: ActionRecord) -> None:
        path = self._ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "version": 1,
            "request": record.request.model_dump(mode="json"),
            "result": record.result.model_dump(mode="json") if record.result else None,
            "artifact_path": record.artifact_path,
        }
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _write_artifact(
        self,
        request: ActuatorRequest,
        operator: str,
        resolved_at: float,
    ) -> Path:
        self._validate_request_id(request.request_id)
        out_dir = self._root() / "aksiyonlar" / request.request_id
        out_dir.mkdir(parents=True, exist_ok=False)
        payload = {
            "format": "dortgoz-action-preview",
            "version": 1,
            "request_id": request.request_id,
            "action": request.actuator,
            "action_label": request.action_label,
            "run_id": request.run_id,
            "feed": request.feed,
            "incident_id": request.incident_id,
            "incident_title": request.incident_title,
            "anomaly_type": request.anomaly_type,
            "risk": request.risk,
            "evidence_timestamps": request.evidence_timestamps,
            "reason": request.reason,
            "operator": _clean_text(operator, 120),
            "requested_at": request.requested_at,
            "resolved_at": resolved_at,
            "delivery": {
                "mode": "preview",
                "delivered": False,
                "external_side_effect": False,
            },
        }
        json_path = out_dir / "bildirim_taslagi.json"
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence = ", ".join(f"{t:.3f} sn" for t in request.evidence_timestamps)
        markdown = "\n".join([
            f"# {request.action_label} taslağı",
            "",
            "Bu bir demo taslağıdır. Dış kuruma iletilmedi.",
            "",
            f"- Kamera: {request.feed or 'ana'}",
            f"- Analiz: {request.run_id}",
            f"- Olay: {request.incident_id} · {request.incident_title}",
            f"- Tür: {request.anomaly_type}",
            f"- Risk: {request.risk}",
            f"- Kanıt zamanları: {evidence}",
            f"- Gerekçe: {request.reason or '—'}",
            f"- Operatör: {_clean_text(operator, 120) or '—'}",
            "- Teslim durumu: dış kuruma iletilmedi",
            "- Dış sistem etkisi: yok",
            "",
        ])
        markdown_path = out_dir / "bildirim_ozeti.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        return markdown_path

    @staticmethod
    def _spec(action: str) -> ActionSpec:
        try:
            return ACTION_SPECS[action]
        except KeyError as exc:
            raise ValueError(f"bilinmeyen aksiyon: {action}") from exc

    @staticmethod
    def _incident(feed: str, incident_id: str):
        if not incident_id:
            raise ValueError("aksiyon için olay kimliği gerekli")
        matches = []
        for ctx in session.all_contexts():
            if feed and ctx.feed != feed:
                continue
            incident = ctx.ledger.incidents.get(incident_id)
            if incident is not None:
                matches.append((ctx, incident))
        if not matches:
            raise ValueError(f"olay bulunamadı: {incident_id}")
        if len(matches) > 1:
            raise ValueError("olay birden fazla kamerada bulundu; kamera adı gerekli")
        return matches[0]

    def _new_id(self) -> str:
        for _ in range(20):
            candidate = uuid4().hex[:12]
            if candidate not in self._records:
                return candidate
        raise RuntimeError("benzersiz aksiyon kimliği üretilemedi")

    @staticmethod
    def _validate_request_id(request_id: str) -> None:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("aksiyon istek kimliği geçersiz")


dispatcher = ActionDispatcher()


__all__ = ["ACTION_SPECS", "ActionDispatcher", "ActionRecord", "ActionSpec", "dispatcher"]
