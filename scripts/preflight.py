#!/usr/bin/env python3
"""Yeni bir Dörtgöz klonunun mock veya gerçek yerel çalışmaya hazır olduğunu denetler."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_FILES = (
    ".env.example",
    ".gitignore",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "frontend/package.json",
    "frontend/bun.lock",
    "media/.gitkeep",
    "models/vlm/manifest.template.json",
    "scripts/dev.ps1",
    "scripts/dev.sh",
)
FORBIDDEN_CLOUD_HOSTS = ("api.openai.com", "openai.azure.com", "api.anthropic.com")
TRACKED_RUNTIME_PREFIXES = ("runs/", "cache/", "models/candidate/local/")
TRACKED_MODEL_SUFFIXES = (".gguf", ".safetensors", ".bin")
PORTABILITY_MARKERS = ("C:\\Users\\kullanici", "~/datasets/Dort_Goz")
PORTABILITY_FILES = (
    ".env.example",
    "scripts/make_long_feed.py",
)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_repository(root: Path, errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"eksik repository dosyası: {relative}")

    template = root / ".env.example"
    if template.is_file() and "DORTGOZ_MOCK=1" not in template.read_text(encoding="utf-8"):
        errors.append(".env.example yeni klon için DORTGOZ_MOCK=1 varsayılanını içermiyor")
    for relative in PORTABILITY_FILES:
        path = root / relative
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if any(marker in content for marker in PORTABILITY_MARKERS):
            errors.append(f"makineye bağlı yol bulundu: {relative}")

    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        return
    result = subprocess.run(
        [git, "-C", str(root), "ls-files"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        errors.append("Git ile izlenen dosya listesi okunamadı")
        return
    tracked = {line.replace("\\", "/") for line in result.stdout.splitlines()}
    if ".env" in tracked:
        errors.append("doldurulmuş .env Git tarafından izleniyor")
    for relative in tracked:
        if relative.startswith(TRACKED_RUNTIME_PREFIXES):
            errors.append(f"runtime/model çıktısı Git tarafından izleniyor: {relative}")
        if relative.startswith("media/") and relative != "media/.gitkeep":
            errors.append(f"video/medya Git tarafından izleniyor: {relative}")
        if relative == "models/vlm/manifest.local.json" or relative.endswith(TRACKED_MODEL_SUFFIXES):
            errors.append(f"yerel VLM manifesti/ağırlığı Git tarafından izleniyor: {relative}")


def _verify_tools(mode: str, errors: list[str]) -> None:
    required = ["uv", "bun"]
    if mode == "real":
        required.extend(("ffmpeg", "ffprobe"))
    for command in required:
        if shutil.which(command) is None:
            errors.append(f"gerekli komut PATH üzerinde değil: {command}")


def _verify_real_config(root: Path, errors: list[str]) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        errors.append("gerçek mod için .env yok; .env.example dosyasını kopyalayıp doldurun")
        return
    values = _read_env(env_path)
    if values.get("DORTGOZ_MOCK", "").casefold() in {"1", "true", "yes", "on"}:
        errors.append("gerçek modda DORTGOZ_MOCK=0 olmalı")
    base_url = values.get("DORTGOZ_LLAMA_BASE_URL", "")
    if not base_url or "<" in base_url or any(host in base_url.casefold() for host in FORBIDDEN_CLOUD_HOSTS):
        errors.append("DORTGOZ_LLAMA_BASE_URL yerel/özel ağ OpenAI-uyumlu endpoint'i olmalı")

    manifest_value = values.get("DORTGOZ_VLM_MANIFEST_PATH", "")
    if not manifest_value:
        errors.append("gerçek candidate-only VLM için DORTGOZ_VLM_MANIFEST_PATH zorunlu")
        return
    manifest_path = Path(manifest_value).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = (root / manifest_path).resolve()
    if not manifest_path.is_file():
        errors.append("DORTGOZ_VLM_MANIFEST_PATH mevcut bir manifest'e işaret etmiyor")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        errors.append("VLM manifest geçerli JSON değil")
        return
    if manifest.get("license") not in {"Apache-2.0", "MIT"}:
        errors.append("VLM manifest lisansı Apache-2.0 veya MIT olmalı")
    artifact_path = Path(str(manifest.get("artifact_path", ""))).expanduser()
    if not artifact_path.is_file():
        errors.append("VLM manifest artifact_path mevcut bir yerel dosya değil")
        return
    expected_hash = manifest.get("artifact_sha256")
    if not isinstance(expected_hash, str) or _sha256(artifact_path) != expected_hash:
        errors.append("VLM artifact SHA-256 manifest ile eşleşmiyor")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--check-tools", action="store_true", help="PATH ve Python sürümünü de denetle")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = args.root.resolve()
    errors: list[str] = []
    _verify_repository(root, errors)
    if args.check_tools:
        _verify_tools(args.mode, errors)
    if args.mode == "real":
        _verify_real_config(root, errors)
    if errors:
        print("PREFLIGHT FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"PREFLIGHT OK ({args.mode})")


if __name__ == "__main__":
    main()
