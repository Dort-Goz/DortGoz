"""Birden çok feedback kaydını tek mantıksal yazımda taşıyan sözleşmeler."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.feedback import DevelopmentApproval, RuleProposal
from ..domain.provenance import HumanReview


@dataclass(frozen=True, slots=True)
class FeedbackWriteBundle:
    """Önceden kimliklendirilmiş feedback kayıtları."""

    reviews: tuple[HumanReview, ...] = ()
    development_approvals: tuple[DevelopmentApproval, ...] = ()
    rule_proposals: tuple[RuleProposal, ...] = ()

    def __post_init__(self) -> None:
        if not (self.reviews or self.development_approvals or self.rule_proposals):
            raise ValueError("feedback write bundle boş olamaz")


@dataclass(frozen=True, slots=True)
class FeedbackWriteResult:
    """Kaydedilen modeller ve bu çağrıda gerçekten değişen kimlikler."""

    reviews: tuple[HumanReview, ...]
    development_approvals: tuple[DevelopmentApproval, ...]
    rule_proposals: tuple[RuleProposal, ...]
    written_review_ids: frozenset[str] = frozenset()
    written_approval_ids: frozenset[str] = frozenset()
    written_proposal_ids: frozenset[str] = frozenset()


__all__ = ["FeedbackWriteBundle", "FeedbackWriteResult"]
