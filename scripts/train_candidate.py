"""Yerel candidate artifact üreticisi.

İki açık mod vardır:

* ``baseline``: önceki deterministic motion referansını tekrar üretir.
* ``temporal-cnn``: yalnız proje annotation şemasındaki video-bazlı train ve
  validation bölmelerinden 1-D temporal CNN eğitir.

Video klipleri ve dış veri sete ait ağırlıklar repoya yazılmaz. CNN çıktısı
git-dışı ``models/candidate/local/`` altında üretilir; manifest SHA-256 ve
lisansını kaydeder. Etkinleştirmek için manifest yolu yalnız local ortam
değişkeniyle verilir.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.pipeline.ingest import motion_profile
from dortgoz.pipeline.temporal_cnn import (  # noqa: E402
    TemporalCnnCandidateModel,
    TemporalCnnTrainingExample,
    evaluate_temporal_cnn,
    train_temporal_cnn,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AnnotationRecord:
    video_id: str
    source_ref: str
    split: str
    intervals: tuple[tuple[float, float], ...]


def build_artifact(activity_scale: float, interaction_scale: float) -> dict:
    if activity_scale <= 0 or interaction_scale <= 0:
        raise ValueError("scale değerleri pozitif olmalı")
    return {
        "model_id": "motion-baseline-v1",
        "version": "1.0.0",
        "activity_scale": activity_scale,
        "interaction_scale": interaction_scale,
        "feature_schema": ["changed", "fg", "mad", "activity"],
        "license": "MIT",
    }


def write_baseline(output_dir: Path, activity_scale: float, interaction_scale: float) -> Path:
    """Geriye uyumlu motion baseline artifact üretimi."""

    output_dir = output_dir.resolve()
    try:
        output_dir.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("candidate output repo kökü altında olmalı") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = build_artifact(activity_scale, interaction_scale)
    artifact_path = output_dir / "motion-baseline-v1.json"
    _write_json(artifact_path, artifact)
    manifest = {
        "model_id": "motion-baseline-v1",
        "version": "1.0.0",
        "model_type": "motion_baseline",
        "artifact_path": artifact_path.relative_to(REPO_ROOT).as_posix(),
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "license": "MIT",
        "input_fps": 1.0,
        "feature_schema": artifact["feature_schema"],
        "notes": "Ölçülebilir referans; learned candidate CNN yerine geçmez.",
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def load_annotation_records(annotation_dir: Path) -> list[AnnotationRecord]:
    """Project annotation JSON'larını strict biçimde okur.

    UCA'nın dil açıklamaları bu şemaya otomatik çevrilmez: zaman damgalı cümle,
    candidate/anomali etiketi değildir. Bu koruma normal davranışı pozitif diye
    öğretme ve train/validation leakage riskini önler.
    """

    if not annotation_dir.is_dir():
        raise ValueError(f"annotation dizini bulunamadı: {annotation_dir}")
    records: list[AnnotationRecord] = []
    for path in sorted(annotation_dir.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"annotation okunamadı: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"annotation object olmalı: {path.name}")
        required = {"video_id", "source_ref", "split", "intervals"}
        if set(payload) != required:
            raise ValueError(f"annotation alanları tam olarak {sorted(required)} olmalı: {path.name}")
        video_id = payload["video_id"]
        source_ref = payload["source_ref"]
        split = payload["split"]
        intervals = payload["intervals"]
        if not isinstance(video_id, str) or not video_id:
            raise ValueError(f"annotation video_id geçersiz: {path.name}")
        if not isinstance(source_ref, str) or not _is_safe_reference(source_ref):
            raise ValueError(f"annotation source_ref local video root dışında: {path.name}")
        if split not in {"train", "validation", "test"}:
            raise ValueError(f"annotation split geçersiz: {path.name}")
        if not isinstance(intervals, list):
            raise ValueError(f"annotation intervals list olmalı: {path.name}")
        parsed_intervals: list[tuple[float, float]] = []
        for interval in intervals:
            if not isinstance(interval, dict) or set(interval) != {"start_time", "end_time", "label"}:
                raise ValueError(f"annotation interval şeması geçersiz: {path.name}")
            start, end, label = interval["start_time"], interval["end_time"], interval["label"]
            if (
                not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or start < 0
                or end <= start
                or not isinstance(label, str)
                or not label
            ):
                raise ValueError(f"annotation interval değeri geçersiz: {path.name}")
            parsed_intervals.append((float(start), float(end)))
        records.append(
            AnnotationRecord(video_id, source_ref, split, tuple(parsed_intervals))
        )
    if not records:
        raise ValueError("temporal CNN için annotation JSON bulunamadı")
    if len({record.video_id for record in records}) != len(records):
        raise ValueError("aynı video_id birden fazla annotation dosyasında bulunamaz")
    return records


async def build_examples(
    records: list[AnnotationRecord], *, video_root: Path, base_fps: float
) -> list[TemporalCnnTrainingExample]:
    root = video_root.resolve()
    if not root.is_dir():
        raise ValueError(f"video root bulunamadı: {video_root}")
    examples: list[TemporalCnnTrainingExample] = []
    for record in records:
        source = (root / record.source_ref).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError(f"annotation videosu bulunamadı: {record.source_ref}")
        profile = await motion_profile(source, base_fps=base_fps)
        if not profile:
            raise ValueError(f"motion profile boş: {record.source_ref}")
        examples.append(
            TemporalCnnTrainingExample(record.video_id, profile, record.intervals)
        )
    return examples


def train_and_write_temporal_cnn(
    *,
    annotation_dir: Path,
    video_root: Path,
    output_dir: Path,
    model_id: str,
    version: str,
    base_fps: float,
    kernel_size: int,
    epochs: int,
    learning_rate: float,
    l2: float,
    seed: int,
    artifact_license: str,
    min_validation_recall: float,
) -> Path:
    records = load_annotation_records(annotation_dir)
    train_records = [record for record in records if record.split == "train"]
    validation_records = [record for record in records if record.split == "validation"]
    if not train_records or not validation_records:
        raise ValueError("temporal CNN için ayrı train ve validation video bölmeleri zorunlu")
    if {record.video_id for record in train_records} & {record.video_id for record in validation_records}:
        raise ValueError("train ve validation video_id kümeleri ayrık olmalı")
    train_examples = asyncio.run(build_examples(train_records, video_root=video_root, base_fps=base_fps))
    validation_examples = asyncio.run(
        build_examples(validation_records, video_root=video_root, base_fps=base_fps)
    )
    model, train_metrics = train_temporal_cnn(
        train_examples,
        model_id=model_id,
        version=version,
        kernel_size=kernel_size,
        epochs=epochs,
        learning_rate=learning_rate,
        l2=l2,
        seed=seed,
        artifact_license=artifact_license,
    )
    validation_metrics = evaluate_temporal_cnn(model, validation_examples)
    if validation_metrics.recall_at_half < min_validation_recall:
        raise ValueError(
            "validation recall kabul eşiğinin altında: "
            f"{validation_metrics.recall_at_half:.3f} < {min_validation_recall:.3f}"
        )
    return write_temporal_cnn(
        output_dir,
        model,
        input_fps=base_fps,
        train_metrics=train_metrics,
        validation_metrics=validation_metrics,
    )


def write_temporal_cnn(
    output_dir: Path,
    model: TemporalCnnCandidateModel,
    *,
    input_fps: float,
    train_metrics: object,
    validation_metrics: object,
) -> Path:
    """Temporal CNN artifact + hash'li manifest'i git-dışı local dizine yazar."""

    if input_fps <= 0:
        raise ValueError("input_fps pozitif olmalı")
    target = output_dir.resolve()
    try:
        target.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("candidate output repo kökü altında olmalı") from exc
    target.mkdir(parents=True, exist_ok=True)
    artifact_path = target / f"{model.model_id}.json"
    _write_json(artifact_path, model.artifact.model_dump(mode="json"))
    artifact_ref = artifact_path.relative_to(REPO_ROOT).as_posix()
    manifest = {
        "model_id": model.model_id,
        "version": model.artifact.version,
        "model_type": "temporal_cnn",
        "artifact_path": artifact_ref,
        "artifact_sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "license": model.artifact.license,
        "input_fps": input_fps,
        "feature_schema": list(model.artifact.feature_schema),
        "notes": (
            "Yerel project annotation bölmesiyle eğitilmiş temporal CNN. "
            f"train={_metrics_payload(train_metrics)}; validation={_metrics_payload(validation_metrics)}"
        ),
    }
    manifest_path = target / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _is_safe_reference(value: str) -> bool:
    reference = PurePath(value)
    return not reference.is_absolute() and ".." not in reference.parts and value == reference.as_posix()


def _metrics_payload(metrics: object) -> str:
    fields = ("sample_count", "positive_count", "mean_loss", "recall_at_half", "false_positive_rate_at_half")
    return json.dumps({field: getattr(metrics, field) for field in fields}, separators=(",", ":"))


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "temporal-cnn"), default="baseline")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--activity-scale", type=float, default=4.0)
    parser.add_argument("--interaction-scale", type=float, default=5.0)
    parser.add_argument("--annotations-dir", type=Path)
    parser.add_argument("--video-root", type=Path)
    parser.add_argument("--model-id", default="temporal-cnn-v1")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--base-fps", type=float, default=1.0)
    parser.add_argument("--kernel-size", type=int, default=3)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--learning-rate", type=float, default=0.35)
    parser.add_argument("--l2", type=float, default=0.0001)
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument("--artifact-license", choices=("Apache-2.0", "MIT"))
    parser.add_argument("--min-validation-recall", type=float, default=0.95)
    args = parser.parse_args()
    if args.mode == "baseline":
        output_dir = args.output_dir or Path("models/candidate")
        print(write_baseline(output_dir, args.activity_scale, args.interaction_scale))
        return
    if args.annotations_dir is None or args.video_root is None:
        parser.error("temporal-cnn için --annotations-dir ve --video-root zorunlu")
    if args.artifact_license is None:
        parser.error("temporal-cnn için --artifact-license zorunlu")
    if not 0 <= args.min_validation_recall <= 1:
        parser.error("--min-validation-recall 0 ile 1 arasında olmalı")
    print(
        train_and_write_temporal_cnn(
            annotation_dir=args.annotations_dir,
            video_root=args.video_root,
            output_dir=args.output_dir or Path("models/candidate/local") / args.model_id,
            model_id=args.model_id,
            version=args.version,
            base_fps=args.base_fps,
            kernel_size=args.kernel_size,
            epochs=args.epochs,
            learning_rate=args.learning_rate,
            l2=args.l2,
            seed=args.seed,
            artifact_license=args.artifact_license,
            min_validation_recall=args.min_validation_recall,
        )
    )


if __name__ == "__main__":
    main()
