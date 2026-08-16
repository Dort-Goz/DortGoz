"""Controlled event-to-D-FINE training sample bridge tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.dataset import (
    DatasetLicenseStatus,
    DatasetSplit,
    DatasetUse,
    DatasetVideoRecord,
    OfflineDatasetManifest,
    calculate_dataset_fingerprint,
)
from dortgoz.domain.event import EventStatus, VerifiedEvent
from dortgoz.domain.evidence import VerifiedEventType
from dortgoz.domain.feedback import (
    DevelopmentApproval,
    DevelopmentApprovalStatus,
    DevelopmentUse,
)
from dortgoz.domain.provenance import AnalysisProvenance, HumanReview, ReviewDecision
from dortgoz.domain.training import (
    FrameReviewResult,
    TrainingSampleStatus,
    VerifiedBoundingBox,
)
from dortgoz.domain.video import VideoMetadata
from dortgoz.repositories.memory import InMemoryEventRepository
from dortgoz.services.coco_export import training_reviews_from_samples
from dortgoz.services.dataset_manifest import write_dataset_manifest
from dortgoz.services.training_sample import (
    TrainingSampleError,
    TrainingSampleService,
    jpeg_dimensions,
)

VIDEO_ID = "00000000-0000-0000-0000-000000000201"
ANALYSIS_ID = "analysis-training-sample"
EVENT_ID = "event-training-sample"


def _jpeg(width: int = 640, height: int = 360, marker: bytes = b"frame") -> bytes:
    components = b"\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    sof = b"\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + components
    comment = b"\xff\xfe" + (len(marker) + 2).to_bytes(2, "big") + marker
    return b"\xff\xd8\xff\xc0" + (len(sof) + 2).to_bytes(2, "big") + sof + comment + b"\xff\xd9"


def _training_manifest(video_payload: bytes) -> OfflineDatasetManifest:
    entry = DatasetVideoRecord(
        dataset_video_id="owned/train-video",
        source_ref="videos/source.mp4",
        source_label="owned",
        split=DatasetSplit.TRAIN,
        file_size_bytes=len(video_payload),
        file_sha256=hashlib.sha256(video_payload).hexdigest(),
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
    )
    return OfflineDatasetManifest(
        dataset_id="owned-approved",
        source_name="Team-owned fixture",
        source_url="https://example.invalid/owned",
        citation="Team-owned test fixture.",
        license_status=DatasetLicenseStatus.VERIFIED,
        license_id="Apache-2.0",
        redistribution_allowed=True,
        training_allowed=True,
        allowed_uses=[DatasetUse.TRAINING, DatasetUse.EVALUATION],
        entries=[entry],
        dataset_fingerprint=calculate_dataset_fingerprint([entry]),
    )


def _benchmark_manifest(video_payload: bytes) -> OfflineDatasetManifest:
    entry = DatasetVideoRecord(
        dataset_video_id="ucf/train-video",
        source_ref="Fighting/Fighting001.mp4",
        source_label="Fighting",
        split=DatasetSplit.TRAIN,
        file_size_bytes=len(video_payload),
        file_sha256=hashlib.sha256(video_payload).hexdigest(),
        allowed_uses=[DatasetUse.BENCHMARK],
    )
    return OfflineDatasetManifest(
        dataset_id="ucf-crime",
        source_name="UCF-Crime",
        source_url="https://www.crcv.ucf.edu/projects/real-world/",
        citation="Benchmark fixture.",
        license_status=DatasetLicenseStatus.UNVERIFIED,
        license_id=None,
        redistribution_allowed=False,
        training_allowed=False,
        allowed_uses=[DatasetUse.BENCHMARK],
        entries=[entry],
        dataset_fingerprint=calculate_dataset_fingerprint([entry]),
    )


def _repository(video_payload: bytes) -> tuple[InMemoryEventRepository, DevelopmentApproval]:
    repository = InMemoryEventRepository()
    metadata = VideoMetadata(
        video_id=VIDEO_ID,
        original_filename="owned.mp4",
        stored_filename=f"{VIDEO_ID}.mp4",
        media_path=f"{VIDEO_ID}.mp4",
        file_size_bytes=len(video_payload),
        file_hash_sha256=hashlib.sha256(video_payload).hexdigest(),
        container="mov",
        codec="h264",
        width=1280,
        height=720,
        fps=25,
        duration_seconds=60,
        has_audio=False,
        time_base="1/12800",
    )
    candidate = CandidateEvent(
        candidate_id="candidate-training-sample",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        start_time=10,
        peak_time=12,
        end_time=15,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.8,
        anomaly_score=0.8,
        trigger_signals=["fixture"],
        screening_model_id="fixture-screening",
        threshold_version="test-v1",
    )
    repository.create_video(metadata)
    repository.create_analysis(
        VIDEO_ID,
        AnalysisProvenance(
            contract_version="1.0.0",
            config_version="test-v1",
            code_revision="test-revision",
        ),
        analysis_id=ANALYSIS_ID,
    )
    repository.save_candidate(candidate)
    repository.save_event(
        VerifiedEvent(
            event_id=EVENT_ID,
            analysis_id=ANALYSIS_ID,
            video_id=VIDEO_ID,
            candidate_id=candidate.candidate_id,
            status=EventStatus.HUMAN_REVIEW,
            event_type=VerifiedEventType.PHYSICAL_FIGHT,
            start_time=10,
            peak_time=12,
            end_time=15,
            confidence=0.8,
        )
    )
    review = repository.save_review(
        HumanReview(
            review_id="review-training-sample",
            event_id=EVENT_ID,
            decision=ReviewDecision.EDIT,
            event_type=VerifiedEventType.PHYSICAL_FIGHT.value,
            start_time=10,
            peak_time=12,
            end_time=15,
            note="Olay aralığı insan tarafından doğrulandı.",
            reviewer="operator",
            revision=1,
        )
    )
    approval = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-training-sample",
            event_id=EVENT_ID,
            review_id=review.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.D_FINE_TRAINING],
            reviewer="operator",
            note="D-FINE kutu incelemesi için kullanılabilir.",
        )
    )
    return repository, approval


def _service(
    tmp_path: Path,
    repository: InMemoryEventRepository,
    fetcher,
) -> TrainingSampleService:
    return TrainingSampleService(
        repository,
        media_root=tmp_path / "media",
        dataset_manifest_root=tmp_path / "manifests",
        frame_root=tmp_path / "media" / "_training_samples",
        frame_fetcher=fetcher,
    )


def _write_inputs(tmp_path: Path, video_payload: bytes, manifest: OfflineDatasetManifest) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / f"{VIDEO_ID}.mp4").write_bytes(video_payload)
    write_dataset_manifest(tmp_path / "manifests" / "approved.json", manifest)


@pytest.mark.asyncio
async def test_approved_event_prepares_idempotent_frames_and_verifies_boxes(
    tmp_path: Path,
) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    manifest = _training_manifest(video_payload)
    _write_inputs(tmp_path, video_payload, manifest)
    calls: list[float] = []

    async def fetcher(_video: Path, timestamp: float, width: int) -> bytes:
        assert width == 640
        calls.append(timestamp)
        return _jpeg(marker=f"frame-{timestamp}".encode())

    service = _service(tmp_path, repository, fetcher)
    samples = await service.prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
    )
    repeated = await service.prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
    )

    assert calls == [10, 12, 15]
    assert [sample.sample_id for sample in repeated] == [sample.sample_id for sample in samples]
    assert len(samples) == 3
    assert all(sample.status == TrainingSampleStatus.PENDING_REVIEW for sample in samples)
    assert all(sample.event_revision == 2 for sample in samples)
    assert all((tmp_path / "media" / sample.frame_ref).is_file() for sample in samples)
    assert all((sample.image_width, sample.image_height) == (640, 360) for sample in samples)

    with pytest.raises(TrainingSampleError) as invalid_review:
        service.verify(
            samples[0].sample_id,
            review_result=FrameReviewResult.VERIFIED_BOXES,
            boxes=[],
            reviewer="operator",
            annotation_tool="Dortgoz UI",
        )
    assert invalid_review.value.code == "TRAINING_FRAME_REVIEW_INVALID"
    assert repository.get_training_sample(samples[0].sample_id).status == (
        TrainingSampleStatus.PENDING_REVIEW
    )

    verified = service.verify(
        samples[1].sample_id,
        review_result=FrameReviewResult.VERIFIED_BOXES,
        boxes=[VerifiedBoundingBox(category_name="person", x=20, y=30, width=100, height=200)],
        reviewer="operator",
        annotation_tool="Dortgoz UI",
    )

    assert verified.status == TrainingSampleStatus.VERIFIED
    assert verified.frame_review is not None
    assert verified.frame_review.annotation_id == verified.sample_id
    assert training_reviews_from_samples(repository.list_training_samples(), manifest) == [
        verified.frame_review
    ]


@pytest.mark.asyncio
async def test_duplicate_captured_frames_are_reduced_to_one_sample(tmp_path: Path) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    _write_inputs(tmp_path, video_payload, _training_manifest(video_payload))

    async def fetcher(_video: Path, _timestamp: float, _width: int) -> bytes:
        return _jpeg(marker=b"same-frame")

    samples = await _service(tmp_path, repository, fetcher).prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
    )

    assert len(samples) == 1


@pytest.mark.asyncio
async def test_benchmark_only_manifest_is_rejected_before_frame_capture(tmp_path: Path) -> None:
    video_payload = b"ucf-video"
    repository, approval = _repository(video_payload)
    _write_inputs(tmp_path, video_payload, _benchmark_manifest(video_payload))
    called = False

    async def fetcher(_video: Path, _timestamp: float, _width: int) -> bytes:
        nonlocal called
        called = True
        return _jpeg()

    with pytest.raises(TrainingSampleError) as error:
        await _service(tmp_path, repository, fetcher).prepare(
            EVENT_ID,
            approval.approval_id,
            "approved.json",
            prepared_by="operator",
        )

    assert error.value.code == "TRAINING_DATASET_REJECTED"
    assert called is False
    assert repository.list_training_samples() == []


@pytest.mark.asyncio
async def test_machine_without_event_video_fails_without_creating_samples(
    tmp_path: Path,
) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    write_dataset_manifest(
        tmp_path / "manifests" / "approved.json",
        _training_manifest(video_payload),
    )

    async def fetcher(_video: Path, _timestamp: float, _width: int) -> bytes:
        raise AssertionError("medya yokken frame fetcher çağrılmamalıdır")

    with pytest.raises(TrainingSampleError) as missing:
        await _service(tmp_path, repository, fetcher).prepare(
            EVENT_ID,
            approval.approval_id,
            "approved.json",
            prepared_by="operator",
        )

    assert missing.value.code == "TRAINING_MEDIA_MISSING"
    assert repository.list_training_samples() == []


@pytest.mark.asyncio
async def test_changed_review_or_frame_blocks_training_verification(tmp_path: Path) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    _write_inputs(tmp_path, video_payload, _training_manifest(video_payload))

    async def fetcher(_video: Path, timestamp: float, _width: int) -> bytes:
        return _jpeg(marker=f"frame-{timestamp}".encode())

    service = _service(tmp_path, repository, fetcher)
    samples = await service.prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
        timestamps=[12],
    )
    frame = tmp_path / "media" / samples[0].frame_ref
    frame.write_bytes(_jpeg(marker=b"tampered-frame"))

    with pytest.raises(TrainingSampleError) as changed:
        service.verify(
            samples[0].sample_id,
            review_result=FrameReviewResult.VERIFIED_NO_TARGET_OBJECTS,
            boxes=[],
            reviewer="operator",
            annotation_tool="Dortgoz UI",
        )
    assert changed.value.code == "TRAINING_FRAME_CHANGED"

    repository.save_review(
        HumanReview(
            review_id="review-newer",
            event_id=EVENT_ID,
            decision=ReviewDecision.EDIT,
            note="Olay tekrar incelendi.",
            reviewer="operator-2",
            revision=1,
        )
    )
    invalidated = repository.get_training_sample(samples[0].sample_id)
    assert invalidated.status == TrainingSampleStatus.REVOKED
    assert invalidated.invalidated_by_review_id == "review-newer"
    with pytest.raises(TrainingSampleError) as stale:
        await service.prepare(
            EVENT_ID,
            approval.approval_id,
            "approved.json",
            prepared_by="operator",
        )
    assert stale.value.code == "TRAINING_REVIEW_STALE"


@pytest.mark.asyncio
async def test_revoked_approval_invalidates_prepared_and_verified_samples(
    tmp_path: Path,
) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    manifest = _training_manifest(video_payload)
    _write_inputs(tmp_path, video_payload, manifest)

    async def fetcher(_video: Path, timestamp: float, _width: int) -> bytes:
        return _jpeg(marker=f"frame-{timestamp}".encode())

    service = _service(tmp_path, repository, fetcher)
    samples = await service.prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
        timestamps=[12, 13],
    )
    service.verify(
        samples[0].sample_id,
        review_result=FrameReviewResult.VERIFIED_NO_TARGET_OBJECTS,
        boxes=[],
        reviewer="operator",
        annotation_tool="Dortgoz UI",
    )
    revocation = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-revocation",
            event_id=EVENT_ID,
            review_id=approval.review_id,
            status=DevelopmentApprovalStatus.REVOKED,
            approved_uses=[],
            reviewer="operator",
            note="Eğitim izni geri çekildi.",
            supersedes_approval_id=approval.approval_id,
        )
    )

    revoked = repository.list_training_samples(EVENT_ID)
    assert all(sample.status == TrainingSampleStatus.REVOKED for sample in revoked)
    assert all(sample.revoked_by_approval_id == revocation.approval_id for sample in revoked)
    with pytest.raises(ValueError, match="doğrulanmış training sample bulunamadı"):
        training_reviews_from_samples(revoked, manifest)


@pytest.mark.asyncio
async def test_superseding_approval_also_invalidates_old_samples(tmp_path: Path) -> None:
    video_payload = b"team-owned-video"
    repository, approval = _repository(video_payload)
    _write_inputs(tmp_path, video_payload, _training_manifest(video_payload))

    async def fetcher(_video: Path, timestamp: float, _width: int) -> bytes:
        return _jpeg(marker=f"frame-{timestamp}".encode())

    samples = await _service(tmp_path, repository, fetcher).prepare(
        EVENT_ID,
        approval.approval_id,
        "approved.json",
        prepared_by="operator",
        timestamps=[12],
    )
    replacement = repository.save_development_approval(
        DevelopmentApproval(
            approval_id="approval-evaluation-only",
            event_id=EVENT_ID,
            review_id=approval.review_id,
            status=DevelopmentApprovalStatus.APPROVED,
            approved_uses=[DevelopmentUse.EVALUATION],
            reviewer="operator",
            note="Yeni karar yalnız değerlendirmeye izin verir.",
            supersedes_approval_id=approval.approval_id,
        )
    )

    stored = repository.get_training_sample(samples[0].sample_id)
    assert stored.status == TrainingSampleStatus.REVOKED
    assert stored.revoked_by_approval_id == replacement.approval_id


def test_jpeg_dimension_reader_rejects_non_jpeg() -> None:
    assert jpeg_dimensions(_jpeg(320, 180)) == (320, 180)
    with pytest.raises(TrainingSampleError) as error:
        jpeg_dimensions(b"not-a-jpeg")
    assert error.value.code == "TRAINING_FRAME_INVALID"
