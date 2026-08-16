"""Local dataset indexing, hashing, and training-eligibility gates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import ValidationError

from ..domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)

_UCF_CLASS_DIRS = {
    "Abuse",
    "Arrest",
    "Arson",
    "Assault",
    "Burglary",
    "Explosion",
    "Fighting",
    "RoadAccidents",
    "Robbery",
    "Shooting",
    "Shoplifting",
    "Stealing",
    "Vandalism",
    "Testing_Normal_Videos_Anomaly",
    "Training_Normal_Videos_Anomaly",
    "z_Normal_Videos_event",
}
_VIDEO_EXTENSIONS = {".mp4"}


@dataclass(frozen=True)
class _AnnotationRef:
    video_id: str
    source_ref: str
    split: DatasetSplit
    annotation_ref: str
    annotation_sha256: str


def sha256_file(path: Path, *, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_ucf_videos_root(dataset_root: Path) -> Path:
    root = dataset_root.resolve()
    videos_root = root / "Videos" if (root / "Videos").is_dir() else root
    if not videos_root.is_dir():
        raise ValueError(f"UCF-Crime video kökü bulunamadı: {dataset_root}")
    if not any((videos_root / name).is_dir() for name in _UCF_CLASS_DIRS):
        raise ValueError(
            "verilen yol UCF-Crime video kökü değil; beklenen sınıf dizinleri bulunamadı"
        )
    return videos_root


def build_ucf_crime_manifest(
    dataset_root: Path,
    *,
    annotation_dir: Path | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
) -> OfflineDatasetManifest:
    """Index a local UCF-Crime copy without authorising training or redistribution."""

    videos_root = resolve_ucf_videos_root(dataset_root)
    annotations = _load_annotation_index(annotation_dir) if annotation_dir else {}
    files = sorted(
        (
            path
            for path in videos_root.rglob("*")
            if path.is_file() and path.suffix.casefold() in _VIDEO_EXTENSIONS
        ),
        key=lambda path: path.relative_to(videos_root).as_posix(),
    )
    if not files:
        raise ValueError("UCF-Crime video kökünde MP4 bulunamadı")

    entries: list[DatasetVideoRecord] = []
    indexed_refs: set[str] = set()
    for index, path in enumerate(files, 1):
        if path.is_symlink():
            raise ValueError(f"dataset symlink içeremez: {path}")
        resolved = path.resolve()
        if not resolved.is_relative_to(videos_root):
            raise ValueError(f"dataset videosu kök dışına çıkıyor: {path}")
        source_ref = resolved.relative_to(videos_root).as_posix()
        annotation = annotations.get(source_ref)
        split = annotation.split if annotation else DatasetSplit.UNASSIGNED
        entries.append(
            DatasetVideoRecord(
                dataset_video_id=f"ucf-crime/{PurePosixPath(source_ref).with_suffix('')}",
                source_ref=source_ref,
                source_label=PurePosixPath(source_ref).parts[0],
                split=split,
                file_size_bytes=resolved.stat().st_size,
                file_sha256=sha256_file(resolved),
                annotation_ref=annotation.annotation_ref if annotation else None,
                annotation_sha256=annotation.annotation_sha256 if annotation else None,
                allowed_uses=[DatasetUse.BENCHMARK],
            )
        )
        indexed_refs.add(source_ref)
        if progress is not None:
            progress(index, len(files), resolved)

    missing = sorted(set(annotations) - indexed_refs)
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"annotation videosu yerel arşivde bulunamadı: {preview}")

    fingerprint = calculate_dataset_fingerprint(entries)
    return OfflineDatasetManifest(
        dataset_id="ucf-crime",
        source_name="UCF-Crime",
        source_url="https://www.crcv.ucf.edu/projects/real-world/",
        citation=(
            "Sultani, Chen and Shah. Real-world Anomaly Detection in Surveillance Videos. "
            "CVPR 2018."
        ),
        license_status=DatasetLicenseStatus.UNVERIFIED,
        license_id=None,
        redistribution_allowed=False,
        training_allowed=False,
        allowed_uses=[DatasetUse.BENCHMARK],
        entries=entries,
        dataset_fingerprint=fingerprint,
    )


def write_dataset_manifest(path: Path, manifest: OfflineDatasetManifest) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def load_dataset_manifest(path: Path) -> OfflineDatasetManifest:
    try:
        return OfflineDatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise ValueError(f"dataset manifest okunamadı: {path}: {exc}") from exc


def verify_training_sources(
    manifest: OfflineDatasetManifest,
    video_root: Path,
    expected_splits: Mapping[str, str],
) -> None:
    """Fail closed before training and re-hash each referenced source video."""

    if not manifest.training_allowed or DatasetUse.TRAINING not in manifest.allowed_uses:
        raise ValueError(
            f"dataset training için onaylı değil: {manifest.dataset_id} "
            f"(license_status={manifest.license_status.value})"
        )
    root = video_root.resolve()
    if not root.is_dir():
        raise ValueError(f"video root bulunamadı: {video_root}")
    entries = {item.source_ref: item for item in manifest.entries}
    for source_ref, raw_split in expected_splits.items():
        entry = entries.get(source_ref)
        if entry is None:
            raise ValueError(f"training videosu dataset manifestinde yok: {source_ref}")
        split = DatasetSplit(raw_split)
        if entry.split != split:
            raise ValueError(
                f"annotation split manifest ile eşleşmiyor: {source_ref}: "
                f"{split.value} != {entry.split.value}"
            )
        if split == DatasetSplit.TEST or DatasetUse.TRAINING not in entry.allowed_uses:
            raise ValueError(f"video training kullanımına açık değil: {source_ref}")
        source = (root / source_ref).resolve()
        if not source.is_relative_to(root) or not source.is_file() or source.is_symlink():
            raise ValueError(f"training videosu bulunamadı veya güvensiz: {source_ref}")
        if source.stat().st_size != entry.file_size_bytes:
            raise ValueError(f"training videosu boyutu değişti: {source_ref}")
        if sha256_file(source) != entry.file_sha256:
            raise ValueError(f"training videosu SHA-256 değeri değişti: {source_ref}")


def _load_annotation_index(annotation_dir: Path) -> dict[str, _AnnotationRef]:
    root = annotation_dir.resolve()
    if not root.is_dir():
        raise ValueError(f"annotation dizini bulunamadı: {annotation_dir}")
    index: dict[str, _AnnotationRef] = {}
    video_ids: set[str] = set()
    for path in sorted(root.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            raw = path.read_bytes()
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"annotation okunamadı: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"annotation object olmalıdır: {path.name}")
        video_id = payload.get("video_id")
        source_ref = payload.get("source_ref")
        split = payload.get("split")
        if not isinstance(video_id, str) or not video_id:
            raise ValueError(f"annotation video_id geçersiz: {path.name}")
        if not isinstance(source_ref, str) or not _safe_reference(source_ref):
            raise ValueError(f"annotation source_ref geçersiz: {path.name}")
        try:
            parsed_split = DatasetSplit(split)
        except ValueError as exc:
            raise ValueError(f"annotation split geçersiz: {path.name}") from exc
        if parsed_split == DatasetSplit.UNASSIGNED:
            raise ValueError(f"annotation split unassigned olamaz: {path.name}")
        if video_id in video_ids or source_ref in index:
            raise ValueError(f"annotation video veya source_ref tekrar ediyor: {path.name}")
        video_ids.add(video_id)
        index[source_ref] = _AnnotationRef(
            video_id=video_id,
            source_ref=source_ref,
            split=parsed_split,
            annotation_ref=path.name,
            annotation_sha256=hashlib.sha256(raw).hexdigest(),
        )
    return index


def _safe_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
        and value == posix.as_posix()
    )
