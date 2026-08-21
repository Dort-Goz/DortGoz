from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..events import AnomalyType, IncidentUpdate, Risk, WindowEvent, WindowReport

RISK_ORDER: list[Risk] = ["dusuk", "orta", "yuksek", "kritik"]
ALARM_FLOOR = 1


def _rank(risk: Risk) -> int:
    return RISK_ORDER.index(risk)


@dataclass
class Incident:
    incident_id: str
    title: str
    first_seen: float
    last_seen: float
    phase: str = "basladi"
    anomaly_type: AnomalyType = "bilinmeyen"
    risk: Risk = "dusuk"
    notes: list[str] = field(default_factory=list)
    thumbnail: str | None = None
    evidence: str | None = None
    needs_review: bool = False
    review_reason: str = ""
    olay_baslangic: float | None = None
    olay_bitis: float | None = None
    evidence_ts: list[float] = field(default_factory=list)

    def not_evidence(self, events: list) -> None:
        for e in events:
            for ref in getattr(e, "evidence", []) or []:
                ts = getattr(ref, "timestamp", None)
                if isinstance(ts, int | float):
                    self.evidence_ts.append(float(ts))
        if self.evidence_ts:
            self.olay_baslangic = max(0.0, min(self.evidence_ts) - 1.0)
            self.olay_bitis = max(self.evidence_ts) + 1.0


@dataclass
class Entity:
    track_id: int
    label: str
    first_seen: float
    last_seen: float
    state: str = ""


class Ledger:

    def __init__(self, grace_windows: int = 1) -> None:
        self.incidents: dict[str, Incident] = {}
        self.entities: dict[int, Entity] = {}
        self._open_id: str | None = None
        self._grace = grace_windows
        self._quiet = 0


    @property
    def open_incident(self) -> Incident | None:
        return self.incidents.get(self._open_id) if self._open_id else None

    @property
    def quiet_streak(self) -> int:
        return self._quiet

    @property
    def grace(self) -> int:
        return self._grace

    def serious(self, report: WindowReport) -> list[WindowEvent]:
        return [e for e in report.events if _rank(e.severity_hint) >= ALARM_FLOOR]

    def continuity_hint(self) -> str:
        inc = self.open_incident
        if inc is None:
            return ""
        return (
            f"SÜREGELEN OLAY (önceki pencerelerden): {inc.title} — "
            f"{inc.first_seen:.0f}. saniyede başladı, şu ana dek {inc.risk} risk. "
            "Bu pencerede DEVAM EDİYORSA ilgili gözlemleri yine listele "
            "(aynı olayın devamıdır). BİTTİYSE ya da artık görünmüyorsa bunu "
            "açıkça yaz ve olağan olarak işaretle — süren olay yokken olay "
            "uydurma."
        )

    def apply_review(self, incident_id: str, review: dict) -> IncidentUpdate | None:
        inc = self.incidents.get(incident_id)
        if inc is None:
            return None
        was_review_required = inc.needs_review
        previous_review_reason = inc.review_reason
        inc.anomaly_type = review.get("anomaly_type", inc.anomaly_type)
        inc.title = _title_text(review.get("zirve", inc.title))
        if not inc.evidence_ts and \
                isinstance(review.get("baslangic_t"), int | float) and \
                isinstance(review.get("bitis_t"), int | float):
            inc.olay_baslangic = float(review["baslangic_t"])
            inc.olay_bitis = float(review["bitis_t"])
        unc = review.get("belirsizlikler", [])
        inc.needs_review = was_review_required or bool(unc) or inc.anomaly_type == "bilinmeyen"
        if previous_review_reason:
            inc.review_reason = previous_review_reason
        elif unc:
            inc.review_reason = f"2. geçiş: {_short(unc[0])}"
        elif inc.anomaly_type == "bilinmeyen":
            inc.review_reason = "olay kapalı sınıf listesine oturmadı"
        detail = "\n".join(filter(None, [
            f"Başlangıç: {review.get('baslangic', '')}".strip(),
            f"Zirve: {review.get('zirve', '')}".strip(),
            f"Sonuç: {review.get('sonuc', '')}".strip(),
            *(f"? {u}" for u in review.get("belirsizlikler", [])[:2]),
        ]))
        return _update(inc, review.get("zirve_t", inc.first_seen), _trim(detail))

    def require_review(
        self,
        reason: str,
        *,
        incident_id: str | None = None,
    ) -> Incident | None:

        identifier = incident_id or self._open_id
        inc = self.incidents.get(identifier) if identifier is not None else None
        if inc is None:
            return None
        inc.needs_review = True
        normalized = _short(reason.strip()) if reason.strip() else "evidence review gerekli"
        if not inc.review_reason:
            inc.review_reason = normalized
        elif normalized not in inc.review_reason:
            inc.review_reason = _short(f"{inc.review_reason} · {normalized}")
        return inc


    def ingest(self, report: WindowReport, thumbnail: str | None = None,
               uncertain: str = "", evidence: str | None = None) -> list[IncidentUpdate]:
        events = self.serious(report)
        if not events:
            if not self._open_id:
                return []
            self._quiet += 1
            if self._quiet > self._grace:
                return self._close()
            return []

        self._quiet = 0
        peak = max(events, key=lambda e: _rank(e.severity_hint))
        current = self.open_incident
        if current is None:
            upd = self._open(peak, events, report, thumbnail, evidence)
        else:
            upd = self._extend(current, peak, events, report)
        inc = self.incidents[upd.incident_id]
        self._flag_review(inc, report, uncertain)
        upd.needs_review = inc.needs_review
        upd.review_reason = inc.review_reason
        return [upd]

    def _flag_review(self, inc: Incident, report: WindowReport,
                     uncertain: str) -> None:
        reasons = []
        if uncertain:
            reasons.append(uncertain)
        if inc.anomaly_type == "bilinmeyen":
            reasons.append("olay kapalı sınıf listesine oturmadı")
        if report.uncertainties:
            reasons.append("model belirsizlik bildirdi: "
                           + _short(report.uncertainties[0]))
        if reasons and not inc.needs_review:
            inc.needs_review = True
            inc.review_reason = " · ".join(reasons[:2])

    def finalize(self) -> list[IncidentUpdate]:
        return self._close() if self._open_id else []


    def _open(self, peak: WindowEvent, events: list[WindowEvent],
              report: WindowReport, thumbnail: str | None,
              evidence: str | None = None) -> IncidentUpdate:
        inc = Incident(
            incident_id=uuid.uuid4().hex[:8],
            title=_title(peak),
            first_seen=peak.t,
            last_seen=events[-1].t,
            phase="basladi",
            anomaly_type=_classify(report),
            risk=peak.severity_hint,
            notes=[e.desc for e in events],
            thumbnail=thumbnail,
            evidence=evidence,
        )
        inc.not_evidence(events)
        self.incidents[inc.incident_id] = inc
        self._open_id = inc.incident_id
        return _update(inc, peak.t, report.summary)

    def _extend(self, inc: Incident, peak: WindowEvent, events: list[WindowEvent],
                report: WindowReport) -> IncidentUpdate:
        inc.phase = "gelisiyor"
        inc.last_seen = events[-1].t
        inc.not_evidence(events)
        inc.notes.extend(e.desc for e in events)
        if _rank(peak.severity_hint) > _rank(inc.risk):
            inc.risk = peak.severity_hint
            inc.title = _title(peak)
            inc.anomaly_type = _classify(report)
        elif inc.anomaly_type == "bilinmeyen":
            inc.anomaly_type = _classify(report)
        return _update(inc, peak.t, report.summary)

    def _close(self) -> list[IncidentUpdate]:
        inc = self.incidents[self._open_id]        # type: ignore[index]
        self._open_id = None
        self._quiet = 0
        inc.phase = "sonuclandi"
        detail = f"{len(inc.notes)} gözlem · {inc.first_seen:.0f}-{inc.last_seen:.0f} sn"
        return [_update(inc, inc.last_seen, detail)]


def _classify(report: WindowReport) -> AnomalyType:
    return "bilinmeyen" if report.anomaly_type == "normal" else report.anomaly_type


def _title(event: WindowEvent) -> str:
    return _title_text(event.desc)


_TIME_LEAD = re.compile(
    r"^(?:t\s*=\s*\d+(?:[.,]\d+)?\s*s?\s*(?:ile|-|–|ve)?\s*)+"
    r"(?:t\s*=\s*\d+(?:[.,]\d+)?\s*s?\s*)?(?:arasında|civarında|itibarıyla|de|da|'de|'da)?[\s,:-]*",
    re.IGNORECASE)


def _title_text(text: str) -> str:
    head = _TIME_LEAD.sub("", text.split(".")[0].strip()).strip()
    if head:
        head = head[0].upper() + head[1:]
    return head if len(head) <= 70 else head[:67] + "…"


def _short(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + "…"


def _trim(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(".", 0, limit)
    return (text[:cut + 1] if cut > limit // 2 else text[:limit].rstrip()) + " …"


def _update(inc: Incident, t: float, detail: str) -> IncidentUpdate:
    return IncidentUpdate(
        incident_id=inc.incident_id,
        t=t,
        phase=inc.phase,                           # type: ignore[arg-type]
        title=inc.title,
        anomaly_type=inc.anomaly_type,
        risk=inc.risk,
        detail=detail,
        thumbnail=inc.thumbnail,
        evidence=inc.evidence,
        needs_review=inc.needs_review,
        review_reason=inc.review_reason,
        olay_baslangic=inc.olay_baslangic,
        olay_bitis=inc.olay_bitis,
    )
