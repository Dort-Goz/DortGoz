from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dortgoz.infrastructure.vlm_manifest import (
    VlmManifestError,
    load_vlm_manifest,
    readiness,
)
from dortgoz.pipeline.candidate_model import CandidateModelManifest
from dortgoz.pipeline.interpret import SYSTEM_TR
from dortgoz.pipeline.semantic import SemanticArtifact

BANNED = ["AGPL-3.0", "GPL-3.0", "CC-BY-NC-4.0", "proprietary", "research-only"]


def write_manifest(tmp_path: Path, *, weights: bool = True, **overrides: object) -> Path:
    blob = tmp_path / "model.gguf"
    if weights:
        blob.write_bytes(b"local-weights")
    payload = {
        "model_id": "fixture-local-vlm",
        "model_version": "1.0.0",
        "artifact_path": str(blob),
        "artifact_sha256": hashlib.sha256(b"local-weights").hexdigest(),
        "license": "MIT",
        "source": "fixture",
        "prompt_version": "candidate-vlm-v1",
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("banned", BANNED)
def test_non_permissive_vlm_license_is_rejected(tmp_path: Path, banned: str) -> None:
    with pytest.raises(VlmManifestError) as raised:
        load_vlm_manifest(write_manifest(tmp_path, license=banned))
    assert raised.value.code == "MODEL_MANIFEST_INVALID"


@pytest.mark.parametrize("banned", BANNED)
def test_non_permissive_screening_licenses_are_rejected(banned: str) -> None:
    with pytest.raises(ValueError):
        CandidateModelManifest(
            model_id="x", version="1.0.0", model_type="motion_baseline",
            artifact_path="a.json", artifact_sha256="0" * 64, license=banned,
        )
    with pytest.raises(ValueError):
        SemanticArtifact.model_validate(
            {"model_id": "x", "version": "1.0.0", "license": banned}
        )


def test_permissive_licenses_pass(tmp_path: Path) -> None:
    assert load_vlm_manifest(write_manifest(tmp_path, license="MIT")).license == "MIT"
    assert load_vlm_manifest(
        write_manifest(tmp_path, license="Apache-2.0")
    ).license == "Apache-2.0"


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(VlmManifestError) as raised:
        load_vlm_manifest(tmp_path / "yok.json")
    assert raised.value.code == "MODEL_MANIFEST_MISSING"


def test_remote_served_model_stays_ready(tmp_path: Path) -> None:
    report = readiness(write_manifest(tmp_path, weights=False))
    assert report["ready"] is True
    assert report["artifact"] == {
        "checked": False,
        "detail": "ağırlık bu makinede yok (uzak servis)",
    }


def test_local_weight_mismatch_reports_but_does_not_block(tmp_path: Path) -> None:
    path = write_manifest(tmp_path)
    (tmp_path / "model.gguf").write_bytes(b"tampered")
    report = readiness(path)
    assert report["ready"] is True
    assert report["artifact"]["checked"] is True
    assert report["artifact"]["matches"] is False


def test_local_weight_match_is_reported(tmp_path: Path) -> None:
    report = readiness(write_manifest(tmp_path))
    assert report["ready"] is True and report["license"] == "MIT"
    assert report["artifact"] == {"checked": True, "matches": True}


def test_unset_manifest_path_is_not_ready() -> None:
    assert readiness(None)["ready"] is False


def test_banned_license_blocks_readiness(tmp_path: Path) -> None:
    report = readiness(write_manifest(tmp_path, license="AGPL-3.0"))
    assert report["ready"] is False
    assert report["code"] == "MODEL_MANIFEST_INVALID"


def test_visual_prompt_injection_is_not_an_instruction_in_any_video_vlm_prompt() -> None:
    assert "güvenilmeyen görsel veri" not in SYSTEM_TR
