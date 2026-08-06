"""Görev 08 candidate-only local VLM ve media artifact sözleşme testleri."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dortgoz.agent.actions import AgentAction
from dortgoz.agent.orchestrator import EventOrchestrator
from dortgoz.agent.policy import RoutingConfig, decide_next_action
from dortgoz.agent.state import EventAgentState
from dortgoz.domain.candidate import CandidateEvent, CandidateType
from dortgoz.domain.context import ContextClip, KeyframeRef
from dortgoz.domain.evidence import VLMStatus
from dortgoz.domain.video import VideoMetadata
from dortgoz.infrastructure.model_client import LocalModelClientError
from dortgoz.tools.context_clip import LocalContextClipTool
from dortgoz.tools.keyframes import LocalKeyframeTool
from dortgoz.tools.local_vlm import LocalVlmManifest, LocalVlmTool, load_local_vlm_manifest
from dortgoz.tools.mock_agent import MockAgentTools
from dortgoz.tools.protocols import ToolExecutionError, VlmSchemaError

ANALYSIS_ID = "analysis-task-08"
VIDEO_ID = "00000000-0000-0000-0000-000000000081"


def metadata() -> VideoMetadata:
    return VideoMetadata(
        video_id=VIDEO_ID,
        original_filename="fixture.mp4",
        stored_filename=f"{VIDEO_ID}.mp4",
        media_path=f"{VIDEO_ID}.mp4",
        file_size_bytes=1024,
        file_hash_sha256="d" * 64,
        container="mov",
        codec="h264",
        width=640,
        height=480,
        fps=25,
        duration_seconds=12,
        has_audio=False,
        time_base="1/12800",
    )


def candidate() -> CandidateEvent:
    return CandidateEvent(
        candidate_id="candidate-task-08",
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        start_time=2,
        peak_time=4,
        end_time=6,
        candidate_type=CandidateType.POSSIBLE_FIGHT,
        peak_score=0.9,
        anomaly_score=0.9,
        trigger_signals=["anomaly"],
        screening_model_id="fixture-screening",
        threshold_version="fixture-thresholds",
    )


def manifest() -> LocalVlmManifest:
    return LocalVlmManifest(
        model_id="fixture-local-vlm",
        model_version="1.0.0",
        artifact_path="C:/local/model.gguf",
        artifact_sha256="e" * 64,
        license="Apache-2.0",
        source="fixture local weights",
        prompt_version="candidate-vlm-v1",
    )


class FakeClient:
    def __init__(self, raw: str | Exception) -> None:
        self.raw = raw
        self.calls: list[dict] = []

    async def complete_json(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


def make_context_and_frames(workspace: Path) -> tuple[ContextClip, list[KeyframeRef]]:
    payloads = {"before": b"before-jpeg", "peak": b"peak-jpeg", "after": b"after-jpeg"}
    timestamps = {"before": 2.0, "peak": 4.0, "after": 6.0}
    frames: list[KeyframeRef] = []
    for label, payload in payloads.items():
        target = workspace / "runs" / ANALYSIS_ID / "candidate-task-08" / "frames" / f"{label}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        frames.append(
            KeyframeRef(
                frame_id=f"candidate-task-08-{label}",
                timestamp=timestamps[label],
                frame_path=target.relative_to(workspace).as_posix(),
                hash_sha256=hashlib.sha256(payload).hexdigest(),
                selection_reason=label,
            )
        )
    return (
        ContextClip(
            candidate_id="candidate-task-08",
            clip_start=2,
            clip_end=6,
            clip_path="runs/analysis-task-08/candidate-task-08/candidate-context.mp4",
            frame_count=8,
            fps=2,
            hash_sha256="f" * 64,
        ),
        frames,
    )


@pytest.mark.asyncio
async def test_local_vlm_uses_only_candidate_frames_and_records_provenance(tmp_path: Path) -> None:
    context, frames = make_context_and_frames(tmp_path)
    raw = json.dumps(
        {
            "event_type": "unknown_anomaly",
            "status": "confirmed",
            "confidence": 0.87,
            "start_time": 2.0,
            "peak_time": 4.0,
            "end_time": 6.0,
            "before": "Hareket başlıyor.",
            "during": "Olağan dışı hareket görülüyor.",
            "after": "Hareket azalıyor.",
            "evidence": [
                {
                    "timestamp": 4.0,
                    "frame_id": "candidate-task-08-peak",
                    "claim": "Tepe karesinde olağan dışı hareket görülüyor.",
                }
            ],
            "uncertainties": [],
        }
    )
    client = FakeClient(raw)
    tool = LocalVlmTool(
        client=client,
        manifest=manifest(),
        workspace_root=tmp_path,
        timeout_seconds=5,
    )

    result = await tool.verify(candidate(), context, frames, attempt=1, strict_schema=False)

    assert result.status == VLMStatus.CONFIRMED
    assert result.model_id == "fixture-local-vlm"
    assert result.artifact_sha256 == "e" * 64
    assert result.model_license == "Apache-2.0"
    content = client.calls[0]["messages"][1]["content"]
    assert sum(part["type"] == "image_url" for part in content) == 3
    assert "candidate-task-08-peak" in content[0]["text"]
    assert "fixture.mp4" not in json.dumps(content)
    schema = json.dumps(client.calls[0]["schema"])
    assert '"$defs"' not in schema
    assert '"$ref"' not in schema


@pytest.mark.asyncio
async def test_invalid_or_unavailable_vlm_result_is_typed(tmp_path: Path) -> None:
    context, frames = make_context_and_frames(tmp_path)
    malformed = LocalVlmTool(
        client=FakeClient("{not-json"),
        manifest=manifest(),
        workspace_root=tmp_path,
        timeout_seconds=5,
    )
    with pytest.raises(VlmSchemaError, match="geçersiz"):
        await malformed.verify(candidate(), context, frames, attempt=1, strict_schema=False)

    unavailable = LocalVlmTool(
        client=FakeClient(LocalModelClientError("MODEL_UNAVAILABLE", "offline")),
        manifest=manifest(),
        workspace_root=tmp_path,
        timeout_seconds=5,
    )
    with pytest.raises(ToolExecutionError, match="offline") as raised:
        await unavailable.verify(candidate(), context, frames, attempt=1, strict_schema=False)
    assert raised.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_local_keyframes_and_context_are_written_under_runs(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / metadata().media_path).write_bytes(b"video")

    async def fetch(_: Path, timestamp: float) -> bytes:
        return f"jpeg:{timestamp}".encode()

    async def write_clip(_: Path, target: Path, *__: float) -> None:
        target.write_bytes(b"clip")

    context_tool = LocalContextClipTool(
        media_root=media_root,
        workspace_root=tmp_path,
        clip_writer=write_clip,
    )
    context = await context_tool.create(
        metadata(),
        candidate(),
        analysis_id=ANALYSIS_ID,
        before_seconds=2,
        after_seconds=3,
        expanded=True,
    )
    keyframes = await LocalKeyframeTool(
        media_root=media_root,
        workspace_root=tmp_path,
        frame_fetcher=fetch,
    ).capture(
        metadata(),
        candidate(),
        analysis_id=ANALYSIS_ID,
        clip_start=context.clip_start,
        clip_end=context.clip_end,
    )

    assert context.clip_path.startswith("runs/")
    assert context.expanded is True
    assert [frame.frame_id.rsplit("-", 1)[-1] for frame in keyframes] == ["before", "peak", "after"]
    assert all((tmp_path / frame.frame_path).is_file() for frame in keyframes)


@pytest.mark.asyncio
async def test_first_schema_failure_allows_one_strict_retry_then_review() -> None:
    class InvalidVlmTools(MockAgentTools):
        async def run_vlm(self, state: EventAgentState, *, strict_schema: bool) -> EventAgentState:
            raise VlmSchemaError("fixture invalid output")

    initial = EventAgentState(
        analysis_id=ANALYSIS_ID,
        video_id=VIDEO_ID,
        candidate_id=candidate().candidate_id,
        candidate=candidate(),
        video_duration=12,
        image_quality=0.9,
    )
    orchestrator = EventOrchestrator(InvalidVlmTools(), RoutingConfig())

    first = await orchestrator.step(initial)
    assert first.vlm_attempts == 1
    assert first.validation is not None and not first.validation.schema_valid
    assert decide_next_action(first, RoutingConfig()).action == AgentAction.RETRY_VLM_STRICT

    second = await orchestrator.step(first)
    assert second.vlm_attempts == 2
    assert decide_next_action(second, RoutingConfig()).action == AgentAction.REQUEST_HUMAN_REVIEW


def test_vlm_manifest_requires_exact_local_weight_hash(tmp_path: Path) -> None:
    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"local-weights")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "model_id": "fixture-local-vlm",
                "model_version": "1.0.0",
                "artifact_path": str(weights),
                "artifact_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
                "license": "MIT",
                "source": "fixture",
                "prompt_version": "candidate-vlm-v1",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_local_vlm_manifest(manifest_path)
    assert loaded.model_id == "fixture-local-vlm"

    weights.write_bytes(b"tampered")
    with pytest.raises(ToolExecutionError, match="hash"):
        load_local_vlm_manifest(manifest_path)
