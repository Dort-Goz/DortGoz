from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..utils import file_sha256


class VlmManifestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VlmManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: Literal["Apache-2.0", "MIT"]
    source: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    max_tokens: int = Field(default=900, ge=128, le=4096)


def load_vlm_manifest(path: Path) -> VlmManifest:
    if not path.is_file():
        raise VlmManifestError("MODEL_MANIFEST_MISSING", "Yerel VLM manifest dosyası bulunamadı.")
    try:
        return VlmManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VlmManifestError(
            "MODEL_MANIFEST_INVALID",
            "Yerel VLM manifest geçersiz (izinsiz lisans veya bozuk alan).",
        ) from exc


def artifact_status(manifest: VlmManifest, manifest_path: Path) -> dict[str, object]:
    weights = Path(manifest.artifact_path).expanduser()
    if not weights.is_absolute():
        weights = (manifest_path.parent / weights).resolve()
    if not weights.is_file():
        return {"checked": False, "detail": "ağırlık bu makinede yok (uzak servis)"}
    if file_sha256(weights) != manifest.artifact_sha256:
        return {"checked": True, "matches": False, "detail": "ağırlık hash'i manifestle uyuşmuyor"}
    return {"checked": True, "matches": True}


def readiness(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "ready": False,
            "mode": "local_vlm",
            "detail": "DORTGOZ_VLM_MANIFEST_PATH ayarlanmadı",
            "endpoint_checked": False,
        }
    report: dict[str, object] = {
        "mode": "local_vlm",
        "manifest_path": str(path),
        "endpoint_checked": False,
    }
    try:
        manifest = load_vlm_manifest(path)
    except VlmManifestError as exc:
        return {**report, "ready": False, "detail": exc.message, "code": exc.code}
    return {
        **report,
        "ready": True,
        "model_id": manifest.model_id,
        "license": manifest.license,
        "artifact": artifact_status(manifest, path),
    }


__all__ = ["VlmManifest", "VlmManifestError", "artifact_status", "load_vlm_manifest", "readiness"]
