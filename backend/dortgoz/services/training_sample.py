"""Prepare and verify D-FINE frames behind explicit human approval gates."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from ..domain.dataset import DatasetSplit, DatasetUse, DatasetVideoRecord
from ..domain.feedback import DevelopmentApprovalStatus, DevelopmentUse
from ..domain.training import (
    FrameReviewResult,
    TrainingFrameReview,
    TrainingSample,
    TrainingSampleStatus,
    VerifiedBoundingBox,
)
from ..pipeline.ingest import grab_frame
from ..repositories.protocols import EventRepository
from .coco_export import ensure_training_manifest_allowed
from .dataset_manifest import load_dataset_manifest, sha256_file

TrainingFrameFetcher = Callable[[Path, float, int], Awaitable[bytes]]


class TrainingSampleError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TrainingSampleService:
    """Bind reviewed events to local frames without bypassing dataset policy."""

    def __init__(
        self,
        repository: EventRepository,
        *,
        media_root: Path,
        dataset_manifest_root: Path,
        frame_root: Path,
        frame_width: int = 640,
        frame_fetcher: TrainingFrameFetcher | None = None,
    ) -> None:
        self.repository = repository
        self.media_root = media_root.resolve()
        self.dataset_manifest_root = dataset_manifest_root.resolve()
        self.frame_root = frame_root.resolve()
        if not self.frame_root.is_relative_to(self.media_root):
            raise ValueError("training frame kökü media kökü içinde olmalıdır")
        if frame_width <= 0:
            raise ValueError("training frame genişliği pozitif olmalıdır")
        self.frame_width = frame_width
        self.frame_fetcher = frame_fetcher or _fetch_frame

    async def prepare(
        self,
        event_id: str,
        approval_id: str,
        dataset_manifest_name: str,
        *,
        prepared_by: str,
        timestamps: list[float] | None = None,
    ) -> list[TrainingSample]:
        event, approval = self._active_approval(event_id, approval_id)
        video = self.repository.get_video(event.video_id)
        if video is None:
            raise TrainingSampleError("TRAINING_VIDEO_MISSING", "Olay videosu bulunamadı.")
        try:
            manifest = load_dataset_manifest(self._resolve_manifest(dataset_manifest_name))
        except TrainingSampleError:
            raise
        except ValueError as exc:
            raise TrainingSampleError(
                "TRAINING_MANIFEST_INVALID", str(exc), status_code=422
            ) from exc
        try:
            ensure_training_manifest_allowed(manifest)
        except ValueError as exc:
            raise TrainingSampleError(
                "TRAINING_DATASET_REJECTED", str(exc), status_code=422
            ) from exc
        matches = [
            entry for entry in manifest.entries if entry.file_sha256 == video.file_hash_sha256
        ]
        if len(matches) != 1:
            raise TrainingSampleError(
                "TRAINING_DATASET_VIDEO_MISMATCH",
                "Olay videosu dataset manifestinde tek bir hash kaydıyla eşleşmelidir.",
                status_code=422,
            )
        entry = matches[0]
        self._validate_dataset_entry(entry, video.file_size_bytes)
        video_path = await asyncio.to_thread(self._verified_video_path, video.media_path, entry)
        choices = self._frame_choices(event, video.duration_seconds, video.fps, timestamps)
        existing = {
            sample.sample_id: sample for sample in self.repository.list_training_samples(event_id)
        }
        selected: list[TrainingSample] = []
        pending_choices: list[tuple[str, float, str]] = []
        for reason, timestamp in choices:
            sample_id = _sample_id(approval.approval_id, manifest.dataset_fingerprint, timestamp)
            current = existing.get(sample_id)
            if current is not None:
                selected.append(current)
            else:
                pending_choices.append((reason, timestamp, sample_id))
        if not pending_choices:
            return sorted(selected, key=lambda item: (item.timestamp_seconds, item.sample_id))

        try:
            payloads = await asyncio.gather(
                *(
                    self.frame_fetcher(video_path, timestamp, self.frame_width)
                    for _, timestamp, _ in pending_choices
                )
            )
        except Exception as exc:
            raise TrainingSampleError(
                "TRAINING_FRAME_CAPTURE_FAILED", "Olay kareleri üretilemedi."
            ) from exc
        now = datetime.now(UTC)
        new_samples: list[TrainingSample] = []
        targets: list[Path] = []
        unique_payloads: list[bytes] = []
        seen_frame_hashes = {sample.frame_sha256 for sample in selected}
        for (reason, timestamp, sample_id), jpeg in zip(pending_choices, payloads):
            width, height = jpeg_dimensions(jpeg)
            frame_sha256 = hashlib.sha256(jpeg).hexdigest()
            if frame_sha256 in seen_frame_hashes:
                continue
            seen_frame_hashes.add(frame_sha256)
            target = (self.frame_root / f"{sample_id}.jpg").resolve()
            if not target.is_relative_to(self.frame_root):
                raise TrainingSampleError(
                    "TRAINING_FRAME_PATH_REJECTED", "Training frame yolu güvenli değil."
                )
            frame_ref = target.relative_to(self.media_root).as_posix()
            new_samples.append(
                TrainingSample(
                    sample_id=sample_id,
                    event_id=event.event_id,
                    event_revision=event.revision,
                    review_id=approval.review_id,
                    approval_id=approval.approval_id,
                    video_id=video.video_id,
                    source_video_sha256=video.file_hash_sha256,
                    dataset_id=manifest.dataset_id,
                    dataset_fingerprint=manifest.dataset_fingerprint,
                    dataset_video_id=entry.dataset_video_id,
                    source_video_ref=entry.source_ref,
                    split=entry.split,
                    timestamp_seconds=timestamp,
                    selection_reason=reason,
                    frame_ref=frame_ref,
                    frame_sha256=frame_sha256,
                    frame_size_bytes=len(jpeg),
                    image_width=width,
                    image_height=height,
                    status=TrainingSampleStatus.PENDING_REVIEW,
                    prepared_by=prepared_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            targets.append(target)
            unique_payloads.append(jpeg)

        if not new_samples:
            return sorted(selected, key=lambda item: (item.timestamp_seconds, item.sample_id))

        written: list[Path] = []
        try:
            for target, payload in zip(targets, unique_payloads):
                existed = target.exists()
                await asyncio.to_thread(_atomic_write, target, payload)
                if not existed:
                    written.append(target)
            stored = self.repository.create_training_samples(new_samples)
        except Exception:
            for target in written:
                target.unlink(missing_ok=True)
            raise
        return sorted(
            [*selected, *stored], key=lambda item: (item.timestamp_seconds, item.sample_id)
        )

    def verify(
        self,
        sample_id: str,
        *,
        review_result: FrameReviewResult,
        boxes: list[VerifiedBoundingBox],
        reviewer: str,
        annotation_tool: str,
    ) -> TrainingSample:
        sample = self.repository.get_training_sample(sample_id)
        if sample is None:
            raise TrainingSampleError(
                "TRAINING_SAMPLE_NOT_FOUND", "Training sample bulunamadı.", status_code=404
            )
        event, approval = self._active_approval(sample.event_id, sample.approval_id)
        if event.revision != sample.event_revision or approval.review_id != sample.review_id:
            raise TrainingSampleError(
                "TRAINING_SAMPLE_STALE",
                "Olay veya insan incelemesi değişti. Kare yeniden hazırlanmalıdır.",
            )
        frame_path = (self.media_root / sample.frame_ref).resolve()
        if (
            not frame_path.is_relative_to(self.frame_root)
            or not frame_path.is_file()
            or frame_path.is_symlink()
        ):
            raise TrainingSampleError(
                "TRAINING_FRAME_MISSING", "Training frame bulunamadı veya güvenli değil."
            )
        if frame_path.stat().st_size != sample.frame_size_bytes:
            raise TrainingSampleError("TRAINING_FRAME_CHANGED", "Training frame boyutu değişti.")
        payload = frame_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != sample.frame_sha256:
            raise TrainingSampleError(
                "TRAINING_FRAME_CHANGED", "Training frame hash değeri değişti."
            )
        if jpeg_dimensions(payload) != (sample.image_width, sample.image_height):
            raise TrainingSampleError("TRAINING_FRAME_CHANGED", "Training frame boyutları değişti.")
        try:
            review = TrainingFrameReview(
                annotation_id=sample.sample_id,
                dataset_id=sample.dataset_id,
                dataset_fingerprint=sample.dataset_fingerprint,
                dataset_video_id=sample.dataset_video_id,
                source_video_ref=sample.source_video_ref,
                frame_ref=sample.frame_ref,
                frame_sha256=sample.frame_sha256,
                frame_size_bytes=sample.frame_size_bytes,
                timestamp_seconds=sample.timestamp_seconds,
                image_width=sample.image_width,
                image_height=sample.image_height,
                split=sample.split,
                review_result=review_result,
                boxes=boxes,
                human_verified=True,
                reviewer=reviewer,
                annotation_tool=annotation_tool,
                reviewed_at=datetime.now(UTC),
            )
        except (ValidationError, ValueError) as exc:
            raise TrainingSampleError(
                "TRAINING_FRAME_REVIEW_INVALID", str(exc), status_code=422
            ) from exc
        return self.repository.verify_training_sample(sample_id, review)

    def _active_approval(self, event_id: str, approval_id: str):
        event = self.repository.get_event(event_id)
        if event is None:
            raise TrainingSampleError(
                "TRAINING_EVENT_NOT_FOUND", "Olay bulunamadı.", status_code=404
            )
        approvals = self.repository.list_development_approvals(event_id)
        latest = approvals[-1] if approvals else None
        if (
            latest is None
            or latest.approval_id != approval_id
            or latest.status != DevelopmentApprovalStatus.APPROVED
            or DevelopmentUse.D_FINE_TRAINING not in latest.approved_uses
        ):
            raise TrainingSampleError(
                "TRAINING_APPROVAL_REQUIRED",
                "Etkin ve en güncel D-FINE development onayı zorunludur.",
            )
        if event.review is None or event.review.review_id != latest.review_id:
            raise TrainingSampleError(
                "TRAINING_REVIEW_STALE",
                "Development onayı olayın en güncel insan incelemesine bağlı olmalıdır.",
            )
        return event, latest

    def _resolve_manifest(self, name: str) -> Path:
        posix = PurePosixPath(name.replace("\\", "/"))
        windows = PureWindowsPath(name)
        if (
            not name
            or posix.name != name
            or windows.name != name
            or windows.drive
            or Path(name).suffix.casefold() != ".json"
        ):
            raise TrainingSampleError(
                "TRAINING_MANIFEST_PATH_REJECTED",
                "Dataset manifest adı güvenli bir JSON dosya adı olmalıdır.",
                status_code=422,
            )
        path = (self.dataset_manifest_root / name).resolve()
        if not path.is_relative_to(self.dataset_manifest_root) or not path.is_file():
            raise TrainingSampleError(
                "TRAINING_MANIFEST_NOT_FOUND", "Dataset manifest bulunamadı.", status_code=404
            )
        return path

    def _verified_video_path(self, media_path: str, entry: DatasetVideoRecord) -> Path:
        path = (self.media_root / media_path).resolve()
        if not path.is_relative_to(self.media_root) or not path.is_file() or path.is_symlink():
            raise TrainingSampleError(
                "TRAINING_MEDIA_MISSING", "Olay videosu bu bilgisayarda bulunamadı."
            )
        if path.stat().st_size != entry.file_size_bytes:
            raise TrainingSampleError(
                "TRAINING_MEDIA_CHANGED", "Olay videosunun boyutu dataset kaydıyla eşleşmiyor."
            )
        if sha256_file(path) != entry.file_sha256:
            raise TrainingSampleError(
                "TRAINING_MEDIA_CHANGED", "Olay videosunun hash değeri dataset kaydıyla eşleşmiyor."
            )
        return path

    @staticmethod
    def _validate_dataset_entry(entry: DatasetVideoRecord, video_size: int) -> None:
        if (
            entry.split not in {DatasetSplit.TRAIN, DatasetSplit.VALIDATION}
            or DatasetUse.TRAINING not in entry.allowed_uses
            or entry.file_size_bytes != video_size
        ):
            raise TrainingSampleError(
                "TRAINING_DATASET_VIDEO_REJECTED",
                "Dataset video kaydı training kullanımına uygun değil.",
                status_code=422,
            )

    @staticmethod
    def _frame_choices(event, duration: float, fps: float, timestamps: list[float] | None):
        if event.start_time is None or event.peak_time is None or event.end_time is None:
            raise TrainingSampleError(
                "TRAINING_EVENT_TIME_MISSING",
                "Kare hazırlamak için doğrulanmış başlangıç, zirve ve bitiş zamanı gerekir.",
            )
        if timestamps is not None and not 1 <= len(timestamps) <= 9:
            raise TrainingSampleError(
                "TRAINING_TIMESTAMPS_INVALID", "Bir ile dokuz kare zamanı seçilmelidir."
            )
        raw = (
            [("operator_selected", value) for value in timestamps]
            if timestamps is not None
            else [
                ("event_start", event.start_time),
                ("event_peak", event.peak_time),
                ("event_end", event.end_time),
            ]
        )
        last_frame = max(0.0, duration - 1.0 / fps)
        choices: list[tuple[str, float]] = []
        seen: set[float] = set()
        for reason, value in raw:
            if not event.start_time <= value <= event.end_time or value > duration:
                raise TrainingSampleError(
                    "TRAINING_TIMESTAMP_OUTSIDE_EVENT",
                    "Seçilen kare zamanı doğrulanmış olay aralığı içinde olmalıdır.",
                    status_code=422,
                )
            timestamp = round(min(value, last_frame), 3)
            if timestamp not in seen:
                seen.add(timestamp)
                choices.append((reason, timestamp))
        return choices


def jpeg_dimensions(payload: bytes) -> tuple[int, int]:
    """Read JPEG SOF dimensions without adding an image-library dependency."""

    if len(payload) < 4 or payload[:2] != b"\xff\xd8":
        raise TrainingSampleError("TRAINING_FRAME_INVALID", "Training frame geçerli JPEG değil.")
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    index = 2
    while index + 1 < len(payload):
        if payload[index] != 0xFF:
            index += 1
            continue
        while index < len(payload) and payload[index] == 0xFF:
            index += 1
        if index >= len(payload):
            break
        marker = payload[index]
        index += 1
        if marker in {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}:
            continue
        if index + 2 > len(payload):
            break
        segment_length = int.from_bytes(payload[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(payload):
            break
        if marker in sof_markers:
            if segment_length < 7:
                break
            height = int.from_bytes(payload[index + 3 : index + 5], "big")
            width = int.from_bytes(payload[index + 5 : index + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        index += segment_length
    raise TrainingSampleError("TRAINING_FRAME_INVALID", "JPEG görüntü boyutları okunamadı.")


async def _fetch_frame(video: Path, timestamp: float, width: int) -> bytes:
    return await grab_frame(video, timestamp, width=width)


def _sample_id(approval_id: str, dataset_fingerprint: str, timestamp: float) -> str:
    key = f"dortgoz:dfine:{approval_id}:{dataset_fingerprint}:{timestamp:.3f}"
    return str(uuid5(NAMESPACE_URL, key))


def _atomic_write(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)


__all__ = ["TrainingSampleError", "TrainingSampleService", "jpeg_dimensions"]
