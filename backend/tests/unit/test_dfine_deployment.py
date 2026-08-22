"""Production D-FINE ONNX export and runtime-contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from dortgoz.config import settings
from dortgoz.domain.model_lifecycle import (
    DfineArchitecture,
    ModelVersion,
    TrainingJob,
    TrainingJobStatus,
)
from dortgoz.pipeline import onnx_ep
from dortgoz.pipeline.perception import (
    SIZE,
    _Detector,
    resolve_production_model_path,
    set_detector_override,
)
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.coco_export import CATEGORY_ID_BASE
from dortgoz.services.dataset_manifest import sha256_file
from dortgoz.services.dfine_deployment import (
    DfineOnnxContract,
    execute_dfine_onnx_export,
    verify_dfine_deployment,
)
from dortgoz.services.dfine_training import ProcessOutcome
from dortgoz.services.evaluation_report import EvaluationReportError


def _dfine_repository(root: Path) -> tuple[Path, str]:
    repository = root / "D-FINE"
    config = repository / "configs" / "dfine" / "custom"
    exporter = repository / "tools" / "deployment"
    config.mkdir(parents=True)
    exporter.mkdir(parents=True)
    (repository / "LICENSE").write_text(
        "Apache License\nVersion 2.0, January 2004\n", encoding="utf-8"
    )
    (repository / "train.py").write_text("# fixture\n", encoding="utf-8")
    (config / "dfine_hgnetv2_s_custom.yml").write_text("epochs: 10\n", encoding="utf-8")
    (exporter / "export_onnx.py").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=repository,
        check=True,
    )
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, revision


def _write_export_manifest(workspace: Path, *, category_id_base: int = CATEGORY_ID_BASE) -> Path:
    manifest = workspace / "runs" / "training" / "dataset" / "export_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "export_version": "1.0.0",
                "categories": ["person", "weapon"],
                "category_id_base": category_id_base,
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _candidate_fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkpoint = workspace / "runs" / "training" / "best.pth"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"candidate-checkpoint")
    _write_export_manifest(workspace)
    dfine_repository, revision = _dfine_repository(tmp_path)
    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    job = TrainingJob(
        job_id="job-1",
        dataset_id="approved",
        dataset_fingerprint="a" * 64,
        export_fingerprint="b" * 64,
        export_ref="runs/training/dataset",
        architecture=DfineArchitecture.SMALL,
        category_names=["person", "weapon"],
        verified_frame_count=100,
        train_frame_count=80,
        validation_frame_count=20,
        source_video_count=10,
        box_count=50,
        dfine_repository_revision=revision,
        base_checkpoint_sha256="c" * 64,
        epochs=10,
        batch_size=2,
        max_gpu_minutes=30,
        daily_gpu_minutes=60,
        status=TrainingJobStatus.SUCCEEDED,
        requested_by="operator",
        output_ref="runs/training",
        checkpoint_ref="runs/training/best.pth",
        checkpoint_sha256=sha256_file(checkpoint),
        started_at=now,
        finished_at=now + timedelta(minutes=1),
        elapsed_seconds=60,
        created_at=now,
        updated_at=now + timedelta(minutes=1),
        revision=3,
    )
    repository = InMemoryEventRepository()
    queued = TrainingJob.model_validate(
        {
            **job.model_dump(),
            "status": TrainingJobStatus.QUEUED,
            "checkpoint_ref": None,
            "checkpoint_sha256": None,
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": 0,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
    )
    repository.create_training_job(queued)
    running = TrainingJob.model_validate(
        {
            **queued.model_dump(),
            "status": TrainingJobStatus.RUNNING,
            "started_at": now,
            "revision": 2,
        }
    )
    repository.update_training_job(running)
    repository.update_training_job(job)
    candidate = repository.create_model_version(
        ModelVersion(
            model_version_id="candidate-1",
            training_job_id=job.job_id,
            architecture=job.architecture,
            checkpoint_ref=job.checkpoint_ref or "",
            checkpoint_sha256=job.checkpoint_sha256 or "",
            dataset_fingerprint=job.dataset_fingerprint,
            export_fingerprint=job.export_fingerprint,
            dfine_repository_revision=job.dfine_repository_revision,
            created_at=job.finished_at,
            updated_at=job.finished_at,
        )
    )
    return workspace, repository, job, candidate, dfine_repository


class _Exporter:
    def run(self, argv: list[str], **kwargs) -> ProcessOutcome:
        checkpoint = Path(argv[argv.index("-r") + 1])
        checkpoint.with_suffix(".onnx").write_bytes(b"deployed-onnx")
        kwargs["log_path"].write_text("export complete\n", encoding="utf-8")
        return ProcessOutcome(exit_code=0, elapsed_seconds=3.5)


def test_export_attaches_hash_bound_production_onnx(tmp_path: Path) -> None:
    workspace, repository, job, candidate, dfine_repository = _candidate_fixture(tmp_path)

    saved, outcome, log_path = execute_dfine_onnx_export(
        repository=repository,
        candidate=candidate,
        training_job=job,
        workspace_root=workspace,
        dfine_repository=dfine_repository,
        python_executable=Path(sys.executable),
        runs_root=workspace / "runs",
        registry_root=workspace / "models" / "dfine" / "candidates",
        process_runner=_Exporter(),
        onnx_inspector=lambda _path, **_kwargs: DfineOnnxContract(
            input_names=["images", "orig_target_sizes"],
            output_names=["labels", "boxes", "scores"],
        ),
        now=datetime(2026, 8, 16, 13, 0, tzinfo=UTC),
    )

    assert outcome.elapsed_seconds == 3.5 and log_path.is_file()
    assert saved.deployment is not None
    assert saved.deployment.category_names == ["person", "weapon"]
    onnx = workspace / saved.deployment.onnx_ref
    assert onnx.read_bytes() == b"deployed-onnx"
    assert saved.deployment.onnx_sha256 == sha256_file(onnx)
    assert verify_dfine_deployment(candidate=saved, workspace_root=workspace) == onnx
    config = json.loads((onnx.parent / "config.json").read_text(encoding="utf-8"))
    assert config["id2label"] == {"0": "person", "1": "weapon"}
    assert config["interest_labels"] == ["person", "weapon"]
    assert min(int(key) for key in config["id2label"]) == CATEGORY_ID_BASE


def test_export_rejects_a_shifted_coco_category_id_base(tmp_path: Path) -> None:
    workspace, repository, job, candidate, dfine_repository = _candidate_fixture(tmp_path)
    _write_export_manifest(workspace, category_id_base=CATEGORY_ID_BASE + 1)

    with pytest.raises(EvaluationReportError) as rejected:
        execute_dfine_onnx_export(
            repository=repository,
            candidate=candidate,
            training_job=job,
            workspace_root=workspace,
            dfine_repository=dfine_repository,
            python_executable=Path(sys.executable),
            runs_root=workspace / "runs",
            registry_root=workspace / "models" / "dfine" / "candidates",
            process_runner=_Exporter(),
            onnx_inspector=lambda _path, **_kwargs: DfineOnnxContract(
                input_names=["images", "orig_target_sizes"],
                output_names=["labels", "boxes", "scores"],
            ),
        )

    assert rejected.value.code == "DFINE_CATEGORY_BASE_MISMATCH"
    assert repository.get_model_version(candidate.model_version_id).deployment is None


def test_deployment_verifier_rejects_changed_onnx(tmp_path: Path) -> None:
    workspace, repository, job, candidate, dfine_repository = _candidate_fixture(tmp_path)
    saved, _, _ = execute_dfine_onnx_export(
        repository=repository,
        candidate=candidate,
        training_job=job,
        workspace_root=workspace,
        dfine_repository=dfine_repository,
        python_executable=Path(sys.executable),
        runs_root=workspace / "runs",
        registry_root=workspace / "models" / "dfine" / "candidates",
        process_runner=_Exporter(),
        onnx_inspector=lambda _path, **_kwargs: DfineOnnxContract(
            input_names=["images", "orig_target_sizes"],
            output_names=["labels", "boxes", "scores"],
        ),
    )
    assert saved.deployment is not None
    (workspace / saved.deployment.onnx_ref).write_bytes(b"changed")

    with pytest.raises(EvaluationReportError) as rejected:
        verify_dfine_deployment(candidate=saved, workspace_root=workspace)
    assert rejected.value.code == "MODEL_ONNX_CHANGED"


class _Node:
    def __init__(self, name: str) -> None:
        self.name = name


class _DeployedSession:
    def get_inputs(self):
        return [_Node("images"), _Node("orig_target_sizes")]

    def get_outputs(self):
        return [_Node("labels"), _Node("boxes"), _Node("scores")]

    def run(self, output_names, feeds):
        assert output_names == ["labels", "boxes", "scores"]
        assert feeds["images"].shape == (1, 3, SIZE, SIZE)
        assert feeds["orig_target_sizes"].tolist() == [[SIZE, SIZE]]
        return (
            np.asarray([[1, 0]], dtype=np.int64),
            np.asarray([[[64, 128, 320, 384], [0, 0, 64, 64]]], dtype=np.float32),
            np.asarray([[0.9, 0.1]], dtype=np.float32),
        )


def test_production_detector_reads_official_deployed_contract(tmp_path: Path) -> None:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fixture")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "id2label": {"0": "person", "1": "weapon"},
                "interest_labels": ["person", "weapon"],
            }
        ),
        encoding="utf-8",
    )
    detector = _Detector(
        model,
        session_factory=lambda *_args, **_kwargs: _DeployedSession(),
    )

    detections = detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8), 0.5)

    assert len(detections) == 1
    detection = detections[0]
    assert detection.label == "weapon"
    assert detection.conf == pytest.approx(0.9)
    assert (detection.cx, detection.cy, detection.w, detection.h) == pytest.approx(
        (0.3, 0.4, 0.4, 0.4)
    )


def test_production_detector_applies_onnx_session_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fixture")
    (tmp_path / "config.json").write_text(
        json.dumps({"id2label": {"0": "person"}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_session(path, *, sess_options, providers):
        captured.update(
            path=path,
            sess_options=sess_options,
            providers=providers,
        )
        return _DeployedSession()

    monkeypatch.setattr(settings, "onnx_intra_threads", 4)
    monkeypatch.setattr(onnx_ep, "providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr("onnxruntime.InferenceSession", fake_session)

    _Detector(model)

    options = captured["sess_options"]
    assert captured["path"] == str(model)
    assert captured["providers"] == ["CPUExecutionProvider"]
    assert options.intra_op_num_threads == 4
    assert options.inter_op_num_threads == 1


def test_active_manifest_selects_only_hash_verified_onnx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    model = workspace / "models" / "dfine" / "candidates" / "v1" / "model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"champion-onnx")
    fingerprint = "f" * 64
    (model.parent / "config.json").write_text(
        json.dumps(
            {
                "id2label": {"0": "person", "1": "weapon"},
                "interest_labels": ["person", "weapon"],
                "onnx_sha256": sha256_file(model),
                "deployment_fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    manifest = workspace / "models" / "dfine" / "local" / "active_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "1.0.0",
                "onnx_ref": model.relative_to(workspace).as_posix(),
                "onnx_sha256": sha256_file(model),
                "deployment_fingerprint": fingerprint,
                "category_names": ["person", "weapon"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "dfine_workspace_root", workspace)
    monkeypatch.setattr(settings, "dfine_active_manifest", manifest)
    set_detector_override(None)

    assert resolve_production_model_path() == model.resolve()
    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        resolve_production_model_path()


def test_active_onnx_hash_is_reused_until_file_signature_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    model = workspace / "models" / "dfine" / "candidates" / "v1" / "model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"verified-onnx")
    fingerprint = "e" * 64
    expected_sha = sha256_file(model)
    (model.parent / "config.json").write_text(
        json.dumps(
            {
                "id2label": {"0": "person"},
                "interest_labels": ["person"],
                "onnx_sha256": expected_sha,
                "deployment_fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    manifest = workspace / "models" / "dfine" / "local" / "active_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "manifest_version": "1.0.0",
                "onnx_ref": model.relative_to(workspace).as_posix(),
                "onnx_sha256": expected_sha,
                "deployment_fingerprint": fingerprint,
                "category_names": ["person"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "dfine_workspace_root", workspace)
    monkeypatch.setattr(settings, "dfine_active_manifest", manifest)
    set_detector_override(None)

    from dortgoz.pipeline import perception

    real_hash = perception._sha256_file
    calls = 0

    def counted_hash(path: Path) -> str:
        nonlocal calls
        calls += 1
        return real_hash(path)

    monkeypatch.setattr(perception, "_sha256_file", counted_hash)

    assert resolve_production_model_path() == model.resolve()
    assert resolve_production_model_path() == model.resolve()
    assert resolve_production_model_path() == model.resolve()
    assert calls == 1


def test_export_adapter_injects_custom_class_contract(tmp_path: Path) -> None:
    official = tmp_path / "export_onnx.py"
    official.write_text(
        """
import json
from pathlib import Path

class YAMLConfig:
    def __init__(self, path, **kwargs):
        Path(__file__).with_name("adapter_call.json").write_text(
            json.dumps({"path": path, "kwargs": kwargs})
        )

def main(args):
    YAMLConfig(args.config, resume=args.resume)
    Path(args.resume).with_suffix(".onnx").write_bytes(b"fixture-onnx")
""".strip()
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yml"
    config.write_text("num_classes: 80\n", encoding="utf-8")
    checkpoint = tmp_path / "candidate.pth"
    checkpoint.write_bytes(b"checkpoint")
    adapter = Path(__file__).parents[3] / "scripts" / "dfine_export_adapter.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(adapter),
            "--official-exporter",
            str(official),
            "-c",
            str(config),
            "-r",
            str(checkpoint),
            "--num-classes",
            "2",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    call = json.loads((tmp_path / "adapter_call.json").read_text(encoding="utf-8"))
    assert call["kwargs"] == {
        "resume": str(checkpoint.resolve()),
        "num_classes": 2,
        "remap_mscoco_category": False,
    }
    assert checkpoint.with_suffix(".onnx").read_bytes() == b"fixture-onnx"
