#!/usr/bin/env python3
"""Plan, run, evaluate, promote, or roll back a controlled D-FINE model."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from dortgoz.domain.model_lifecycle import (  # noqa: E402
    DfineArchitecture,
    DfineTrainingPolicy,
    PromotionPolicy,
)
from dortgoz.repositories.sqlite import SqliteEventRepository  # noqa: E402
from dortgoz.services.dataset_manifest import load_dataset_manifest  # noqa: E402
from dortgoz.services.dfine_training import (  # noqa: E402
    DfineTrainingError,
    DfineTrainingService,
)
from dortgoz.services.evaluation_report import (  # noqa: E402
    DfineEvaluationReport,
    EvaluationReportError,
    build_dfine_evaluation_report,
)
from dortgoz.services.model_registry import (  # noqa: E402
    ModelRegistryError,
    ModelRegistryService,
)


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--event-store", type=_path, required=True)
    parser.add_argument(
        "--policy",
        type=_path,
        default=REPO_ROOT / "configs" / "dfine_feedback_training.json",
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

    run = commands.add_parser("run", help="kuyruktaki işi yerel CUDA worker'da çalıştır")
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
    artifacts.add_argument(
        "--e2e-artifact", type=_path, action="append", required=True
    )
    artifacts.add_argument("--evaluator", required=True)
    artifacts.add_argument("--output", type=_path)

    promote = commands.add_parser("promote", help="kapıyı geçen candidate'ı champion yap")
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
        active_analysis_probe=lambda: _active_analysis_probe(args.event_store.resolve()),
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
                job, version = _training_service(repository, args, training_policy).execute(
                    args.job_id,
                    dfine_repository=args.dfine_repository,
                    base_checkpoint=args.base_checkpoint,
                    python_executable=args.python,
                )
                _print_json({"job": job.model_dump(mode="json"), "candidate": version.model_dump(mode="json")})
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
                    {"active_model": version.model_dump(mode="json") if version else None}
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
