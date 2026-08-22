#!/usr/bin/env python3
"""Plan, run, evaluate, promote, or roll back a controlled D-FINE model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    DfineTrainingPolicy,
    PromotionPolicy,
)
from dortgoz.repositories.sqlite import SqliteEventRepository
from dortgoz.services.dataset_manifest import load_dataset_manifest
from dortgoz.services.dfine_deployment import execute_dfine_onnx_export
from dortgoz.services.dfine_evaluation import (
    build_dfine_test_command,
    execute_dfine_detector_evaluation,
    inspect_project_revision,
    load_dfine_evaluation_plan,
    normalize_dfine_evaluation_log,
    prepare_dfine_detector_evaluation,
    write_dfine_evaluation_plan,
)
from dortgoz.services.dfine_training import (
    DfineTrainingError,
    DfineTrainingService,
)
from dortgoz.services.evaluation_report import (
    DfineEvaluationReport,
    EvaluationReportError,
    build_dfine_evaluation_report,
)
from dortgoz.services.execution_coordinator import ExecutionCoordinator
from dortgoz.services.model_registry import (
    ModelRegistryError,
    ModelRegistryService,
)
from dortgoz.services.shadow_evaluation import (
    execute_shadow_evaluation,
    load_shadow_case_manifest,
    load_shadow_plan,
    prepare_shadow_evaluation,
    write_shadow_plan,
)
from dortgoz.services.training_selection import (
    load_training_selection_policy,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event-store", type=_path, required=True)
    parser.add_argument(
        "--policy",
        type=_path,
        default=REPO_ROOT / "defaults" / "dfine_feedback_training.json",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="COCO paketini doğrula ve işi kuyruğa al")
    _common(plan)
    plan.add_argument("--dataset-manifest", type=_path, required=True)
    plan.add_argument("--dfine-repository", type=_path, required=True)
    plan.add_argument("--base-checkpoint", type=_path, required=True)
    plan.add_argument(
        "--architecture",
        type=DfineArchitecture,
        choices=list(DfineArchitecture),
        default=DfineArchitecture.SMALL,
    )
    plan.add_argument("--requested-by", required=True)
    plan.add_argument("--epochs", type=int, default=10)
    plan.add_argument("--batch-size", type=int, default=2)
    plan.add_argument("--workers", type=int, default=2)
    plan.add_argument("--gpu-index", type=int, default=0)
    plan.add_argument("--max-gpu-minutes", type=int, default=60)
    plan.add_argument("--seed", type=int, default=0)
    plan.add_argument("--frame-root", type=_path, default=REPO_ROOT / "media")
    plan.add_argument("--runs-root", type=_path, default=REPO_ROOT / "runs")
    plan.add_argument(
        "--selection-policy",
        type=_path,
        default=REPO_ROOT / "defaults" / "dfine_sample_selection.json",
    )

    run = commands.add_parser(
        "run", help="kuyruktaki işi yerel CUDA worker'da çalıştır"
    )
    _common(run)
    run.add_argument("job_id")
    run.add_argument("--dfine-repository", type=_path, required=True)
    run.add_argument("--base-checkpoint", type=_path, required=True)
    run.add_argument("--python", type=_path, default=Path(sys.executable))
    run.add_argument("--frame-root", type=_path, default=REPO_ROOT / "media")
    run.add_argument("--runs-root", type=_path, default=REPO_ROOT / "runs")

    evaluate = commands.add_parser("evaluate", help="ölçüm JSON'unu candidate'a bağla")
    _common(evaluate)
    evaluate.add_argument("model_version_id")
    evaluate.add_argument("--report", type=_path, required=True)

    artifacts = commands.add_parser(
        "evaluate-artifacts",
        help="COCO ve üç shadow artifact'tan rapor üretip candidate'a bağla",
    )
    _common(artifacts)
    artifacts.add_argument("model_version_id")
    artifacts.add_argument("--test-dataset-manifest", type=_path, required=True)
    artifacts.add_argument("--detector-report", type=_path, required=True)
    artifacts.add_argument("--e2e-artifact", type=_path, action="append", required=True)
    artifacts.add_argument("--evaluator", required=True)
    artifacts.add_argument("--output", type=_path)

    prepare_evaluation = commands.add_parser(
        "prepare-evaluation",
        help="candidate, COCO test seti ve üç shadow tekrarı hash ile kilitle",
    )
    _common(prepare_evaluation)
    prepare_evaluation.add_argument("model_version_id")
    prepare_evaluation.add_argument(
        "--test-dataset-manifest", type=_path, required=True
    )
    prepare_evaluation.add_argument("--dfine-repository", type=_path, required=True)
    prepare_evaluation.add_argument("--coco-annotations", type=_path, required=True)
    prepare_evaluation.add_argument("--frame-root", type=_path, required=True)
    prepare_evaluation.add_argument("--created-by", required=True)
    prepare_evaluation.add_argument(
        "--python", type=_path, default=Path(sys.executable)
    )
    prepare_evaluation.add_argument("--batch-size", type=int, default=2)
    prepare_evaluation.add_argument("--workers", type=int, default=2)
    prepare_evaluation.add_argument(
        "--runs-root", type=_path, default=REPO_ROOT / "runs"
    )
    prepare_evaluation.add_argument("--output", type=_path)

    run_evaluation = commands.add_parser(
        "run-detector-evaluation",
        help="hazır planı yerel CUDA worker'da bütçeli çalıştır",
    )
    run_evaluation.add_argument("--event-store", type=_path, required=True)
    run_evaluation.add_argument("--plan", type=_path, required=True)
    run_evaluation.add_argument("--dfine-repository", type=_path, required=True)
    run_evaluation.add_argument("--python", type=_path, default=Path(sys.executable))
    run_evaluation.add_argument("--runs-root", type=_path, default=REPO_ROOT / "runs")
    run_evaluation.add_argument("--gpu-index", type=int, default=0)
    run_evaluation.add_argument("--batch-size", type=int, default=2)
    run_evaluation.add_argument("--workers", type=int, default=2)
    run_evaluation.add_argument("--max-gpu-minutes", type=int, default=60)

    normalize_detector = commands.add_parser(
        "normalize-detector",
        help="önceden alınmış resmî D-FINE logunu hash bağlı rapora çevir",
    )
    normalize_detector.add_argument("--plan", type=_path, required=True)
    normalize_detector.add_argument("--log", type=_path, required=True)
    normalize_detector.add_argument("--output", type=_path, required=True)

    export_onnx = commands.add_parser(
        "export-onnx",
        help="candidate checkpoint'i doğrulanmış production ONNX'e aktar",
    )
    _common(export_onnx)
    export_onnx.add_argument("model_version_id")
    export_onnx.add_argument("--dfine-repository", type=_path, required=True)
    export_onnx.add_argument("--python", type=_path, default=Path(sys.executable))
    export_onnx.add_argument("--runs-root", type=_path, default=REPO_ROOT / "runs")
    export_onnx.add_argument(
        "--registry-root",
        type=_path,
        default=REPO_ROOT / "models" / "dfine" / "local" / "candidates",
    )
    export_onnx.add_argument("--max-minutes", type=int, default=30)

    prepare_shadow = commands.add_parser(
        "prepare-shadow",
        help="candidate ve kritik/normal video listesini üç tekrarlı plana kilitle",
    )
    _common(prepare_shadow)
    prepare_shadow.add_argument("model_version_id")
    prepare_shadow.add_argument("--test-dataset-manifest", type=_path, required=True)
    prepare_shadow.add_argument("--case-manifest", type=_path, required=True)
    prepare_shadow.add_argument("--media-root", type=_path, default=REPO_ROOT / "media")
    prepare_shadow.add_argument("--created-by", required=True)
    prepare_shadow.add_argument("--output", type=_path)

    run_shadow = commands.add_parser(
        "run-shadow",
        help="hazır candidate planını üç kez izole canonical hatta çalıştır",
    )
    run_shadow.add_argument("--event-store", type=_path, required=True)
    run_shadow.add_argument("--plan", type=_path, required=True)
    run_shadow.add_argument("--runs-root", type=_path, default=REPO_ROOT / "runs")
    run_shadow.add_argument("--max-minutes", type=int, default=180)

    promote = commands.add_parser(
        "promote", help="kapıyı geçen candidate'ı champion yap"
    )
    _common(promote)
    promote.add_argument("model_version_id")
    promote.add_argument("--approved-by", required=True)
    promote.add_argument("--reason", required=True)

    rollback = commands.add_parser(
        "rollback", help="başarısız champion'dan önceki sürüme otomatik dön"
    )
    _common(rollback)
    rollback.add_argument("failed_model_version_id")
    rollback.add_argument("--failure-code", required=True)
    rollback.add_argument("--failure-detail", required=True)

    health = commands.add_parser(
        "health-check", help="aktif checkpoint'i doğrula; bozuksa önceki sürüme dön"
    )
    _common(health)

    status = commands.add_parser("status", help="iş ve model kayıtlarını göster")
    _common(status)
    return parser


def _load_policies(path: Path) -> tuple[DfineTrainingPolicy, PromotionPolicy]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        DfineTrainingPolicy.model_validate(payload["training_policy"]),
        PromotionPolicy.model_validate(payload["promotion_policy"]),
    )


def _active_analysis_probe(event_store: Path) -> bool:
    try:
        with sqlite3.connect(event_store) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM analyses WHERE status IN ('queued', 'running')"
            ).fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return True


def _training_service(
    repository: SqliteEventRepository,
    args: argparse.Namespace,
    policy: DfineTrainingPolicy,
) -> DfineTrainingService:
    return DfineTrainingService(
        repository,
        workspace_root=REPO_ROOT,
        frame_root=args.frame_root,
        runs_root=args.runs_root,
        policy=policy,
        selection_policy=(
            load_training_selection_policy(args.selection_policy)
            if getattr(args, "selection_policy", None) is not None
            else None
        ),
        active_analysis_probe=lambda: _active_analysis_probe(
            args.event_store.resolve()
        ),
        execution_coordinator=ExecutionCoordinator(args.event_store.resolve()),
    )


def _registry(repository: SqliteEventRepository) -> ModelRegistryService:
    return ModelRegistryService(
        repository,
        workspace_root=REPO_ROOT,
        registry_root=REPO_ROOT / "models" / "dfine" / "local",
    )


def _print_json(value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "normalize-detector":
            artifact = normalize_dfine_evaluation_log(
                plan=load_dfine_evaluation_plan(args.plan),
                log_path=args.log,
                output_path=args.output,
            )
            _print_json(
                {
                    "detector_report": str(args.output.resolve()),
                    "artifact": artifact.model_dump(mode="json"),
                }
            )
            return
        if args.command == "run-detector-evaluation":
            artifact, outcome, report_path = execute_dfine_detector_evaluation(
                plan=load_dfine_evaluation_plan(args.plan),
                workspace_root=REPO_ROOT,
                dfine_repository=args.dfine_repository,
                python_executable=args.python,
                runs_root=args.runs_root,
                gpu_index=args.gpu_index,
                batch_size=args.batch_size,
                workers=args.workers,
                max_gpu_minutes=args.max_gpu_minutes,
                active_analysis_probe=lambda: _active_analysis_probe(
                    args.event_store.resolve()
                ),
                execution_coordinator=ExecutionCoordinator(args.event_store.resolve()),
            )
            _print_json(
                {
                    "detector_report": str(report_path),
                    "elapsed_seconds": outcome.elapsed_seconds,
                    "artifact": artifact.model_dump(mode="json"),
                }
            )
            return
        if args.command == "run-shadow":
            repository = SqliteEventRepository(args.event_store)
            try:
                plan = load_shadow_plan(args.plan)
                candidate = repository.get_model_version(plan.model_version_id)
                if candidate is None:
                    raise ModelRegistryError(
                        "MODEL_VERSION_NOT_FOUND",
                        f"model version bulunamadı: {plan.model_version_id}",
                    )
                outputs = asyncio.run(
                    execute_shadow_evaluation(
                        plan=plan,
                        candidate=candidate,
                        workspace_root=REPO_ROOT,
                        runs_root=args.runs_root,
                        max_minutes=args.max_minutes,
                        active_analysis_probe=lambda: _active_analysis_probe(
                            args.event_store.resolve()
                        ),
                        execution_coordinator=ExecutionCoordinator(
                            args.event_store.resolve()
                        ),
                    )
                )
                _print_json({"shadow_artifacts": [str(path) for path in outputs]})
            finally:
                repository.close()
            return
        training_policy, promotion_policy = _load_policies(args.policy)
        repository = SqliteEventRepository(args.event_store)
        try:
            if args.command == "plan":
                job = _training_service(repository, args, training_policy).plan(
                    dataset_manifest_path=args.dataset_manifest,
                    dfine_repository=args.dfine_repository,
                    base_checkpoint=args.base_checkpoint,
                    architecture=args.architecture,
                    requested_by=args.requested_by,
                    epochs=args.epochs,
                    batch_size=args.batch_size,
                    workers=args.workers,
                    gpu_index=args.gpu_index,
                    max_gpu_minutes=args.max_gpu_minutes,
                    seed=args.seed,
                )
                _print_json(job)
            elif args.command == "run":
                job, version = _training_service(
                    repository, args, training_policy
                ).execute(
                    args.job_id,
                    dfine_repository=args.dfine_repository,
                    base_checkpoint=args.base_checkpoint,
                    python_executable=args.python,
                )
                _print_json(
                    {
                        "job": job.model_dump(mode="json"),
                        "candidate": version.model_dump(mode="json"),
                    }
                )
            elif args.command == "evaluate":
                report = DfineEvaluationReport.model_validate_json(
                    args.report.read_text(encoding="utf-8")
                )
                version = _registry(repository).record_evaluation(
                    args.model_version_id, **report.model_dump()
                )
                _print_json(version)
            elif args.command == "evaluate-artifacts":
                candidate = repository.get_model_version(args.model_version_id)
                if candidate is None:
                    raise ModelRegistryError(
                        "MODEL_VERSION_NOT_FOUND",
                        f"model version bulunamadı: {args.model_version_id}",
                    )
                output = args.output or (
                    REPO_ROOT
                    / "runs"
                    / "dfine-evaluations"
                    / f"{args.model_version_id}.json"
                )
                report = build_dfine_evaluation_report(
                    candidate=candidate,
                    test_dataset_manifest=load_dataset_manifest(
                        args.test_dataset_manifest
                    ),
                    detector_report_path=args.detector_report,
                    e2e_artifact_paths=args.e2e_artifact,
                    evaluator=args.evaluator,
                    output_path=output,
                )
                version = _registry(repository).record_evaluation(
                    args.model_version_id, **report.model_dump()
                )
                _print_json(
                    {
                        "report": str(output.resolve()),
                        "model_version": version.model_dump(mode="json"),
                    }
                )
            elif args.command == "prepare-evaluation":
                candidate = repository.get_model_version(args.model_version_id)
                if candidate is None:
                    raise ModelRegistryError(
                        "MODEL_VERSION_NOT_FOUND",
                        f"model version bulunamadı: {args.model_version_id}",
                    )
                training_job = repository.get_training_job(candidate.training_job_id)
                if training_job is None:
                    raise ModelRegistryError(
                        "TRAINING_JOB_NOT_FOUND",
                        f"training job bulunamadı: {candidate.training_job_id}",
                    )
                plan = prepare_dfine_detector_evaluation(
                    candidate=candidate,
                    test_dataset_manifest=load_dataset_manifest(
                        args.test_dataset_manifest
                    ),
                    workspace_root=REPO_ROOT,
                    dfine_repository=args.dfine_repository,
                    coco_annotations=args.coco_annotations,
                    frame_root=args.frame_root,
                    code_revision=inspect_project_revision(REPO_ROOT),
                    created_by=args.created_by,
                    expected_category_names=training_job.category_names,
                )
                output = args.output or (
                    args.runs_root
                    / "dfine-evaluations"
                    / plan.plan_id
                    / "evaluation-plan.json"
                )
                write_dfine_evaluation_plan(output, plan)
                command = build_dfine_test_command(
                    plan=plan,
                    workspace_root=REPO_ROOT,
                    dfine_repository=args.dfine_repository,
                    python_executable=args.python,
                    output_dir=(
                        args.runs_root / "dfine-evaluations" / plan.plan_id / "detector"
                    ),
                    batch_size=args.batch_size,
                    workers=args.workers,
                )
                _print_json(
                    {
                        "evaluation_plan": str(output.resolve()),
                        "plan": plan.model_dump(mode="json"),
                        "verified_test_command_argv": command,
                    }
                )
            elif args.command == "export-onnx":
                candidate = repository.get_model_version(args.model_version_id)
                if candidate is None:
                    raise ModelRegistryError(
                        "MODEL_VERSION_NOT_FOUND",
                        f"model version bulunamadı: {args.model_version_id}",
                    )
                training_job = repository.get_training_job(candidate.training_job_id)
                if training_job is None:
                    raise ModelRegistryError(
                        "TRAINING_JOB_NOT_FOUND",
                        f"training job bulunamadı: {candidate.training_job_id}",
                    )
                saved, outcome, log_path = execute_dfine_onnx_export(
                    repository=repository,
                    candidate=candidate,
                    training_job=training_job,
                    workspace_root=REPO_ROOT,
                    dfine_repository=args.dfine_repository,
                    python_executable=args.python,
                    runs_root=args.runs_root,
                    registry_root=args.registry_root,
                    max_minutes=args.max_minutes,
                    active_analysis_probe=lambda: _active_analysis_probe(
                        args.event_store.resolve()
                    ),
                )
                _print_json(
                    {
                        "model_version": saved.model_dump(mode="json"),
                        "elapsed_seconds": outcome.elapsed_seconds,
                        "export_log": str(log_path),
                    }
                )
            elif args.command == "prepare-shadow":
                candidate = repository.get_model_version(args.model_version_id)
                if candidate is None:
                    raise ModelRegistryError(
                        "MODEL_VERSION_NOT_FOUND",
                        f"model version bulunamadı: {args.model_version_id}",
                    )
                case_manifest = load_shadow_case_manifest(args.case_manifest)
                plan = prepare_shadow_evaluation(
                    candidate=candidate,
                    test_dataset_manifest=load_dataset_manifest(
                        args.test_dataset_manifest
                    ),
                    case_manifest=case_manifest,
                    case_manifest_path=args.case_manifest,
                    workspace_root=REPO_ROOT,
                    media_root=args.media_root,
                    code_revision=inspect_project_revision(REPO_ROOT),
                    created_by=args.created_by,
                )
                output = args.output or (
                    REPO_ROOT
                    / "runs"
                    / "dfine-evaluations"
                    / plan.plan_id
                    / "shadow-plan.json"
                )
                write_shadow_plan(output, plan)
                _print_json(
                    {
                        "shadow_plan": str(output.resolve()),
                        "plan": plan.model_dump(mode="json"),
                    }
                )
            elif args.command == "promote":
                version = _registry(repository).promote(
                    args.model_version_id,
                    policy=promotion_policy,
                    approved_by=args.approved_by,
                    reason=args.reason,
                )
                _print_json(version)
            elif args.command == "rollback":
                version = _registry(repository).rollback_failed_champion(
                    args.failed_model_version_id,
                    failure_code=args.failure_code,
                    failure_detail=args.failure_detail,
                )
                _print_json(version)
            elif args.command == "health-check":
                version = _registry(repository).reconcile_active_manifest()
                _print_json(
                    {
                        "active_model": version.model_dump(mode="json")
                        if version
                        else None
                    }
                )
            else:
                _print_json(
                    {
                        "training_jobs": [
                            item.model_dump(mode="json")
                            for item in repository.list_training_jobs()
                        ],
                        "model_versions": [
                            item.model_dump(mode="json")
                            for item in repository.list_model_versions()
                        ],
                    }
                )
        finally:
            repository.close()
    except (
        DfineTrainingError,
        EvaluationReportError,
        ModelRegistryError,
        ValidationError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        reasons = getattr(exc, "reasons", [])
        parser.error(f"{code}: {exc}" + (f" ({'; '.join(reasons)})" if reasons else ""))


if __name__ == "__main__":
    main()
