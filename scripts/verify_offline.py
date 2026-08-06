"""Dörtgöz offline release girdilerini ağ kullanmadan doğrular."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REQUIRED_REPOSITORY_FILES = (
    "docker-compose.yml",
    "backend/Dockerfile",
    "frontend/Dockerfile",
    ".dockerignore",
    ".env.example",
    "backend/uv.lock",
    "frontend/bun.lock",
    "models/MANIFEST.json",
    "THIRD_PARTY_NOTICES.md",
    "docs/OFFLINE_INSTALL.md",
)
FORBIDDEN_RUNTIME_URLS = ("api.openai.com", "openai.azure.com", "api.anthropic.com")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(root: Path, relative: str, errors: list[str]) -> None:
    if not (root / relative).is_file():
        errors.append(f"eksik dosya: {relative}")


def _verify_model_manifest(root: Path, errors: list[str]) -> None:
    manifest_path = root / "models" / "MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"model manifest okunamadı: {exc}")
        return

    if manifest.get("policy", {}).get("network_download_at_runtime") is not False:
        errors.append("model manifest runtime ağ indirmesini açık bırakıyor")
    allowed_licenses = {"Apache-2.0", "MIT"}
    for artifact in manifest.get("artifacts", []):
        component = artifact.get("component", "bilinmeyen")
        if artifact.get("license") not in allowed_licenses:
            errors.append(f"{component}: izin verilmeyen lisans")
            continue
        relative_path = artifact.get("path")
        expected_hash = artifact.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            errors.append(f"{component}: path veya sha256 eksik")
            continue
        artifact_path = (root / relative_path).resolve()
        if not artifact_path.is_relative_to(root.resolve()) or not artifact_path.is_file():
            errors.append(f"{component}: artifact bulunamadı")
        elif _sha256(artifact_path) != expected_hash:
            errors.append(f"{component}: SHA-256 eşleşmiyor")


def _verify_runtime_config(root: Path, errors: list[str]) -> None:
    for relative in ("docker-compose.yml", ".env.example", "backend/Dockerfile", "frontend/Dockerfile"):
        path = root / relative
        if not path.is_file():
            continue
        lowered = path.read_text(encoding="utf-8").casefold()
        for forbidden in FORBIDDEN_RUNTIME_URLS:
            if forbidden in lowered:
                errors.append(f"yasak cloud runtime ucu bulundu: {relative} → {forbidden}")


def _verify_bundle(bundle: Path, errors: list[str]) -> None:
    for relative in (
        "images/dortgoz-images.tar",
        "source.tar",
        "SHA256SUMS",
        "docker-compose.yml",
        "models/MANIFEST.json",
        "docs/OFFLINE_INSTALL.md",
    ):
        _require_file(bundle, relative, errors)


def verify(root: Path, bundle: Path | None = None) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_REPOSITORY_FILES:
        _require_file(root, relative, errors)
    _verify_model_manifest(root, errors)
    _verify_runtime_config(root, errors)
    if bundle is not None:
        _verify_bundle(bundle, errors)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--bundle", type=Path, help="oluşturulmuş offline bundle klasörü")
    args = parser.parse_args()
    errors = verify(args.root.resolve(), args.bundle.resolve() if args.bundle else None)
    if errors:
        print("OFFLINE VERIFY FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("OFFLINE VERIFY OK")


if __name__ == "__main__":
    main()
