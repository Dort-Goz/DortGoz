

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FalseAlarmReason(StrEnum):
    NORMAL_ACTIVITY = "normal_activity"
    CAMERA_CONDITION = "camera_condition"
    OCCLUSION = "occlusion"
    REFLECTION_OR_SHADOW = "reflection_or_shadow"
    DUPLICATE_EVENT = "duplicate_event"
    WRONG_CLASSIFICATION = "wrong_classification"
    OTHER = "other"


class DevelopmentUse(StrEnum):
    CAMERA_RULE = "camera_rule"
    PROMPT_EXAMPLE = "prompt_example"
    THRESHOLD_CALIBRATION = "threshold_calibration"
    SIGLIP_TRAINING = "siglip_training"
    D_FINE_TRAINING = "d_fine_training"
    EVALUATION = "evaluation"


class DevelopmentApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class RuleProposalStatus(StrEnum):


    COLLECTING = "collecting"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    EXPIRED = "expired"


class RuleProposal(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(min_length=1)
    feed: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    status: RuleProposalStatus = RuleProposalStatus.COLLECTING
    dismissal_count: int = Field(default=1, ge=1)
    source_event_ids: list[str] = Field(min_length=1)
    source_review_ids: list[str] = Field(min_length=1)
    development_approval_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=1000)
    proposed_by: str = Field(default="operator-feedback", min_length=1, max_length=120)
    decided_by: str | None = Field(default=None, min_length=1, max_length=120)
    expires_at: datetime | None = None
    auto_applied_count: int = Field(default=0, ge=0)
    last_applied_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def lifecycle_fields_are_consistent(self) -> RuleProposal:
        if len(set(self.source_review_ids)) != len(self.source_review_ids):
            raise ValueError("rule proposal source_review_ids tekrar içeremez")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("rule proposal source_event_ids tekrar içeremez")
        if len(self.source_event_ids) != len(self.source_review_ids):
            raise ValueError("rule proposal event ve review kaynakları eşleşmelidir")
        if self.updated_at < self.created_at:
            raise ValueError("rule proposal updated_at created_at öncesinde olamaz")
        if self.status == RuleProposalStatus.APPROVED:
            if self.decided_by is None or self.expires_at is None:
                raise ValueError("approved rule proposal karar veren ve süre sonu gerektirir")
            if len(self.development_approval_ids) != len(self.source_review_ids):
                raise ValueError("approved rule proposal development approval gerektirir")
            if self.expires_at <= self.updated_at:
                raise ValueError("approved rule proposal süre sonu gelecekte olmalıdır")
        elif self.status == RuleProposalStatus.EXPIRED:
            if self.expires_at is None or self.expires_at > self.updated_at:
                raise ValueError("expired rule proposal geçmiş süre sonu gerektirir")
        elif self.expires_at is not None:
            raise ValueError("yalnız approved rule proposal süre sonu taşıyabilir")
        if self.status in {
            RuleProposalStatus.REJECTED,
            RuleProposalStatus.REVOKED,
            RuleProposalStatus.EXPIRED,
        } and self.decided_by is None:
            raise ValueError("terminal rule proposal karar veren kişi gerektirir")
        return self


class DevelopmentApproval(BaseModel):


    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    maintenance_review_id: str | None = Field(default=None, min_length=1)
    status: DevelopmentApprovalStatus
    approved_uses: list[DevelopmentUse] = Field(default_factory=list)
    reviewer: str = Field(min_length=1)
    note: str = Field(min_length=1)
    supersedes_approval_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def approval_fields_match_status(self) -> DevelopmentApproval:
        if len(set(self.approved_uses)) != len(self.approved_uses):
            raise ValueError("approved_uses tekrar eden değer içeremez")
        if self.status == DevelopmentApprovalStatus.APPROVED and not self.approved_uses:
            raise ValueError("approved development decision en az bir kullanım gerektirir")
        if self.status != DevelopmentApprovalStatus.APPROVED and self.approved_uses:
            raise ValueError("rejected veya revoked decision kullanım izni taşıyamaz")
        if (
            self.status == DevelopmentApprovalStatus.REVOKED
            and self.supersedes_approval_id is None
        ):
            raise ValueError("revoked decision önceki approval kaydını belirtmelidir")
        return self
