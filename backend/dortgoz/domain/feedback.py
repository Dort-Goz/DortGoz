"""Controlled development feedback contracts.

Operator review and development approval are separate decisions.  A review can
correct an event without authorising that event for prompts, calibration,
training, or evaluation.
"""

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
    PROMPT_EXAMPLE = "prompt_example"
    THRESHOLD_CALIBRATION = "threshold_calibration"
    SIGLIP_TRAINING = "siglip_training"
    D_FINE_TRAINING = "d_fine_training"
    EVALUATION = "evaluation"


class DevelopmentApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class DevelopmentApproval(BaseModel):
    """Append-only operator decision for development use of one reviewed event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
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
