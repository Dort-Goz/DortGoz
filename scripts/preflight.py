#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
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
    "scripts/dev.ps1",
    "scripts/dev.sh",
)



CLOUD_TELEMETRY_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
DISABLED_VALUES = {"", "0", "false", "no", "off"}
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
        if relative.endswith(TRACKED_MODEL_SUFFIXES):
            errors.append(f"model ağırlığı Git tarafından izleniyor: {relative}")


def _verify_cloud_telemetry(root: Path, errors: list[str]) -> None:

    sources: list[tuple[str, dict[str, str]]] = [("ortam", dict(os.environ))]
    env_path = root / ".env"
    if env_path.is_file():
        sources.append((".env", _read_env(env_path)))
    for source, values in sources:
        for name in CLOUD_TELEMETRY_VARS:
            value = values.get(name)
            if value is None:
                continue
            if value.strip().casefold() not in DISABLED_VALUES:
                errors.append(f"{source}: {name}={value} bulut izlemesini açıyor; false olmalı")


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
    values = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("DORTGOZ_")
    }
    values.update(_read_env(env_path))
    if values.get("DORTGOZ_MOCK", "").casefold() in {"1", "true", "yes", "on"}:
        errors.append("gerçek modda DORTGOZ_MOCK=0 olmalı")
    if values.get("DORTGOZ_DEPLOYMENT_PROFILE") != "competition-real":
        errors.append("gerçek modda DORTGOZ_DEPLOYMENT_PROFILE=competition-real olmalı")
    if not values.get("DORTGOZ_EVENT_STORE_PATH"):
        errors.append("competition-real profilinde DORTGOZ_EVENT_STORE_PATH zorunlu")
    base_url = values.get("DORTGOZ_LLAMA_BASE_URL", "")
    if not base_url.startswith("https://") or "inference.example.invalid" not in base_url:
        errors.append("DORTGOZ_LLAMA_BASE_URL EVREN HTTPS çıkarım ucunu göstermeli")
    if not values.get("DORTGOZ_API_KEY"):
        errors.append("DORTGOZ_API_KEY zorunlu")
    expected = {
        "DORTGOZ_MAIN_MODEL": "llm-fast",
        "DORTGOZ_VIDEO_MODEL": "vlm",
        "DORTGOZ_SECOND_OPINION_MODEL": "llm-large",
        "DORTGOZ_AGENT_MODEL": "llm-fast",
        "DORTGOZ_ROUTER_MODEL": "router",
        "DORTGOZ_GUARD_MODEL": "guard",
        "DORTGOZ_EMBEDDING_MODEL": "bge-m3-embed",
    }
    for key, expected_value in expected.items():
        if values.get(key, expected_value) != expected_value:
            errors.append(f"{key}={expected_value} olmalı")
    if not values.get("DORTGOZ_QDRANT_URL", "").startswith("https://"):
        errors.append("DORTGOZ_QDRANT_URL EVREN HTTPS vektör ucunu göstermeli")
    if not values.get("DORTGOZ_QDRANT_PREFIX"):
        errors.append("DORTGOZ_QDRANT_PREFIX zorunlu")
    if not values.get("DORTGOZ_QDRANT_API_KEY"):
        errors.append("DORTGOZ_QDRANT_API_KEY zorunlu")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--check-tools", action="store_true", help="PATH ve Python sürümünü de denetle")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = args.root.resolve()
    errors: list[str] = []
    _verify_repository(root, errors)
    _verify_cloud_telemetry(root, errors)
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
