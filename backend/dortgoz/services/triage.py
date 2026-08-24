

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..config import settings
from ..domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
    RuleProposal,
    RuleProposalStatus,
)
from ..domain.priority import intervention_band_for_score
from ..domain.provenance import HumanReview, ReviewDecision
from ..domain.taxonomy import canonical_event_type_from_ws_label
from ..events import Event, IncidentUpdate
from ..repositories.bundles import FeedbackWriteBundle
from ..repositories.protocols import EventRepository
from . import exemplar_bank
from .event_service import EventMemoryService
from .intervention_priority import (
    RULESET_VERSION,
    InterventionPriorityService,
    calculate_priority_score,
)

LOGGER = logging.getLogger(__name__)
LEDGER_VERSION = 2

CATEGORIES = [
    "kavga", "saldiri", "hirsizlik", "silahli_olay", "yangin",
    "patlama", "arac_kazasi", "vandalizm", "bilinmeyen",
]
MAX_PENDING = 200
MAX_RESOLVED = 500
RULE_THRESHOLD = 3
DEFAULT_RULE_HOURS = 24
MAX_RULE_HOURS = 24 * 30
PROTECTED_CATEGORIES = frozenset(
    {"kavga", "saldiri", "silahli_olay", "yangin", "patlama", "arac_kazasi"}
)
PROTECTED_RISKS = frozenset({"yuksek", "kritik"})
RISK = ["dusuk", "orta", "yuksek", "kritik"]

_NOTE_TR = {
    "hirsizlik": "araç ve eşya çevresindeki olağan yükleme veya bekleme hareketleri",
    "vandalizm": "yapı veya eşya yakınında çalışan ya da bekleyen kişiler",
    "bilinmeyen": "bu kameranın olağan sahne hareketleri",
}


def _config_snapshot() -> dict[str, Any]:
    return {
        "escalate_p": settings.escalate_p,
        "candidate_start_threshold": settings.candidate_start_threshold,
        "candidate_continue_threshold": settings.candidate_continue_threshold,
        "candidate_screening": settings.candidate_screening,
        "second_opinion_model": settings.second_opinion_model,
        "dual_read": settings.dual_read,
        "final_sweep": settings.final_sweep,
    }


def _run_meta(run_id: str) -> dict[str, Any]:
    if not run_id:
        return {}
    try:
        raw = (settings.runs_dir / f"{run_id}.meta.json").read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        meta = json.loads(raw)
    except ValueError:
        return {}
    out = {key: meta.get(key, "") for key in ("model", "mode", "video")}
    prompt = meta.get("system_prompt", "") or ""
    task = meta.get("task_prompt", "") or ""
    out["system_prompt_sha"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    out["task_prompt_sha"] = hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]
    return out


class TriagePersistenceError(RuntimeError):
    pass


@dataclass
class TriageItem:
    key: str
    feed: str
    incident_id: str
    t: float
    wall: float
    title: str
    model_category: str
    risk: str
    phase: str
    event_id: str | None = None
    thumbnail: str | None = None
    evidence: str | None = None
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    sample: bool = False
    emsal_benzerlik: float | None = None
    emsal_key: str = ""
    emsal_golge: bool = False
    needs_review: bool = False
    review_reason: str = ""
    run_id: str = ""
    video: str = ""
    model_start: float | None = None
    model_end: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    operator_category: str = ""
    operator_start: float | None = None
    operator_end: float | None = None
    reviewer: str = ""
    note: str = ""
    decided_wall: float | None = None
    tekrar: int = 1
    review_ids: list[str] = field(default_factory=list)
    operator_risk: str = ""
    false_alarm_reason: str = ""
    intervention_required: bool | None = None
    review_start: float | None = None
    review_peak: float | None = None
    review_end: float | None = None
    intervention_score: int = 0
    intervention_band: str = "routine"
    intervention_reasons: list[str] = field(default_factory=list)
    priority_ruleset_version: str = RULESET_VERSION
    escalation_scopes: list[str] = field(default_factory=list)
    decision_id: str = ""
    supersedes: str | None = None
    ledger_version: int = LEDGER_VERSION
    config: dict[str, Any] = field(default_factory=dict)
    run_meta: dict[str, Any] = field(default_factory=dict)


class TriageStore:
    def __init__(
        self,
        repository: EventRepository | None = None,
        event_service: EventMemoryService | None = None,
        event_id_resolver: Callable[[str, str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        allow_ledger_only: bool = False,
    ) -> None:
        self._pending: dict[str, TriageItem] = {}
        self._resolved: list[TriageItem] = []
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self.queue_overflow_count = 0
        self.expired_count = 0
        self.emsal_shadow_count = 0
        self.emsal_suppressed = 0
        self.rules: dict[tuple[str, str], int] = {}
        self._runs: dict[str, tuple[str, str]] = {}
        self.repository = repository
        self.event_service = event_service or (
            EventMemoryService(repository) if repository is not None else None
        )
        self.event_id_resolver = event_id_resolver
        self.priority_service = (
            InterventionPriorityService(repository) if repository is not None else None
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self.allow_ledger_only = allow_ledger_only
        self._matcher = exemplar_bank.Matcher(
            settings.runs_dir, settings.runs_dir / "nobet_defteri.jsonl"
        )

    def configure(
        self,
        repository: EventRepository,
        event_service: EventMemoryService,
        event_id_resolver: Callable[[str, str], str | None],
    ) -> None:


        self.repository = repository
        self.event_service = event_service
        self.event_id_resolver = event_id_resolver
        self.priority_service = InterventionPriorityService(repository)

    @staticmethod
    def _signal_dict(payload: Any) -> dict[str, Any]:
        signals = getattr(payload, "signals", None)
        return signals.model_dump() if signals is not None else {}

    def _merge_signals(self, item: TriageItem, payload: Any) -> None:
        incoming = self._signal_dict(payload)
        if not incoming:
            return
        if not item.signals:
            item.signals = incoming
            return
        previous = item.signals.get("durum_p")
        current = incoming.get("durum_p")
        if current is not None and (previous is None or current > previous):
            item.signals = incoming

    @staticmethod
    def _evidence_dicts(feed: str, incident_id: str) -> list[dict[str, Any]]:


        from .. import session

        matches = []
        for ctx in session.all_contexts():
            if feed and ctx.feed != feed:
                continue
            incident = ctx.ledger.incidents.get(incident_id)
            if incident is not None:
                matches.append(incident)
        if len(matches) != 1:
            return []
        return [
            ref.model_dump(mode="json")
            for ref in matches[0].evidence_refs
        ]

    def _merge_evidence_refs(self, item: TriageItem) -> None:
        known = {
            (entry.get("frame_id"), entry.get("timestamp"), entry.get("claim"))
            for entry in item.evidence_refs
        }
        for entry in self._evidence_dicts(item.feed, item.incident_id):
            key = (entry.get("frame_id"), entry.get("timestamp"), entry.get("claim"))
            if key not in known:
                item.evidence_refs.append(entry)
                known.add(key)
        item.evidence_refs.sort(key=lambda entry: float(entry.get("timestamp", 0.0)))

    def observe(self, event: Event) -> None:
        payload = event.payload
        kind = getattr(payload, "type", "")
        if kind == "run_status":
            if getattr(payload, "run_id", ""):
                self._runs[event.feed] = (
                    payload.run_id,
                    getattr(payload, "video", ""),
                )
            return
        if kind == "review_sample":
            self._observe_sample(event, payload)
            return
        if kind != "incident_update":
            return
        key = f"{event.feed}:{payload.incident_id}"
        event_id = self._resolve_event_id(event.feed, payload.incident_id)
        priority = self._priority_values(payload, event_id)
        if key in self._pending:
            item = self._pending[key]
            item.t, item.risk, item.phase = payload.t, payload.risk, payload.phase
            item.title = payload.title
            item.model_category = payload.anomaly_type
            item.event_id = event_id or item.event_id
            item.thumbnail = payload.thumbnail or item.thumbnail
            item.evidence = payload.evidence or item.evidence
            self._merge_evidence_refs(item)
            item.needs_review = payload.needs_review
            item.review_reason = payload.review_reason
            if payload.olay_baslangic is not None:
                item.model_start = payload.olay_baslangic
            if payload.olay_bitis is not None:
                item.model_end = payload.olay_bitis
            self._merge_signals(item, payload)
            self._apply_priority(item, priority)
            return
        resolved = next((item for item in self._resolved if item.key == key), None)
        if resolved is not None:
            item = self._reopen_escalated(resolved, payload, event_id, priority)
            if item is None:
                return
            self._pending[key] = item
            self._enforce_capacity()
            return

        rule = self._active_rule(event.feed, payload.anomaly_type)
        if (
            rule is not None
            and payload.anomaly_type not in PROTECTED_CATEGORIES
            and payload.risk not in PROTECTED_RISKS
            and event_id is not None
        ):
            review = self._save_review(
                event_id,
                ReviewDecision.REJECT,
                reviewer=f"approved-rule:{rule.proposal_id}",
                note=(
                    "Süreli operatör kuralı uygulandı. "
                    f"Kapsam: {event.feed}/{payload.anomaly_type}."
                ),
                false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
                intervention_required=False,
            )
            self._mark_rule_applied(rule)
            self.auto_dismissed += 1
            item = self._new_item(event, event_id, priority)
            item.verdict = "sorun_degil"
            item.note = f"onaylı süreli kural: {rule.proposal_id}"
            item.decided_wall = time.time()
            item.review_ids = [review.review_id]
            self._append_resolved(item)
            self._stamp_and_log(item)
            return

        match = self._emsal_check(event.feed, payload)
        item = self._new_item(event, event_id, priority, match)
        if match.suppress:
            if event_id is None:
                suffix = (
                    "Canonical olay kaydı hazır değil; emsal bastırması "
                    "güvenli biçimde uygulanmadı."
                )
                item.needs_review = True
                item.review_reason = " · ".join(
                    filter(None, [item.review_reason, suffix])
                )
                self._pending[key] = item
                self._enforce_capacity()
                return
            review = self._save_review(
                event_id,
                ReviewDecision.REJECT,
                reviewer="exemplar-bank",
                note=f"Emsal bastırması uygulandı. {match.reason}",
                false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
                intervention_required=False,
            )
            self.emsal_suppressed += 1
            self.auto_dismissed += 1
            item.verdict = "sorun_degil"
            item.note = f"otomatik: emsal bastırması — {match.reason}"
            item.decided_wall = time.time()
            item.review_ids = [review.review_id]
            self._append_resolved(item)
            self._stamp_and_log(item)
            return
        if rule is not None and event_id is None:
            suffix = "Canonical olay kaydı hazır değil; kural güvenli biçimde uygulanmadı."
            item.needs_review = True
            item.review_reason = " · ".join(filter(None, [item.review_reason, suffix]))
        self._pending[key] = item
        self._enforce_capacity()

    def _observe_sample(self, event: Event, payload: Any) -> None:
        run_id, video = self._runs.get(event.feed, ("", ""))
        key = f"{event.feed}:ornek:{payload.sample_id}"
        if key in self._pending or any(item.key == key for item in self._resolved):
            return
        self._pending[key] = TriageItem(
            key=key,
            feed=event.feed,
            incident_id=payload.sample_id,
            t=payload.t,
            wall=time.time(),
            title=payload.summary or "Denetim örneği: model olay görmedi",
            model_category="normal",
            risk="dusuk",
            phase="sonuclandi",
            thumbnail=payload.thumbnail,
            evidence=payload.evidence,
            sample=True,
            run_id=run_id,
            video=video,
            signals=self._signal_dict(payload),
            model_start=payload.window_start,
            model_end=payload.window_end,
        )

    def decide(
        self,
        key: str,
        verdict: str,
        category: str = "",
        risk_level: str | None = None,
        start_time: float | None = None,
        peak_time: float | None = None,
        end_time: float | None = None,
        false_alarm_reason: FalseAlarmReason | None = None,
        intervention_required: bool | None = None,
        note: str = "",
        reviewer: str = "operator-console",
        operator_start: float | None = None,
        operator_end: float | None = None,
    ) -> TriageItem:
        if verdict not in {"anomali", "sorun_degil"}:
            raise ValueError(f"geçersiz karar: {verdict}")
        item = self._pending.get(key)
        if item is None:
            raise KeyError(f"bekleyen kayıt yok: {key}")
        if verdict == "anomali" and category not in CATEGORIES:
            raise ValueError(f"geçersiz kategori: {category}")
        if verdict == "anomali" and false_alarm_reason is not None:
            raise ValueError("anomali kararı yanlış alarm nedeni taşıyamaz")
        if verdict == "sorun_degil" and (category or risk_level is not None):
            raise ValueError("sorun değil kararı kategori veya risk düzeyi taşıyamaz")
        if verdict == "sorun_degil" and any(
            value is not None for value in (start_time, peak_time, end_time)
        ):
            raise ValueError("sorun değil kararı olay zamanı düzeltmesi taşıyamaz")
        if false_alarm_reason == FalseAlarmReason.OTHER and not note.strip():
            raise ValueError("diğer yanlış alarm nedeni açıklama gerektirir")
        if risk_level is not None and risk_level not in {"dusuk", "orta", "yuksek", "kritik"}:
            raise ValueError(f"geçersiz risk düzeyi: {risk_level}")
        if not reviewer.strip():
            raise ValueError("reviewer boş olamaz")
        if (operator_start is None) != (operator_end is None):
            raise ValueError("operatör başlangıç ve bitiş zamanı birlikte verilmelidir")
        if (
            operator_start is not None
            and operator_end is not None
            and operator_start > operator_end
        ):
            raise ValueError("operatör başlangıcı bitişten sonra olamaz")

        if item.sample:
            self._pending.pop(key)
            item.verdict = verdict
            item.operator_category = category if verdict == "anomali" else ""
            item.operator_risk = risk_level or (item.risk if verdict == "anomali" else "")
            item.note = note[:500]
            item.reviewer = reviewer[:120]
            item.intervention_required = intervention_required
            item.review_start, item.review_peak, item.review_end = (
                start_time,
                peak_time,
                end_time,
            )
            item.operator_start = operator_start if operator_start is not None else start_time
            item.operator_end = operator_end if operator_end is not None else end_time
            item.decided_wall = time.time()
            self._append_resolved(item)
            self._stamp_and_log(item)
            return item

        event_id = item.event_id or self._resolve_event_id(item.feed, item.incident_id)
        if event_id is None or self.repository is None or self.repository.get_event(event_id) is None:
            if self.allow_ledger_only:
                return self._save_ledger_only_decision(
                    item,
                    verdict=verdict,
                    category=category,
                    risk_level=risk_level,
                    start_time=start_time,
                    peak_time=peak_time,
                    end_time=end_time,
                    note=note,
                    reviewer=reviewer,
                    operator_start=operator_start,
                    operator_end=operator_end,
                )
            raise TriagePersistenceError(
                "Olay henüz canonical SQLite kaydına bağlanamadı; karar kaydedilmedi."
            )
        item.event_id = event_id
        event = self.repository.get_event(event_id)
        assert event is not None
        if operator_start is not None and operator_end is not None:
            if any(value is not None for value in (start_time, peak_time, end_time)):
                raise ValueError(
                    "legacy ve yapılandırılmış zaman düzeltmeleri birlikte verilemez"
                )
            start_time = operator_start
            end_time = operator_end
            peak_time = min(max(event.peak_time, start_time), end_time)
        corrected_times = self._review_times(
            event_id,
            start_time=start_time,
            peak_time=peak_time,
            end_time=end_time,
            use_event_defaults=verdict == "anomali",
        )
        required = intervention_required if intervention_required is not None else verdict == "anomali"

        if verdict == "anomali":
            decision = (
                ReviewDecision.CONFIRM
                if event.validation is not None
                and event.validation.permits_confirmation
                and bool(event.evidence)
                else ReviewDecision.EDIT
            )
            review = self._save_review(
                event_id,
                decision,
                reviewer=reviewer.strip(),
                note=note.strip() or "Operatör nöbet kuyruğunda anomaliyi doğruladı.",
                event_type=canonical_event_type_from_ws_label(category).value,
                start_time=corrected_times[0],
                peak_time=corrected_times[1],
                end_time=corrected_times[2],
                risk_level=risk_level or item.risk,
                intervention_required=required,
            )
            item.operator_category = category
            item.operator_risk = risk_level or item.risk
            self._cancel_scope(item.feed, item.model_category, reviewer.strip())
        else:
            reason = false_alarm_reason or FalseAlarmReason.NORMAL_ACTIVITY
            reject_note = (
                note.strip() or "Operatör nöbet kuyruğunda sorun olmadığını belirtti."
            )
            if (
                reason == FalseAlarmReason.NORMAL_ACTIVITY
                and not self._scope_is_protected(item.model_category, item.risk)
            ):


                review = self._save_dismissal(
                    item,
                    reviewer=reviewer.strip(),
                    note=reject_note,
                    reason=reason,
                    intervention_required=required,
                )
            else:
                review = self._save_review(
                    event_id,
                    ReviewDecision.REJECT,
                    reviewer=reviewer.strip(),
                    note=reject_note,
                    false_alarm_reason=reason,
                    intervention_required=required,
                )
            item.false_alarm_reason = reason.value
            self.dismissed_count += 1

        self._pending.pop(key)
        item.review_ids = [review.review_id]
        item.verdict = verdict
        item.note = review.note[:500]
        item.reviewer = reviewer.strip()[:120]
        item.intervention_required = required
        item.review_start, item.review_peak, item.review_end = corrected_times
        item.operator_start = corrected_times[0]
        item.operator_end = corrected_times[2]
        item.decided_wall = time.time()
        self._append_resolved(item)
        self._stamp_and_log(item)
        return item

    def _save_ledger_only_decision(
        self,
        item: TriageItem,
        *,
        verdict: str,
        category: str,
        risk_level: str | None,
        start_time: float | None,
        peak_time: float | None,
        end_time: float | None,
        note: str,
        reviewer: str,
        operator_start: float | None,
        operator_end: float | None,
    ) -> TriageItem:


        self._pending.pop(item.key)
        item.verdict = verdict
        item.operator_category = category if verdict == "anomali" else ""
        item.operator_risk = risk_level or (item.risk if verdict == "anomali" else "")
        item.note = note[:500]
        item.reviewer = reviewer[:120]
        item.operator_start = operator_start
        item.operator_end = operator_end
        item.review_start = start_time
        item.review_peak = peak_time
        item.review_end = end_time
        if operator_start is None:
            item.operator_start = start_time
        if operator_end is None:
            item.operator_end = end_time
        item.decided_wall = time.time()
        if verdict == "sorun_degil" and not item.sample:
            self.dismissed_count += 1
        self._append_resolved(item)
        self._stamp_and_log(item)
        return item

    def revise(
        self,
        key: str,
        verdict: str,
        category: str = "",
        note: str = "",
        reviewer: str = "operator-console",
        operator_start: float | None = None,
        operator_end: float | None = None,
    ) -> TriageItem:


        from dataclasses import replace

        prior = next((item for item in reversed(self._resolved) if item.key == key), None)
        if prior is None:
            raise KeyError(f"düzeltilecek karar yok: {key}")
        if verdict not in {"anomali", "sorun_degil"}:
            raise ValueError(f"geçersiz karar: {verdict}")
        if verdict == "anomali" and category not in CATEGORIES:
            raise ValueError(f"geçersiz kategori: {category}")
        if (
            operator_start is not None
            and operator_end is not None
            and operator_start > operator_end
        ):
            raise ValueError("operatör başlangıcı bitişten sonra olamaz")
        item = replace(
            prior,
            verdict=verdict,
            operator_category=category if verdict == "anomali" else "",
            note=note[:500],
            reviewer=reviewer[:120],
            operator_start=operator_start,
            operator_end=operator_end,
            decided_wall=time.time(),
            decision_id="",
            supersedes=prior.decision_id,
        )
        self._append_resolved(item)
        self._stamp_and_log(item)
        return item

    def approve_rule(
        self,
        proposal_id: str,
        reviewer: str,
        duration_hours: int = DEFAULT_RULE_HOURS,
        expected_revision: int | None = None,
    ) -> RuleProposal:
        proposal = self._proposal(proposal_id)
        normalized_reviewer = reviewer.strip()
        if (
            proposal.status == RuleProposalStatus.APPROVED
            and proposal.decided_by == normalized_reviewer
        ):
            return proposal
        if proposal.status != RuleProposalStatus.PROPOSED:
            raise ValueError("yalnız proposed kural onaylanabilir")
        if proposal.category in PROTECTED_CATEGORIES:
            raise ValueError("kritik olay sınıfı için bastırma kuralı onaylanamaz")
        if not normalized_reviewer:
            raise ValueError("reviewer boş olamaz")
        if not 1 <= duration_hours <= MAX_RULE_HOURS:
            raise ValueError(f"kural süresi 1-{MAX_RULE_HOURS} saat olmalıdır")
        if expected_revision is not None and proposal.revision != expected_revision:
            raise ValueError(
                "kural önerisi ekrandan sonra değişti; güncel kaydı yeniden inceleyin"
            )
        now = self._clock()
        bundle = self._prepare_rule_approval_bundle(
            proposal,
            reviewer=normalized_reviewer,
            expires_at=now + timedelta(hours=duration_hours),
            created_at=now,
        )
        assert self.repository is not None
        saved = self.repository.save_feedback_bundle(bundle)
        return saved.rule_proposals[0]

    def reject_rule(self, proposal_id: str, reviewer: str) -> RuleProposal:
        proposal = self._proposal(proposal_id)
        if proposal.status != RuleProposalStatus.PROPOSED:
            raise ValueError("yalnız proposed kural reddedilebilir")
        if not reviewer.strip():
            raise ValueError("reviewer boş olamaz")
        return self._update_proposal(
            proposal,
            status=RuleProposalStatus.REJECTED,
            decided_by=reviewer.strip(),
            updated_at=self._clock(),
        )

    def revoke_rule(self, proposal_id: str, reviewer: str) -> RuleProposal:
        proposal = self._proposal(proposal_id)
        if proposal.status not in {
            RuleProposalStatus.COLLECTING,
            RuleProposalStatus.PROPOSED,
            RuleProposalStatus.APPROVED,
        }:
            raise ValueError("etkin olmayan kural geri alınamaz")
        if not reviewer.strip():
            raise ValueError("reviewer boş olamaz")
        self._revoke_development_use(proposal, reviewer.strip())
        return self._update_proposal(
            proposal,
            status=RuleProposalStatus.REVOKED,
            decided_by=reviewer.strip(),
            expires_at=None,
            updated_at=self._clock(),
        )

    def feed_note(self, feed: str) -> str:
        parts = [
            _NOTE_TR.get(rule.category, rule.category)
            for rule in self._approved_rules()
            if rule.feed == feed and rule.category not in PROTECTED_CATEGORIES
        ]
        if not parts:
            return ""
        return (
            "\n\n## Bu kameraya özgü OLAĞAN durumlar (süreli operatör onayı)\n"
            + "".join(
                f"- {part} bu kamerada olağandır; tek başına alarm üretme.\n"
                for part in parts
            )
        )

    def get_item(self, key: str) -> TriageItem | None:
        item = self._pending.get(key)
        if item is not None:
            return item
        return next(
            (entry for entry in reversed(self._resolved) if entry.key == key),
            None,
        )

    def decision_for(self, feed: str, incident_id: str) -> TriageItem | None:
        key = f"{feed}:{incident_id}"
        return next(
            (item for item in reversed(self._resolved) if item.key == key),
            None,
        )

    def snapshot(self) -> dict:
        self._expire_rules()
        confirmed = [
            self._item_view(item)
            for item in reversed(self._resolved)
            if item.verdict == "anomali"
        ]
        proposals = []
        if self.repository is not None:
            proposals = [
                item.model_dump(mode="json")
                for item in self.repository.list_rule_proposals()
                if item.status in {RuleProposalStatus.PROPOSED, RuleProposalStatus.APPROVED}
            ]
        return {
            "pending": [
                self._item_view(item)
                for item in sorted(
                    self._pending.values(),
                    key=lambda queued: (
                        -queued.intervention_score,
                        queued.wall,
                        queued.key,
                    ),
                )
            ],
            "confirmed": confirmed,
            "dismissed_count": self.dismissed_count,
            "auto_dismissed": self.auto_dismissed,
            "queue_overflow_count": self.queue_overflow_count,
            "expired_count": self.expired_count,
            "critical_overflow_count": max(0, len(self._pending) - MAX_PENDING),
            "rule_proposals": proposals,
            "rules": [
                {
                    "feed": item.feed,
                    "category": item.category,
                    "auto_count": item.auto_applied_count,
                }
                for item in self._approved_rules()
            ],
            "categories": CATEGORIES,
            "protected_categories": sorted(PROTECTED_CATEGORIES),
            "emsal": {
                "acik": settings.exemplar_suppress,
                "golge": settings.exemplar_shadow,
                "esik": settings.exemplar_threshold,
                "golge_isabet": self.emsal_shadow_count,
                "bastirilan": self.emsal_suppressed,
                "kamera_basina": self._matcher.counts(),
            },
        }

    def clear(self) -> None:
        self._pending.clear()
        self._resolved.clear()
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self.queue_overflow_count = 0
        self.expired_count = 0
        self.emsal_shadow_count = 0
        self.emsal_suppressed = 0
        self.rules.clear()
        self._runs.clear()

    def _new_item(
        self,
        event: Event,
        event_id: str | None,
        priority: tuple[int, str, list[str], str],
        match: exemplar_bank.Match | None = None,
    ) -> TriageItem:
        payload = event.payload
        run_id, video = self._runs.get(event.feed, ("", ""))
        if match is None:
            match = self._emsal_check(event.feed, payload)
        shadow_hit = bool(
            match is not None
            and match.shadow
            and match.similarity >= settings.exemplar_threshold
        )
        exemplar_hit = bool(match is not None and (match.suppress or shadow_hit))
        item = TriageItem(
            key=f"{event.feed}:{payload.incident_id}",
            feed=event.feed,
            incident_id=payload.incident_id,
            event_id=event_id,
            t=payload.t,
            wall=time.time(),
            title=payload.title,
            model_category=payload.anomaly_type,
            risk=payload.risk,
            phase=payload.phase,
            thumbnail=payload.thumbnail,
            evidence=payload.evidence,
            evidence_refs=self._evidence_dicts(event.feed, payload.incident_id),
            needs_review=payload.needs_review,
            review_reason=payload.review_reason,
            run_id=run_id,
            video=video,
            model_start=payload.olay_baslangic,
            model_end=payload.olay_bitis,
            signals=self._signal_dict(payload),
            emsal_golge=shadow_hit,
            emsal_benzerlik=(round(match.similarity, 4) if exemplar_hit else None),
            emsal_key=(
                match.precedent.key
                if exemplar_hit and match is not None and match.precedent is not None
                else ""
            ),
        )
        self._apply_priority(item, priority)
        return item

    def _emsal_check(self, feed: str, payload: Any):
        embedding = None
        evidence_key = exemplar_bank.key_from_evidence(
            getattr(payload, "evidence", None)
        )
        if evidence_key:
            found = exemplar_bank.load(settings.runs_dir).get(evidence_key)
            embedding = found.embedding if found is not None else None
        match = self._matcher.check(
            feed,
            payload.anomaly_type,
            payload.risk,
            embedding,
            threshold=settings.exemplar_threshold,
            enabled=settings.exemplar_suppress,
            shadow=settings.exemplar_shadow,
        )
        if match.shadow and match.similarity >= settings.exemplar_threshold:
            self.emsal_shadow_count += 1
        return match

    def _reopen_escalated(
        self,
        resolved: TriageItem,
        payload: IncidentUpdate,
        event_id: str | None,
        priority: tuple[int, str, list[str], str],
    ) -> TriageItem | None:


        scope = self._escalation_scope(payload.anomaly_type, payload.risk)
        seen = set(resolved.escalation_scopes)
        decided = self._escalation_scope(resolved.model_category, resolved.risk)
        if decided:
            seen.add(decided)
        if not scope or scope in seen:
            return None
        self._resolved.remove(resolved)
        item = resolved
        item.escalation_scopes = [*resolved.escalation_scopes, scope]
        item.t, item.risk, item.phase = payload.t, payload.risk, payload.phase
        item.title = payload.title
        item.model_category = payload.anomaly_type
        item.event_id = event_id or item.event_id
        item.thumbnail = payload.thumbnail or item.thumbnail
        item.verdict = ""
        item.operator_category = ""
        item.operator_risk = ""
        item.false_alarm_reason = ""
        item.intervention_required = None
        item.decided_wall = None
        item.review_start = item.review_peak = item.review_end = None
        item.needs_review = True
        item.review_reason = " · ".join(
            filter(
                None,
                [payload.review_reason, f"karar sonrası {scope} kapsamına tırmandı"],
            )
        )
        self._apply_priority(item, priority)
        return item

    def _priority_values(
        self, payload: IncidentUpdate, event_id: str | None
    ) -> tuple[int, str, list[str], str]:
        if self.priority_service is not None and event_id is not None:
            try:
                stored = self.priority_service.assess_and_save(
                    event_id,
                    risk=payload.risk,
                    event_type=payload.anomaly_type,
                    phase=payload.phase,
                    needs_review=payload.needs_review,
                )
                return (
                    stored.score,
                    stored.band.value,
                    list(stored.reasons),
                    stored.ruleset_version,
                )
            except Exception:
                LOGGER.exception(
                    "intervention priority kalıcı kayda yazılamadı: event=%s",
                    event_id,
                )
        calculated = calculate_priority_score(
            risk=payload.risk,
            event_type=payload.anomaly_type,
            phase=payload.phase,
            needs_review=payload.needs_review,
        )
        return (
            calculated.score,
            intervention_band_for_score(calculated.score).value,
            list(calculated.reasons),
            RULESET_VERSION,
        )

    @staticmethod
    def _apply_priority(
        item: TriageItem, priority: tuple[int, str, list[str], str]
    ) -> None:
        (
            item.intervention_score,
            item.intervention_band,
            item.intervention_reasons,
            item.priority_ruleset_version,
        ) = priority

    def _enforce_capacity(self) -> None:
        while len(self._pending) > MAX_PENDING:
            evictable = [
                item
                for item in self._pending.values()
                if item.intervention_band != "urgent"
                and item.model_category not in PROTECTED_CATEGORIES
                and item.risk not in PROTECTED_RISKS
            ]
            if not evictable:
                LOGGER.warning(
                    "nöbet kuyruğu yalnız kritik olaylarla kapasiteyi aştı: %d/%d",
                    len(self._pending),
                    MAX_PENDING,
                )
                return
            victim = min(
                evictable,
                key=lambda queued: (
                    queued.phase != "sonuclandi",
                    queued.intervention_score,
                    queued.wall,
                    queued.key,
                ),
            )
            self._pending.pop(victim.key)
            self.queue_overflow_count += 1
            self.expired_count += 1
            victim.verdict = "expired"
            victim.decided_wall = time.time()
            victim.note = "kuyruk taştı: operatör karar veremeden görünümden çıkarıldı"
            self._stamp_and_log(victim)
            LOGGER.warning(
                "nöbet kartı kapasite nedeniyle görünümden çıkarıldı; canonical kayıt korundu: %s",
                victim.key,
            )

    def _item_view(self, item: TriageItem) -> dict:
        view = asdict(item)
        event = (
            self.repository.get_event(item.event_id)
            if self.repository is not None and item.event_id is not None
            else None
        )
        media = (
            self.repository.get_incident_media_for_event(item.event_id)
            if self.repository is not None and item.event_id is not None
            else None
        )
        view.update(
            clip_url=f"/media/{media.clip_ref}" if media is not None else None,
            clip_start=media.clip_start if media is not None else None,
            clip_end=media.clip_end if media is not None else None,
            media_thumbnail_url=(
                f"/media/{media.thumbnail_ref}" if media is not None else None
            ),
            event_start=event.start_time if event is not None else None,
            event_peak=event.peak_time if event is not None else None,
            event_end=event.end_time if event is not None else None,
        )
        return view

    def _review_times(
        self,
        event_id: str,
        *,
        start_time: float | None,
        peak_time: float | None,
        end_time: float | None,
        use_event_defaults: bool,
    ) -> tuple[float | None, float | None, float | None]:
        assert self.repository is not None
        event = self.repository.get_event(event_id)
        assert event is not None
        supplied = (start_time, peak_time, end_time)
        if any(value is not None for value in supplied) and not all(
            value is not None for value in supplied
        ):
            raise ValueError("olay başlangıç, zirve ve bitiş zamanı birlikte verilmelidir")
        times = supplied
        if all(value is None for value in times) and use_event_defaults:
            times = (event.start_time, event.peak_time, event.end_time)
        if all(value is not None for value in times):
            start, peak, end = times
            assert start is not None and peak is not None and end is not None
            if not 0 <= start <= peak <= end:
                raise ValueError("beklenen sıra: start_time <= peak_time <= end_time")
            video = self.repository.get_video(event.video_id)
            if video is None:
                raise TriagePersistenceError("canonical video kaydı bulunamadı")
            if (
                "MOCK_VIRTUAL_SOURCE" not in video.warnings
                and end > video.duration_seconds
            ):
                raise ValueError("olay bitiş zamanı video süresini aşamaz")
        return times

    def _resolve_event_id(self, feed: str, incident_id: str) -> str | None:
        if self.event_id_resolver is None:
            return None
        return self.event_id_resolver(feed, incident_id)

    def _save_review(
        self, event_id: str, decision: ReviewDecision, **kwargs
    ) -> HumanReview:
        if self.event_service is None:
            raise TriagePersistenceError("canonical feedback servisi yapılandırılmadı")
        return self.event_service.review_event(event_id, decision, **kwargs)

    def _save_dismissal(
        self,
        item: TriageItem,
        *,
        reviewer: str,
        note: str,
        reason: FalseAlarmReason,
        intervention_required: bool,
    ) -> HumanReview:


        assert self.repository is not None
        assert item.event_id is not None
        review = HumanReview(
            review_id=str(uuid4()),
            event_id=item.event_id,
            decision=ReviewDecision.REJECT,
            false_alarm_reason=reason,
            intervention_required=intervention_required,
            note=note,
            reviewer=reviewer,
            revision=1,
        )
        proposal = self._prepare_dismissal(item, review.review_id, reviewer)
        saved = self.repository.save_feedback_bundle(
            FeedbackWriteBundle(
                reviews=(review,),
                rule_proposals=(proposal,) if proposal is not None else (),
            )
        )
        return saved.reviews[0]

    def _prepare_dismissal(
        self, item: TriageItem, review_id: str, reviewer: str
    ) -> RuleProposal | None:


        active = self._scope_proposal(item.feed, item.model_category)
        now = self._clock()
        if active is None:
            return RuleProposal(
                proposal_id=str(uuid4()),
                feed=item.feed,
                category=item.model_category,
                source_event_ids=[item.event_id],
                source_review_ids=[review_id],
                reason="Aynı kapsam için operatör retleri toplanıyor.",
                proposed_by=reviewer,
                created_at=now,
                updated_at=now,
            )
        if active.status in {RuleProposalStatus.PROPOSED, RuleProposalStatus.APPROVED}:


            return None
        count = active.dismissal_count + 1
        status = (
            RuleProposalStatus.PROPOSED
            if count >= RULE_THRESHOLD
            else RuleProposalStatus.COLLECTING
        )
        reason = (
            f"Operatör aynı kamera ve sınıfı {count} kez sorun değil olarak işaretledi."
            if status == RuleProposalStatus.PROPOSED
            else "Aynı kapsam için operatör retleri toplanıyor."
        )
        return RuleProposal.model_validate(
            {
                **active.model_dump(),
                "status": status,
                "dismissal_count": count,
                "source_review_ids": [*active.source_review_ids, review_id],
                "source_event_ids": [*active.source_event_ids, item.event_id],
                "reason": reason,
                "updated_at": now,
                "revision": active.revision + 1,
            }
        )

    def _cancel_scope(self, feed: str, category: str, reviewer: str) -> None:
        proposal = self._scope_proposal(feed, category)
        if proposal is not None:
            self.revoke_rule(proposal.proposal_id, reviewer)

    def _scope_proposal(self, feed: str, category: str) -> RuleProposal | None:
        if self.repository is None:
            return None
        active = {
            RuleProposalStatus.COLLECTING,
            RuleProposalStatus.PROPOSED,
            RuleProposalStatus.APPROVED,
        }
        return next(
            (
                item
                for item in reversed(self.repository.list_rule_proposals())
                if item.feed == feed and item.category == category and item.status in active
            ),
            None,
        )

    def _active_rule(self, feed: str, category: str) -> RuleProposal | None:
        self._expire_rules()
        proposal = self._scope_proposal(feed, category)
        return (
            proposal
            if proposal is not None and proposal.status == RuleProposalStatus.APPROVED
            else None
        )

    def _approved_rules(self) -> list[RuleProposal]:
        self._expire_rules()
        if self.repository is None:
            return []
        return [
            item
            for item in self.repository.list_rule_proposals()
            if item.status == RuleProposalStatus.APPROVED
        ]

    def _expire_rules(self) -> None:
        if self.repository is None:
            return
        now = self._clock()
        for item in self.repository.list_rule_proposals():
            if (
                item.status == RuleProposalStatus.APPROVED
                and item.expires_at is not None
                and item.expires_at <= now
            ):
                self._revoke_development_use(item, "system-expiry")
                self._update_proposal(
                    item,
                    status=RuleProposalStatus.EXPIRED,
                    decided_by="system-expiry",
                    updated_at=now,
                )

    def _mark_rule_applied(self, proposal: RuleProposal) -> RuleProposal:
        now = self._clock()
        return self._update_proposal(
            proposal,
            auto_applied_count=proposal.auto_applied_count + 1,
            last_applied_at=now,
            updated_at=now,
        )

    def _prepare_rule_approval_bundle(
        self,
        proposal: RuleProposal,
        *,
        reviewer: str,
        expires_at: datetime,
        created_at: datetime,
    ) -> FeedbackWriteBundle:
        if self.repository is None:
            raise TriagePersistenceError("canonical feedback repository yapılandırılmadı")
        for event_id, review_id in zip(
            proposal.source_event_ids, proposal.source_review_ids, strict=True
        ):
            reviews = self.repository.list_reviews(event_id)
            if not any(item.review_id == review_id for item in reviews):
                raise TriagePersistenceError("kural kaynağı human review ile eşleşmiyor")
            if self.repository.list_development_approvals(event_id):
                raise ValueError(
                    "Kaynak olay için daha yeni geliştirme kararı var; kural onaylanmadı."
                )
        approvals = tuple(
            DevelopmentApproval(
                approval_id=str(uuid4()),
                event_id=event_id,
                review_id=review_id,
                status=DevelopmentApprovalStatus.APPROVED,
                approved_uses=[DevelopmentUse.CAMERA_RULE],
                reviewer=reviewer,
                note=(
                    "Operatör bu geri bildirimi süreli kamera kuralı için onayladı."
                ),
                created_at=created_at,
            )
            for event_id, review_id in zip(
                proposal.source_event_ids, proposal.source_review_ids, strict=True
            )
        )
        approved = RuleProposal.model_validate(
            {
                **proposal.model_dump(),
                "status": RuleProposalStatus.APPROVED,
                "decided_by": reviewer,
                "expires_at": expires_at,
                "development_approval_ids": [
                    item.approval_id for item in approvals
                ],
                "updated_at": created_at,
                "revision": proposal.revision + 1,
            }
        )
        return FeedbackWriteBundle(
            development_approvals=approvals,
            rule_proposals=(approved,),
        )

    def _revoke_development_use(self, proposal: RuleProposal, reviewer: str) -> None:
        if self.repository is None or self.event_service is None:
            return
        for event_id, approval_id in zip(
            proposal.source_event_ids,
            proposal.development_approval_ids,
            strict=False,
        ):
            history = self.repository.list_development_approvals(event_id)
            latest = history[-1] if history else None
            if (
                latest is None
                or latest.approval_id != approval_id
                or latest.status != DevelopmentApprovalStatus.APPROVED
            ):
                continue
            self.event_service.record_development_decision(
                event_id,
                latest.review_id,
                DevelopmentApprovalStatus.REVOKED,
                approved_uses=[],
                reviewer=reviewer,
                note="Süreli kamera kuralının geliştirme izni sona erdi.",
                supersedes_approval_id=latest.approval_id,
            )

    def _proposal(self, proposal_id: str) -> RuleProposal:
        if self.repository is None:
            raise TriagePersistenceError("canonical feedback repository yapılandırılmadı")
        proposal = self.repository.get_rule_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"rule proposal bulunamadı: {proposal_id}")
        return proposal

    def _update_proposal(self, proposal: RuleProposal, **changes) -> RuleProposal:
        assert self.repository is not None
        updated = RuleProposal.model_validate(
            {
                **proposal.model_dump(),
                **changes,
                "revision": proposal.revision + 1,
            }
        )
        return self.repository.update_rule_proposal(updated)

    @staticmethod
    def _scope_is_protected(category: str, risk: str) -> bool:
        return category in PROTECTED_CATEGORIES or risk in PROTECTED_RISKS

    @staticmethod
    def _escalation_scope(category: str, risk: str) -> str:


        return "/".join(
            part
            for part in (
                category if category in PROTECTED_CATEGORIES else "",
                risk if risk in PROTECTED_RISKS else "",
            )
            if part
        )

    def _append_resolved(self, item: TriageItem) -> None:
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]

    def _stamp_and_log(self, item: TriageItem) -> None:
        if not item.decision_id:
            item.decision_id = uuid4().hex
        if not item.config:
            item.config = _config_snapshot()
        if not item.run_meta:
            item.run_meta = _run_meta(item.run_id)
        try:
            settings.runs_dir.mkdir(parents=True, exist_ok=True)
            with (settings.runs_dir / "nobet_defteri.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        except OSError:
            LOGGER.exception("nöbet defteri yazılamadı")


store = TriageStore()
