from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llama_base_url: str = "http://127.0.0.1:8080/v1"
    vllm_base_url: str = "http://127.0.0.1:8001/v1"
    api_key: str = "local"

    main_model: str = "qwen3.6-35b-a3b-vision"
    triage_model: str = "minicpm-v-4.6"

    mock: bool = False
    mock_speed: float = 1.0

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
    incident_grace_windows: int = 1
    escalate_p: float = 0.10
    escalate_target_p: float = 0.0
    escalate_shadow: bool = True
    shadow_sample_rate: float = 0.0
    exemplar_suppress: bool = False
    exemplar_shadow: bool = True
    exemplar_threshold: float = 0.97
    second_opinion_model: str = "qwen3.8-27b-vision-dg"
    second_opinion_motion: float = 0.30
    interpret_effort: str = ""
    second_opinion_effort: str = ""
    agent_model: str = ""
    agent_effort: str = ""
    agent_think_budget: int = 1200
    interpret_think_budget: int = 2500
    interpret_think_temp: float = 0.0
    dual_read: bool = False
    final_sweep: bool = False
    max_inflight: int = 8
    llm_retries: int = 6

    onnx_device: str = "cpu"
    onnx_providers: str = ""
    detector_enabled: bool = True
    dfine_onnx: str = "~/.cache/dortgoz/dfine/model.onnx"
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
    candidate_model_manifest: str = ""
    candidate_start_threshold: float = 0.65
    candidate_continue_threshold: float = 0.40
    candidate_end_patience: int = 3
    candidate_adaptive_threshold: bool = False
    candidate_adaptive_saturation: float = 0.95
    candidate_adaptive_raised: float = 0.85
    candidate_merge_gap_seconds: float = 2.0
    candidate_min_duration_seconds: float = 0.5
    candidate_threshold_version: str = "candidate-thresholds-v1"

    vlm_manifest_path: Path | None = None
    vlm_timeout_seconds: float = 90.0
    vlm_context_clip_timeout_seconds: float = 90.0
    vlm_context_before_seconds: float = 8.0
    vlm_context_after_seconds: float = 8.0

    media_dir: Path = Path(__file__).resolve().parents[2] / "media"
    runs_dir: Path = Path(__file__).resolve().parents[2] / "runs"
    gguf_paths: str = ""
    max_feeds: int = 25
    live_feeds_path: Path = Path(__file__).resolve().parents[2] / "config" / "live_feeds.json"
    live_segment_seconds: int = 30
    live_max_backlog: int = 2
    live_keep_segments: int = 3
    live_keep_runs: int = 20
    candidate_cache_dir: Path = Path(__file__).resolve().parents[2] / "cache" / "candidate"
    candidate_manifest_path: Path = (
        Path(__file__).resolve().parents[2] / "models" / "candidate" / "manifest.json"
    )
    video_store_path: Path | None = None

    @field_validator("vlm_manifest_path", "video_store_path", mode="before")
    @classmethod
    def blank_path_is_unset(cls, value: object) -> object:

        return None if value is None or value == "" else value

    @field_validator("candidate_manifest_path", mode="after")
    @classmethod
    def resolve_candidate_manifest_path(cls, value: Path) -> Path:

        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("candidate_model_manifest", mode="after")
    @classmethod
    def resolve_candidate_model_manifest(cls, value: str) -> str:

        if not value or Path(value).is_absolute():
            return value
        return str((Path(__file__).resolve().parents[2] / value).resolve())

    @field_validator("vlm_manifest_path", mode="after")
    @classmethod
    def resolve_vlm_manifest_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if value.is_absolute():
            return value.resolve()
        return (Path(__file__).resolve().parents[2] / value).resolve()

    @field_validator("video_store_path", mode="after")
    @classmethod
    def resolve_video_store_path(cls, value: Path | None) -> Path | None:
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
