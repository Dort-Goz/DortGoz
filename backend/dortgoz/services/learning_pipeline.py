
from __future__ import annotations

import json
from pathlib import Path

from ..domain.feedback import DevelopmentUse
from ..domain.model_lifecycle import (
    DfineArchitecture,
    DfineTrainingPolicy,
    ModelStage,
    ModelVersion,
    PromotionPolicy,
    TrainingJob,
    TrainingJobStatus,
)
from ..domain.pipeline import (
    LearningPipelineView,
    PipelineEventItem,
    PipelineModelItem,
    PipelineQueueGroup,
    PipelineReadiness,
    PipelineStage,
    PipelineStageSummary,
)
from ..repositories.protocols import EventRepository
from .execution_coordinator import ExecutionCoordinator
from .learning_orchestrator import LearningOrchestrator, LearningSnapshot
from .model_registry import ModelRegistryService

_ACTIVE_JOB_STATES = frozenset(
    {TrainingJobStatus.QUEUED, TrainingJobStatus.RUNNING}
)
_ATTENTION_JOB_STATES = frozenset(
    {
        TrainingJobStatus.FAILED,
        TrainingJobStatus.INTERRUPTED,
        TrainingJobStatus.BUDGET_STOPPED,
    }
)
_APPROVAL_PENDING_STATES = frozenset({"approval_required", "not_approved", "stale"})

_JOB_HISTORY_LIMIT = 50
# Stage counts stay exact; only the transported item lists are capped.
_STAGE_ITEM_LIMIT = 50


class LearningPipelineError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class LearningPipelineService:
    """One read model for the whole feedback pipeline.

    İnceleme → Onay → Kuyruk → Eğitim → Ölçüm → Terfi. Every stage reads a
    record that already exists; this service does not persist a stage of its own.
    """

    def __init__(
        self,
        repository: EventRepository,
        orchestrator: LearningOrchestrator,
        *,
        workspace_root: Path,
        policy_path: Path,
        dfine_repository: Path | None = None,
        base_checkpoint: Path | None = None,
        dataset_manifest_path: Path | None = None,
        registry_root: Path | None = None,
        execution_coordinator: ExecutionCoordinator | None = None,
    ) -> None:
        self.repository = repository
        self.orchestrator = orchestrator
        self.workspace_root = workspace_root.resolve()
        self.policy_path = policy_path
        self.dfine_repository = dfine_repository
        self.base_checkpoint = base_checkpoint
        self.dataset_manifest_path = dataset_manifest_path
        self.execution_coordinator = execution_coordinator
        self.registry = ModelRegistryService(
            repository,
            workspace_root=self.workspace_root,
            registry_root=(
                registry_root
                if registry_root is not None
                else self.workspace_root / "models" / "dfine" / "local"
            ),
        )

    def view(self) -> LearningPipelineView:
        snapshot = self.orchestrator.snapshot()
        jobs = sorted(
            self.repository.list_training_jobs(),
            key=lambda job: (job.created_at, job.job_id),
            reverse=True,
        )[:_JOB_HISTORY_LIMIT]
        versions = self.repository.list_model_versions()
        training_policy, promotion_policy = self.policies()
        champion = next(
            (item for item in versions if item.stage == ModelStage.CHAMPION), None
        )
        candidates = [
            self._model_item(version, promotion_policy, champion)
            for version in sorted(
                (item for item in versions if item.stage == ModelStage.CANDIDATE),
                key=lambda item: (item.created_at, item.model_version_id),
                reverse=True,
            )
        ]
        review_items = [
            self._event_item(entry.summary)
            for entry in snapshot.events
            if entry.review_id is None
        ]
        approval_items = [
            self._event_item(entry.summary)
            for entry in snapshot.events
            if entry.review_id is not None and self._awaits_approval(entry)
        ]
        queue = [
            self._queue_group(use, snapshot) for use in DevelopmentUse
        ]
        readiness = self.readiness(
            training_policy=training_policy, promotion_policy=promotion_policy
        )
        return LearningPipelineView(
            stages=self._stages(
                review_items=review_items,
                approval_items=approval_items,
                queue=queue,
                jobs=jobs,
                candidates=candidates,
            ),
            review_items=sorted(
                review_items, key=lambda item: -item.learning_score
            )[:_STAGE_ITEM_LIMIT],
            approval_items=sorted(
                approval_items, key=lambda item: -item.learning_score
            )[:_STAGE_ITEM_LIMIT],
            queue=queue,
            jobs=jobs,
            candidates=candidates,
            champion=(
                self._model_item(champion, promotion_policy, None)
                if champion is not None
                else None
            ),
            readiness=readiness,
            drift=snapshot.drift,
        )

    def readiness(
        self,
        *,
        training_policy: DfineTrainingPolicy | None = None,
        promotion_policy: PromotionPolicy | None = None,
    ) -> PipelineReadiness:
        if training_policy is None or promotion_policy is None:
            training_policy, promotion_policy = self.policies()
        blockers: list[str] = []
        if not _is_directory(self.dfine_repository):
            blockers.append(
                "D-FINE eğitim deposu yapılandırılmadı "
                "(DORTGOZ_DFINE_TRAINING_REPOSITORY)"
            )
        if not _is_file(self.base_checkpoint):
            blockers.append(
                "Temel ağırlık dosyası bulunamadı (DORTGOZ_DFINE_BASE_CHECKPOINT)"
            )
        if not _is_file(self.dataset_manifest_path):
            blockers.append(
                "Veri kümesi bildirimi bulunamadı (DORTGOZ_DFINE_DATASET_MANIFEST)"
            )
        if training_policy is None:
            blockers.append(f"Eğitim politikası okunamadı: {self.policy_path}")
        active = None
        if self.execution_coordinator is not None:
            owner = self.execution_coordinator.active_exclusive()
            active = owner.workload.value if owner is not None else None
        can_plan = not blockers
        return PipelineReadiness(
            can_plan=can_plan,
            can_run=can_plan and active is None,
            blockers=blockers + (
                [f"Münhasır iş çalışıyor: {active}"] if active is not None else []
            ),
            active_workload=active,
            training_policy_version=(
                training_policy.policy_version if training_policy is not None else None
            ),
            promotion_policy_version=(
                promotion_policy.policy_version
                if promotion_policy is not None
                else None
            ),
        )

    def promotion_gate(self, model_version_id: str) -> PipelineModelItem:
        version = self.repository.get_model_version(model_version_id)
        if version is None:
            raise LearningPipelineError(
                "MODEL_VERSION_NOT_FOUND",
                f"model version bulunamadı: {model_version_id}",
                status_code=404,
            )
        _, promotion_policy = self.policies()
        champion = next(
            (
                item
                for item in self.repository.list_model_versions()
                if item.stage == ModelStage.CHAMPION
            ),
            None,
        )
        return self._model_item(version, promotion_policy, champion)

    def allowed_architectures(self) -> list[DfineArchitecture]:
        training_policy, _ = self.policies()
        if training_policy is None:
            return []
        return list(training_policy.allowed_architectures)

    def policies(self) -> tuple[DfineTrainingPolicy | None, PromotionPolicy | None]:
        try:
            payload = json.loads(self.policy_path.read_text(encoding="utf-8"))
            return (
                DfineTrainingPolicy.model_validate(payload["training_policy"]),
                PromotionPolicy.model_validate(payload["promotion_policy"]),
            )
        except (OSError, ValueError, KeyError):
            return (None, None)

    def _queue_group(
        self, use: DevelopmentUse, snapshot: LearningSnapshot
    ) -> PipelineQueueGroup:
        queue = self.orchestrator.route_queue(use, snapshot=snapshot)
        sample = next(
            (
                route
                for entry in snapshot.events
                for route in entry.plan.routes
                if route.use == use
            ),
            None,
        )
        return PipelineQueueGroup(
            use=use,
            downstream=sample.downstream if sample is not None else use.value,
            safety_gate=sample.safety_gate if sample is not None else "insan onayı",
            count=queue.count,
            items=queue.items,
        )

    def _model_item(
        self,
        version: ModelVersion,
        promotion_policy: PromotionPolicy | None,
        champion: ModelVersion | None,
    ) -> PipelineModelItem:
        failures: list[str]
        if promotion_policy is None:
            failures = ["terfi politikası okunamadı"]
        elif version.stage == ModelStage.CHAMPION:
            failures = []
        else:
            failures = self.registry.promotion_failures(
                version, promotion_policy, champion
            )
        evaluation = version.evaluation
        return PipelineModelItem(
            version=version,
            gate_failures=failures,
            gate_passed=not failures,
            onnx_exported=version.deployment is not None,
            measured=evaluation is not None,
            shadow_passed=evaluation.shadow_passed if evaluation is not None else False,
        )

    @staticmethod
    def _event_item(summary) -> PipelineEventItem:
        return PipelineEventItem(
            event_id=summary.event_id,
            event_type=summary.event_type,
            video_id=summary.video_id,
            learning_score=summary.learning_score,
            learning_band=summary.learning_band,
            recommended_uses=list(summary.recommended_uses),
            ready_uses=list(summary.ready_uses),
            blockers=list(summary.blockers),
        )

    @staticmethod
    def _awaits_approval(entry) -> bool:
        return any(
            route.recommended and route.approval_state in _APPROVAL_PENDING_STATES
            for route in entry.plan.routes
        )

    @staticmethod
    def _stages(
        *,
        review_items: list[PipelineEventItem],
        approval_items: list[PipelineEventItem],
        queue: list[PipelineQueueGroup],
        jobs: list[TrainingJob],
        candidates: list[PipelineModelItem],
    ) -> list[PipelineStageSummary]:
        ready_events = {
            item.event_id for group in queue for item in group.items
        }
        stale_approvals = sum(
            any("yenilenmeli" in blocker for blocker in item.blockers)
            for item in approval_items
        )
        active_jobs = [job for job in jobs if job.status in _ACTIVE_JOB_STATES]
        attention_jobs = [job for job in jobs if job.status in _ATTENTION_JOB_STATES]
        measuring = [item for item in candidates if not item.measured]
        promotable = [item for item in candidates if item.measured]
        return [
            PipelineStageSummary(
                stage=PipelineStage.REVIEW,
                count=len(review_items),
                blocked_count=0,
                action_label="Olayı incele",
                detail="Operatör kararı bekleyen olaylar.",
            ),
            PipelineStageSummary(
                stage=PipelineStage.APPROVAL,
                count=len(approval_items),
                blocked_count=stale_approvals,
                action_label="Geliştirme iznini ver",
                detail="İncelendi, geliştirme kullanımı için açık izin bekliyor.",
            ),
            PipelineStageSummary(
                stage=PipelineStage.QUEUE,
                count=len(ready_events),
                blocked_count=0,
                action_label="Paket oluştur",
                detail="İzinli örnekler eğitim paketine alınmaya hazır.",
            ),
            PipelineStageSummary(
                stage=PipelineStage.TRAINING,
                count=len(active_jobs),
                blocked_count=len(attention_jobs),
                action_label="Eğitimi başlat",
                detail="Planlanan ve çalışan D-FINE eğitim işleri.",
            ),
            PipelineStageSummary(
                stage=PipelineStage.MEASUREMENT,
                count=len(measuring),
                blocked_count=sum(
                    not item.onnx_exported for item in measuring
                ),
                action_label="Ölçümü tamamla",
                detail="ONNX aktarımı, dedektör ölçümü ve gölge koşusu bekleyen adaylar.",
            ),
            PipelineStageSummary(
                stage=PipelineStage.PROMOTION,
                count=len(promotable),
                blocked_count=sum(
                    not item.gate_passed for item in promotable
                ),
                action_label="Terfi ettir",
                detail="Ölçümü biten adaylar; terfi kapısı insan onayı ister.",
            ),
        ]


def _is_file(path: Path | None) -> bool:
    return path is not None and path.is_file()


def _is_directory(path: Path | None) -> bool:
    return path is not None and path.is_dir()


__all__ = ["LearningPipelineError", "LearningPipelineService"]
