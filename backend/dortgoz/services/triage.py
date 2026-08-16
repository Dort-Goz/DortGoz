"""Kalıcı anomali nöbet kuyruğu ve kontrollü kural önerileri.

Her operatör kararı canonical ``HumanReview`` olarak event repository'ye
yazılır. Aynı kamera ve sınıf için üç ret doğrudan kural üretmez. Yalnız bir
``RuleProposal`` oluşturur. Öneri ayrı bir operatör onayı ve süre sonu olmadan
etkinleşmez. Kritik olay sınıfları hiçbir zaman bastırılamaz.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..domain.feedback import (
    DevelopmentApprovalStatus,
    DevelopmentUse,
    FalseAlarmReason,
    RuleProposal,
    RuleProposalStatus,
)
from ..domain.provenance import HumanReview, ReviewDecision
from ..domain.taxonomy import canonical_event_type_from_ws_label
from ..events import Event
from ..repositories.protocols import EventRepository
from .event_service import EventMemoryService

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

_NOTE_TR = {
    "hirsizlik": "araç ve eşya çevresindeki olağan yükleme veya bekleme hareketleri",
    "vandalizm": "yapı veya eşya yakınında çalışan ya da bekleyen kişiler",
    "bilinmeyen": "bu kameranın olağan sahne hareketleri",
}


class TriagePersistenceError(RuntimeError):
    """Karar canonical event kaydına güvenle bağlanamadı."""


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
    needs_review: bool = False
    review_reason: str = ""
    verdict: str = ""
    operator_category: str = ""
    note: str = ""
    decided_wall: float | None = None
    tekrar: int = 1
    review_ids: list[str] = field(default_factory=list)


class TriageStore:
    def __init__(
        self,
        repository: EventRepository | None = None,
        event_service: EventMemoryService | None = None,
        event_id_resolver: Callable[[str, str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._pending: dict[str, TriageItem] = {}
        self._resolved: list[TriageItem] = []
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self.repository = repository
        self.event_service = event_service or (
            EventMemoryService(repository) if repository is not None else None
        )
        self.event_id_resolver = event_id_resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def configure(
        self,
        repository: EventRepository,
        event_service: EventMemoryService,
        event_id_resolver: Callable[[str, str], str | None],
    ) -> None:
        """Uygulama composition sınırındaki canonical adapterları bağla."""

        self.repository = repository
        self.event_service = event_service
        self.event_id_resolver = event_id_resolver

    def observe(self, event: Event) -> None:
        payload = event.payload
        if getattr(payload, "type", "") != "incident_update":
            return
        key = f"{event.feed}:{payload.incident_id}"
        event_id = self._resolve_event_id(event.feed, payload.incident_id)
        if key in self._pending:
            item = self._pending[key]
            item.t, item.risk, item.phase = payload.t, payload.risk, payload.phase
            item.title = payload.title
            item.model_category = payload.anomaly_type
            item.event_id = event_id or item.event_id
            item.thumbnail = payload.thumbnail or item.thumbnail
            item.needs_review = payload.needs_review
            item.review_reason = payload.review_reason
            return
        if any(item.key == key for item in self._resolved):
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
            item = self._new_item(event, event_id)
            item.verdict = "sorun_degil"
            item.note = f"onaylı süreli kural: {rule.proposal_id}"
            item.decided_wall = time.time()
            item.review_ids = [review.review_id]
            self._append_resolved(item)
            return

        item = self._new_item(event, event_id)
        if rule is not None and event_id is None:
            suffix = "Canonical olay kaydı hazır değil; kural güvenli biçimde uygulanmadı."
            item.needs_review = True
            item.review_reason = " · ".join(filter(None, [item.review_reason, suffix]))
        self._pending[key] = item
        while len(self._pending) > MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))

    def decide(
        self,
        key: str,
        verdict: str,
        category: str = "",
        note: str = "",
        reviewer: str = "operator-console",
    ) -> TriageItem:
        if verdict not in {"anomali", "sorun_degil"}:
            raise ValueError(f"geçersiz karar: {verdict}")
        item = self._pending.get(key)
        if item is None:
            raise KeyError(f"bekleyen kayıt yok: {key}")
        if verdict == "anomali" and category not in CATEGORIES:
            raise ValueError(f"geçersiz kategori: {category}")
        if not reviewer.strip():
            raise ValueError("reviewer boş olamaz")

        event_id = item.event_id or self._resolve_event_id(item.feed, item.incident_id)
        if event_id is None or self.repository is None or self.repository.get_event(event_id) is None:
            raise TriagePersistenceError(
                "Olay henüz canonical SQLite kaydına bağlanamadı; karar kaydedilmedi."
            )
        item.event_id = event_id

        if verdict == "anomali":
            event = self.repository.get_event(event_id)
            assert event is not None
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
                risk_level=item.risk,
                intervention_required=True,
            )
            item.operator_category = category
            self._cancel_scope(item.feed, item.model_category, reviewer.strip())
        else:
            review = self._save_review(
                event_id,
                ReviewDecision.REJECT,
                reviewer=reviewer.strip(),
                note=note.strip() or "Operatör nöbet kuyruğunda sorun olmadığını belirtti.",
                false_alarm_reason=FalseAlarmReason.NORMAL_ACTIVITY,
                intervention_required=False,
            )
            self.dismissed_count += 1
            if not self._scope_is_protected(item.model_category, item.risk):
                self._record_dismissal(item, review.review_id, reviewer.strip())

        self._pending.pop(key)
        item.review_ids = [review.review_id]
        item.verdict = verdict
        item.note = note[:500]
        item.decided_wall = time.time()
        self._append_resolved(item)
        return item

    def approve_rule(
        self,
        proposal_id: str,
        reviewer: str,
        duration_hours: int = DEFAULT_RULE_HOURS,
    ) -> RuleProposal:
        proposal = self._proposal(proposal_id)
        if proposal.status != RuleProposalStatus.PROPOSED:
            raise ValueError("yalnız proposed kural onaylanabilir")
        if proposal.category in PROTECTED_CATEGORIES:
            raise ValueError("kritik olay sınıfı için bastırma kuralı onaylanamaz")
        if not reviewer.strip():
            raise ValueError("reviewer boş olamaz")
        if not 1 <= duration_hours <= MAX_RULE_HOURS:
            raise ValueError(f"kural süresi 1-{MAX_RULE_HOURS} saat olmalıdır")
        now = self._clock()
        approval_ids = self._approve_development_use(proposal, reviewer.strip())
        return self._update_proposal(
            proposal,
            status=RuleProposalStatus.APPROVED,
            decided_by=reviewer.strip(),
            expires_at=now + timedelta(hours=duration_hours),
            development_approval_ids=approval_ids,
            updated_at=now,
        )

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
                self._item_view(item) for item in reversed(list(self._pending.values()))
            ],
            "confirmed": confirmed,
            "dismissed_count": self.dismissed_count,
            "auto_dismissed": self.auto_dismissed,
            "rule_proposals": proposals,
            "categories": CATEGORIES,
            "protected_categories": sorted(PROTECTED_CATEGORIES),
        }

    def clear(self) -> None:
        self._pending.clear()
        self._resolved.clear()
        self.dismissed_count = 0
        self.auto_dismissed = 0

    def _new_item(self, event: Event, event_id: str | None) -> TriageItem:
        payload = event.payload
        return TriageItem(
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
            needs_review=payload.needs_review,
            review_reason=payload.review_reason,
        )

    def _item_view(self, item: TriageItem) -> dict:
        view = asdict(item)
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
        )
        return view

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

    def _record_dismissal(
        self, item: TriageItem, review_id: str, reviewer: str
    ) -> RuleProposal:
        assert self.repository is not None
        active = self._scope_proposal(item.feed, item.model_category)
        now = self._clock()
        if active is None:
            return self.repository.create_rule_proposal(
                RuleProposal(
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
            )
        if active.status == RuleProposalStatus.APPROVED:
            return active
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
        return self._update_proposal(
            active,
            status=status,
            dismissal_count=count,
            source_review_ids=[*active.source_review_ids, review_id],
            source_event_ids=[*active.source_event_ids, item.event_id],
            reason=reason,
            updated_at=now,
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

    def _approve_development_use(
        self, proposal: RuleProposal, reviewer: str
    ) -> list[str]:
        if self.repository is None or self.event_service is None:
            raise TriagePersistenceError("canonical development approval servisi yok")
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
        approvals = [
            self.event_service.record_development_decision(
                event_id,
                review_id,
                DevelopmentApprovalStatus.APPROVED,
                approved_uses=[DevelopmentUse.CAMERA_RULE],
                reviewer=reviewer,
                note=(
                    "Operatör bu geri bildirimi süreli kamera kuralı için onayladı."
                ),
            )
            for event_id, review_id in zip(
                proposal.source_event_ids, proposal.source_review_ids, strict=True
            )
        ]
        return [item.approval_id for item in approvals]

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

    def _append_resolved(self, item: TriageItem) -> None:
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]


store = TriageStore()
