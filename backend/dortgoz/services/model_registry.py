

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ..domain.model_lifecycle import (
    ModelEvaluation,
    ModelStage,
    ModelVersion,
    PromotionPolicy,
)
from ..repositories.protocols import EventRepository
from .dataset_manifest import sha256_file


class ModelRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.reasons = reasons or []


class ModelRegistryService:


    def __init__(
        self,
        repository: EventRepository,
        *,
        workspace_root: Path,
        registry_root: Path,
    ) -> None:
        self.repository = repository
        self.workspace_root = workspace_root.resolve()
        self.registry_root = registry_root.resolve()
        if not self.registry_root.is_relative_to(self.workspace_root):
            raise ValueError("registry_root workspace içinde olmalıdır")

    def record_evaluation(
        self,
        model_version_id: str,
        *,
        test_dataset_fingerprint: str,
        code_revision: str,
        map_50_95: float,
        map_50: float,
        critical_recall: float,
        false_alarms_per_hour: float,
        p95_latency_ms: float,
        peak_memory_mb: int,
        repetitions: int,
        shadow_passed: bool,
        evaluator: str,
        measured_at: datetime,
        detector_report_sha256: str,
        e2e_artifact_sha256s: list[str],
    ) -> ModelVersion:
        version = self._get_version(model_version_id)
        if version.stage != ModelStage.CANDIDATE or version.evaluation is not None:
            raise ModelRegistryError(
                "MODEL_NOT_EVALUATABLE",
                "yalnız değerlendirilmemiş candidate model ölçülebilir",
            )
        if version.deployment is None:
            raise ModelRegistryError(
                "MODEL_DEPLOYMENT_MISSING",
                "candidate değerlendirmeden önce production ONNX'e aktarılmalıdır",
            )
        payload = {
            "evaluation_version": "1.0.0",
            "checkpoint_sha256": version.checkpoint_sha256,
            "test_dataset_fingerprint": test_dataset_fingerprint,
            "code_revision": code_revision,
            "map_50_95": map_50_95,
            "map_50": map_50,
            "critical_recall": critical_recall,
            "false_alarms_per_hour": false_alarms_per_hour,
            "p95_latency_ms": p95_latency_ms,
            "peak_memory_mb": peak_memory_mb,
            "repetitions": repetitions,
            "shadow_passed": shadow_passed,
            "evaluator": evaluator,
            "measured_at": measured_at.isoformat(),
            "detector_report_sha256": detector_report_sha256,
            "e2e_artifact_sha256s": e2e_artifact_sha256s,
        }
        evaluation = ModelEvaluation(
            evaluation_id=f"dfine-evaluation-{uuid4()}",
            metrics_fingerprint=_payload_fingerprint(payload),
            **payload,
        )
        updated = ModelVersion.model_validate(
            {
                **version.model_dump(),
                "evaluation": evaluation,
                "updated_at": datetime.now(UTC),
                "revision": version.revision + 1,
            }
        )
        return self.repository.update_model_version(updated)

    def promotion_failures(
        self,
        candidate: ModelVersion,
        policy: PromotionPolicy,
        champion: ModelVersion | None,
    ) -> list[str]:
        evaluation = candidate.evaluation
        if evaluation is None:
            return ["candidate evaluation kaydı yok"]
        failures: list[str] = []
        if candidate.deployment is None:
            failures.append("candidate production ONNX deployment kaydı yok")
        checks = (
            (
                evaluation.map_50_95 >= policy.minimum_map_50_95,
                f"mAP50-95 {evaluation.map_50_95:.3f} < {policy.minimum_map_50_95:.3f}",
            ),
            (
                evaluation.critical_recall >= policy.minimum_critical_recall,
                "kritik olay recall "
                f"{evaluation.critical_recall:.3f} < {policy.minimum_critical_recall:.3f}",
            ),
            (
                evaluation.false_alarms_per_hour <= policy.maximum_false_alarms_per_hour,
                "yanlış alarm/saat "
                f"{evaluation.false_alarms_per_hour:.3f} > "
                f"{policy.maximum_false_alarms_per_hour:.3f}",
            ),
            (
                evaluation.p95_latency_ms <= policy.maximum_p95_latency_ms,
                f"p95 gecikme {evaluation.p95_latency_ms:.1f} ms > "
                f"{policy.maximum_p95_latency_ms:.1f} ms",
            ),
            (
                evaluation.peak_memory_mb <= policy.maximum_peak_memory_mb,
                f"tepe bellek {evaluation.peak_memory_mb} MB > {policy.maximum_peak_memory_mb} MB",
            ),
            (
                evaluation.repetitions >= policy.minimum_repetitions,
                f"tekrar {evaluation.repetitions} < {policy.minimum_repetitions}",
            ),
            (evaluation.shadow_passed, "shadow test geçmedi"),
        )
        failures.extend(message for passed, message in checks if not passed)
        if champion is not None:
            baseline = champion.evaluation
            if baseline is None:
                failures.append("mevcut champion evaluation kaydı yok")
            else:
                if evaluation.critical_recall < (
                    baseline.critical_recall - policy.maximum_critical_recall_drop
                ):
                    failures.append("kritik olay recall mevcut champion değerinden düştü")
                if evaluation.false_alarms_per_hour > (
                    baseline.false_alarms_per_hour + policy.maximum_false_alarm_increase
                ):
                    failures.append("yanlış alarm/saat mevcut champion değerinden arttı")
                if evaluation.p95_latency_ms > (
                    baseline.p95_latency_ms * (1 + policy.maximum_latency_increase_ratio)
                ):
                    failures.append("p95 gecikme mevcut champion sınırını aştı")
        return failures

    def promote(
        self,
        model_version_id: str,
        *,
        policy: PromotionPolicy,
        approved_by: str,
        reason: str,
    ) -> ModelVersion:
        candidate = self._get_version(model_version_id)
        if candidate.stage != ModelStage.CANDIDATE:
            raise ModelRegistryError("MODEL_NOT_CANDIDATE", "yalnız candidate model terfi edebilir")
        self._verify_model_artifacts(candidate)
        current = self._current_champion()
        failures = self.promotion_failures(candidate, policy, current)
        if failures:
            raise ModelRegistryError(
                "PROMOTION_GATE_REJECTED",
                "candidate model terfi kapısından geçemedi",
                reasons=failures,
            )
        promoted = self._champion_version(
            candidate,
            policy_version=policy.policy_version,
            approved_by=approved_by,
            reason=reason,
        )
        retired = self._retired_version(current) if current is not None else None
        saved = self.repository.switch_champion(promoted, retired)
        self._write_active_manifest(saved)
        return saved

    def rollback_failed_champion(
        self,
        failed_model_version_id: str,
        *,
        failure_code: str,
        failure_detail: str,
    ) -> ModelVersion:
        current = self._current_champion()
        if current is None or current.model_version_id != failed_model_version_id:
            raise ModelRegistryError(
                "FAILED_MODEL_NOT_CHAMPION",
                "başarısız olduğu bildirilen model aktif champion değil",
            )
        target = next(
            (
                version
                for version in sorted(
                    self.repository.list_model_versions(),
                    key=lambda item: (item.retired_at or item.updated_at, item.model_version_id),
                    reverse=True,
                )
                if version.stage == ModelStage.RETIRED
                and version.model_version_id != current.model_version_id
            ),
            None,
        )
        if target is None:
            raise ModelRegistryError(
                "ROLLBACK_TARGET_MISSING", "geri dönülecek önceki champion bulunamadı"
            )
        self._verify_model_artifacts(target)
        restored = self._champion_version(
            target,
            policy_version=target.promotion_policy_version or "rollback-v1",
            approved_by="automatic-health-gate",
            reason=f"{failure_code}: {failure_detail}",
        )
        retired = self._retired_version(current)
        saved = self.repository.switch_champion(restored, retired)
        self._write_active_manifest(saved)
        return saved

    def reconcile_active_manifest(self) -> ModelVersion | None:
        champion = self._current_champion()
        if champion is not None:
            try:
                self._verify_model_artifacts(champion)
            except ModelRegistryError as exc:
                return self.rollback_failed_champion(
                    champion.model_version_id,
                    failure_code=exc.code,
                    failure_detail=str(exc),
                )
            self._write_active_manifest(champion)
        return champion

    def _get_version(self, model_version_id: str) -> ModelVersion:
        version = self.repository.get_model_version(model_version_id)
        if version is None:
            raise ModelRegistryError(
                "MODEL_VERSION_NOT_FOUND", f"model version bulunamadı: {model_version_id}"
            )
        return version

    def _current_champion(self) -> ModelVersion | None:
        champions = [
            item
            for item in self.repository.list_model_versions()
            if item.stage == ModelStage.CHAMPION
        ]
        if len(champions) > 1:
            raise ModelRegistryError(
                "MULTIPLE_CHAMPIONS", "registry birden fazla champion içeriyor"
            )
        return champions[0] if champions else None

    def _verify_checkpoint(self, version: ModelVersion) -> Path:
        path = self.workspace_root.joinpath(*version.checkpoint_ref.split("/"))
        if path.is_symlink():
            raise ModelRegistryError("MODEL_CHECKPOINT_INVALID", "model checkpoint symlink olamaz")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace_root) or not resolved.is_file():
            raise ModelRegistryError("MODEL_CHECKPOINT_MISSING", "model checkpoint bulunamadı")
        if sha256_file(resolved) != version.checkpoint_sha256:
            raise ModelRegistryError(
                "MODEL_CHECKPOINT_CHANGED", "model checkpoint SHA-256 değeri değişti"
            )
        return resolved

    def _verify_model_artifacts(self, version: ModelVersion) -> tuple[Path, Path]:
        checkpoint = self._verify_checkpoint(version)
        deployment = version.deployment
        if deployment is None:
            raise ModelRegistryError(
                "MODEL_DEPLOYMENT_MISSING", "model production ONNX deployment kaydı yok"
            )
        path = self.workspace_root.joinpath(*deployment.onnx_ref.split("/"))
        if path.is_symlink():
            raise ModelRegistryError("MODEL_ONNX_INVALID", "model ONNX dosyası symlink olamaz")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace_root) or not resolved.is_file():
            raise ModelRegistryError("MODEL_ONNX_MISSING", "model ONNX dosyası bulunamadı")
        if sha256_file(resolved) != deployment.onnx_sha256:
            raise ModelRegistryError("MODEL_ONNX_CHANGED", "model ONNX SHA-256 değeri değişti")
        config = resolved.parent / "config.json"
        if config.is_symlink() or not config.is_file():
            raise ModelRegistryError(
                "MODEL_ONNX_CONFIG_MISSING", "model ONNX config.json dosyası yok"
            )
        try:
            payload = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelRegistryError(
                "MODEL_ONNX_CONFIG_INVALID", f"model ONNX config okunamadı: {exc}"
            ) from exc
        expected_labels = {str(index): name for index, name in enumerate(deployment.category_names)}
        if (
            payload.get("id2label") != expected_labels
            or payload.get("interest_labels") != deployment.category_names
            or payload.get("onnx_sha256") != deployment.onnx_sha256
            or payload.get("deployment_fingerprint") != deployment.artifact_fingerprint
        ):
            raise ModelRegistryError(
                "MODEL_ONNX_CONFIG_CHANGED", "model ONNX config deployment ile eşleşmiyor"
            )
        return checkpoint, resolved

    @staticmethod
    def _champion_version(
        version: ModelVersion,
        *,
        policy_version: str,
        approved_by: str,
        reason: str,
    ) -> ModelVersion:
        now = datetime.now(UTC)
        return ModelVersion.model_validate(
            {
                **version.model_dump(),
                "stage": ModelStage.CHAMPION,
                "promotion_policy_version": policy_version,
                "approved_by": approved_by,
                "promotion_reason": reason,
                "promoted_at": now,
                "retired_at": None,
                "revoked_at": None,
                "updated_at": now,
                "revision": version.revision + 1,
            }
        )

    @staticmethod
    def _retired_version(version: ModelVersion) -> ModelVersion:
        now = datetime.now(UTC)
        return ModelVersion.model_validate(
            {
                **version.model_dump(),
                "stage": ModelStage.RETIRED,
                "retired_at": now,
                "updated_at": now,
                "revision": version.revision + 1,
            }
        )

    def _write_active_manifest(self, version: ModelVersion) -> None:
        payload = {
            "manifest_version": "1.0.0",
            "model_version_id": version.model_version_id,
            "architecture": version.architecture.value,
            "checkpoint_ref": version.checkpoint_ref,
            "checkpoint_sha256": version.checkpoint_sha256,
            "dataset_fingerprint": version.dataset_fingerprint,
            "export_fingerprint": version.export_fingerprint,
            "dfine_repository_revision": version.dfine_repository_revision,
            "onnx_ref": version.deployment.onnx_ref if version.deployment else None,
            "onnx_sha256": (version.deployment.onnx_sha256 if version.deployment else None),
            "deployment_fingerprint": (
                version.deployment.artifact_fingerprint if version.deployment else None
            ),
            "category_names": (version.deployment.category_names if version.deployment else []),
            "promotion_policy_version": version.promotion_policy_version,
            "activated_at": datetime.now(UTC).isoformat(),
        }
        self.registry_root.mkdir(parents=True, exist_ok=True)
        target = self.registry_root / "active_manifest.json"
        temporary = self.registry_root / ".active_manifest.json.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)


def _payload_fingerprint(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["ModelRegistryError", "ModelRegistryService"]
