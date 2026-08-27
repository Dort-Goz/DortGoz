
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_learning_orchestrator import _event, _repository, _review

from dortgoz.domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
)
from dortgoz.domain.pipeline import PipelineStage
from dortgoz.services.learning_orchestrator import LearningOrchestrator
from dortgoz.services.learning_pipeline import LearningPipelineService

POLICY_PATH = Path(__file__).resolve().parents[3] / "defaults" / "dfine_feedback_training.json"


def _service(repository, tmp_path: Path, **kwargs) -> LearningPipelineService:
    return LearningPipelineService(
        repository,
        LearningOrchestrator(repository),
        workspace_root=tmp_path,
        policy_path=kwargs.pop("policy_path", POLICY_PATH),
        **kwargs,
    )


def _stage(view, stage: PipelineStage):
    return next(item for item in view.stages if item.stage == stage)


def test_stages_follow_the_event_from_review_to_promotion(tmp_path: Path) -> None:
    repository = _repository()
    reviewed = _event(repository, 1)
    _event(repository, 2)
    review = _review(repository, reviewed, 1)

    view = _service(repository, tmp_path).view()

    assert _stage(view, PipelineStage.REVIEW).count == 1
    assert _stage(view, PipelineStage.APPROVAL).count == 1
    assert _stage(view, PipelineStage.QUEUE).count == 0
    assert [item.event_id for item in view.review_items] == ["event-learning-2"]
    assert [item.event_id for item in view.approval_items] == [reviewed.event_id]

    repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-pipeline-1",
            event_id=reviewed.event_id,
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.EVALUATION],
            reviewer="operator",
            note="Değerlendirme için onaylandı.",
        )
    )

    after = _service(repository, tmp_path).view()

    assert _stage(after, PipelineStage.QUEUE).count == 1
    evaluation = next(
        group for group in after.queue if group.use == DevelopmentUse.EVALUATION
    )
    assert [item.event_id for item in evaluation.items] == [reviewed.event_id]


def test_missing_training_configuration_blocks_planning(tmp_path: Path) -> None:
    repository = _repository()

    readiness = _service(repository, tmp_path).readiness()

    assert readiness.can_plan is False
    assert readiness.can_run is False
    assert len(readiness.blockers) == 3
    assert readiness.training_policy_version == "dfine-training-v1"
    assert readiness.promotion_policy_version == "dfine-promotion-v1"


def test_configured_paths_allow_planning(tmp_path: Path) -> None:
    repository = _repository()
    checkpoint = tmp_path / "base.pth"
    checkpoint.write_bytes(b"weights")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    dfine = tmp_path / "dfine"
    dfine.mkdir()

    readiness = _service(
        repository,
        tmp_path,
        dfine_repository=dfine,
        base_checkpoint=checkpoint,
        dataset_manifest_path=manifest,
    ).readiness()

    assert readiness.can_plan is True
    assert readiness.can_run is True
    assert readiness.blockers == []


def test_unreadable_policy_reports_a_blocker(tmp_path: Path) -> None:
    repository = _repository()
    broken = tmp_path / "policy.json"
    broken.write_text("{ not json", encoding="utf-8")

    readiness = _service(repository, tmp_path, policy_path=broken).readiness()

    assert readiness.can_plan is False
    assert readiness.training_policy_version is None
    assert any("politikas\u0131 okunamad\u0131" in blocker for blocker in readiness.blockers)


def test_view_never_advertises_automatic_training(tmp_path: Path) -> None:
    view = _service(_repository(), tmp_path).view()

    assert view.mode == "human_gated"
    assert view.automatic_training is False
    assert view.automatic_promotion is False
    assert view.pipeline_version == "dortgoz-learning-pipeline-v1"


def test_promotion_gate_rejects_unknown_model(tmp_path: Path) -> None:
    service = _service(_repository(), tmp_path)

    with pytest.raises(Exception) as excinfo:
        service.promotion_gate("model-yok")

    assert "MODEL_VERSION_NOT_FOUND" in str(excinfo.value) or "bulunamad" in str(
        excinfo.value
    )


def test_policy_file_matches_the_shipped_defaults() -> None:
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert payload["promotion_policy"]["minimum_repetitions"] >= 3
