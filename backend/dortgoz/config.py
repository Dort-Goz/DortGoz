

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_ENV_AUTHORITATIVE = {
    "DORTGOZ_LLAMA_BASE_URL",
    "DORTGOZ_API_KEY",
    "DORTGOZ_MAIN_MODEL",
    "DORTGOZ_VIDEO_MODEL",
    "DORTGOZ_SECOND_OPINION_MODEL",
    "DORTGOZ_AGENT_MODEL",
    "DORTGOZ_ROUTER_MODEL",
    "DORTGOZ_GUARD_MODEL",
    "DORTGOZ_EMBEDDING_MODEL",
    "DORTGOZ_QDRANT_URL",
    "DORTGOZ_QDRANT_PREFIX",
    "DORTGOZ_QDRANT_API_KEY",
}

if _ENV_PATH.is_file():
    for raw_line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in _ENV_AUTHORITATIVE:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


class Settings(BaseSettings):
    llama_base_url: str = "http://127.0.0.1:8080/v1"
    api_key: str = ""

    main_model: str = "llm-fast"
    video_model: str = "vlm"
    second_opinion_model: str = "llm-large"
    agent_model: str = "llm-fast"
    router_model: str = "router"
    guard_model: str = "guard"
    embedding_model: str = "bge-m3-embed"
    qdrant_url: str = ""
    qdrant_prefix: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "dortgoz-procedures"
    procedure_rag_top_k: int = Field(default=3, ge=1, le=10)

    mock: bool = False
    mock_speed: float = 1.0
    deployment_profile: Literal["development", "competition-real"] = "development"

    base_fps: float = 1.0
    window_seconds: float = 30.0
    dynamic_windows: bool = False
    window_min_seconds: float = 8.0
    window_preroll: float = 3.0
    window_quiet_tail: float = 6.0
    keyframes_per_window: int = 6
    keyframe_dedup: float = 0.008
    motion_gate_adaptive: bool = True
    motion_gate: float = 0.004
    interpret_max_tokens: int = 1400
    two_tier: bool = True
    carry_context: bool = True
    incident_review: bool = True
    incident_review_strict: bool = False
    incident_grace_windows: int = 1
    escalate_p: float = 0.10
    escalate_target_p: float = 0.0
    escalate_shadow: bool = True
    escalation_zoom_seconds: float = Field(default=0.0, ge=0.0, le=30.0)
    escalate_low_severity: bool = False
    shadow_sample_rate: float = 0.0
    category_rules_enabled: bool = False
    exemplar_suppress: bool = False
    exemplar_shadow: bool = True
    exemplar_threshold: float = 0.97
    second_opinion_motion: float = 0.30
    interpret_effort: str = ""
    second_opinion_effort: str = ""
    agent_effort: str = ""
    agent_think_budget: int = 1200
    interpret_think_budget: int = 2500
    interpret_think_temp: float = 0.0
    dual_read: bool = False
    final_sweep: bool = False
    max_inflight: int = 4
    llm_retries: int = 6




    onnx_device: str = "cpu"
    onnx_providers: str = ""
    onnx_intra_threads: int = 4
    local_inference_limit: int = Field(default=2, ge=1, le=8)
    migraphx_dir: str = ""
    adjudicate_confusable: str = "hirsizlik,kavga,saldiri,bilinmeyen,arac_kazasi"
    adjudicate_min_conf: float = 0.0
    adjudicate_frame_width: int = 512
    detector_enabled: bool = True
    dfine_onnx: str = ""
    dfine_active_manifest: Path = (
        Path(__file__).resolve().parents[2]
        / "models"
        / "dfine"
        / "local"
        / "active_manifest.json"
    )
    dfine_workspace_root: Path = Path(__file__).resolve().parents[2]
    detector_conf: float = 0.40
    detector_rescue_conf: float = 0.25
    detector_samples: int = 4

    video_max_bytes: int = 2 * 1024 * 1024 * 1024
    max_agent_steps: int = 14
    max_vlm_attempts: int = 2
    max_context_expansions: int = 1
    max_dense_analyses: int = 1
    quality_min: float = 0.35
    medium_candidate_score: float = 0.45
    high_candidate_score: float = 0.70
    critical_candidate_score: float = 0.85
    cv_only_confidence: float = 0.92
    vlm_confirm_confidence: float = 0.80
    vlm_reject_confidence: float = 0.80

    candidate_screening: bool = True
    candidate_model_manifest: str = "models/semantic/manifest.json"
    candidate_start_threshold: float = 0.80
    candidate_continue_threshold: float = 0.48
    candidate_end_patience: int = 3
    candidate_adaptive_threshold: bool = False
    candidate_adaptive_saturation: float = 0.95
    candidate_adaptive_raised: float = 0.85
    candidate_merge_gap_seconds: float = 2.0
    candidate_min_duration_seconds: float = 0.5
    candidate_threshold_version: str = "candidate-thresholds-v1"

    vlm_timeout_seconds: float = 1800.0
    vlm_context_clip_timeout_seconds: float = 180.0
    video_input_max_seconds: float = Field(default=260.0, gt=0, le=260.0)
    video_input_width: int = Field(default=540, ge=240, le=1280)
    vlm_context_before_seconds: float = 8.0
    vlm_context_after_seconds: float = 8.0

    media_dir: Path = Path(__file__).resolve().parents[2] / "media"
    runs_dir: Path = Path(__file__).resolve().parents[2] / "runs"
    training_frame_width: int = Field(default=640, ge=320, le=1920)
    incident_pre_capture_seconds: float = Field(default=8.0, ge=0, le=120)
    incident_post_capture_seconds: float = Field(default=8.0, ge=0, le=120)
    incident_clip_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    max_feeds: int = 25
    live_feeds_path: Path = Path(__file__).resolve().parents[2] / "config" / "live_feeds.json"
    live_segment_seconds: int = 30
    live_max_backlog: int = 2
    live_keep_segments: int = Field(default=3, ge=1)
    live_keep_runs: int = Field(default=20, ge=1)
    candidate_cache_dir: Path = Path(__file__).resolve().parents[2] / "cache" / "candidate"
    candidate_manifest_path: Path = (
        Path(__file__).resolve().parents[2] / "models" / "candidate" / "manifest.json"
    )
    video_store_path: Path | None = None
    event_store_path: Path | None = None

    @property
    def runtime_profile(self) -> Literal["mock", "development", "competition-real"]:
        return "mock" if self.mock else self.deployment_profile

    @field_validator(
        "video_store_path",
        "event_store_path",
        mode="before",
    )
    @classmethod
    def blank_path_is_unset(cls, value: object) -> object:

        return None if value is None or value == "" else value

    @field_validator(
        "candidate_manifest_path",
        "dfine_active_manifest",
        "dfine_workspace_root",
        mode="after",
    )
    @classmethod
    def resolve_repository_path(cls, value: Path) -> Path:

        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("candidate_model_manifest", mode="after")
    @classmethod
    def resolve_candidate_model_manifest(cls, value: str) -> str:

        if not value or Path(value).is_absolute():
            return value
        return str((Path(__file__).resolve().parents[2] / value).resolve())

    @field_validator("dfine_onnx", mode="after")
    @classmethod
    def resolve_dfine_onnx(cls, value: str) -> str:

        if value and Path(value).expanduser().is_file():
            return value
        repo = Path(__file__).resolve().parents[2]
        for candidate in (Path.home() / ".cache" / "dortgoz" / "dfine" / "model.onnx",
                          repo / "models" / "dfine" / "model.onnx"):
            if candidate.is_file():
                return str(candidate)
        return value

    @field_validator("video_store_path", mode="after")
    @classmethod
    def resolve_video_store_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("event_store_path", mode="after")
    @classmethod
    def resolve_event_store_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    model_config = {
        "env_prefix": "DORTGOZ_",
        "env_file": str(Path(__file__).resolve().parents[2] / ".env"),
        "extra": "ignore",
    }


settings = Settings()
