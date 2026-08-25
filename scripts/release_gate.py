from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MAX_TRACKED_BYTES = 5 * 1024 * 1024
BINARY_SUFFIXES = {
    ".mp4", ".avi", ".mkv", ".mov", ".webm",
    ".onnx", ".gguf", ".safetensors", ".pt", ".pth", ".bin", ".weights",
}
TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml", ".toml", ".sh",
    ".ps1", ".txt", ".cfg", ".ini", ".env", ".example",
}
SECRET_PATTERNS = [
    ("özel anahtar", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP|DSA)? ?PRIVATE KEY")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{24,}")),
    ("OpenAI/Anthropic anahtarı", re.compile(r"\b(sk|pk)-[A-Za-z0-9]{24,}")),
    ("AWS erişim anahtarı", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("tailnet adresi", re.compile(r"[A-Za-z0-9-]+\.ts\.net")),
    ("Tailscale auth key", re.compile(r"\btskey-[A-Za-z0-9-]{10,}")),
]
ALLOWED_SECRET_PATHS = {"scripts/release_gate.py"}


SELFTEST_SAMPLES = {
    "özel anahtar": "-----BEGIN OPENSSH PRIVATE KEY-----",
    "bearer token": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
    "OpenAI/Anthropic anahtarı": "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2",
    "AWS erişim anahtarı": "AKIA" + "ABCDEFGHIJKLMNOP",
    "tailnet adresi": "ornek-tailnet.ts.net",
    "Tailscale auth key": "tskey-auth-abcdef0123456789",
}


def selftest() -> int:
    failures = []
    for label, pattern in SECRET_PATTERNS:
        sample = SELFTEST_SAMPLES.get(label)
        if sample is None:
            failures.append(f"{label}: örnek yok")
        elif not pattern.search(sample):
            failures.append(f"{label}: örneği yakalamadı")
    if any(pattern.search("normal bir satır, sır yok") for _, pattern in SECRET_PATTERNS):
        failures.append("temiz metinde yanlış pozitif")
    for failure in failures:
        print(f"SELFTEST: {failure}")
    if failures:
        return 1
    print(f"SELFTEST TAMAM: {len(SECRET_PATTERNS)} desen doğrulandı")
    return 0


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [name for name in result.stdout.split("\0") if name]


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    findings: list[str] = []
    names = tracked_files()

    for name in names:
        path = ROOT / name
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in BINARY_SUFFIXES:
            findings.append(f"izlenen ikili varlık: {name}")
            continue
        size = path.stat().st_size
        if size > MAX_TRACKED_BYTES:
            findings.append(f"izlenen dosya çok büyük: {name} ({size / 1_048_576:.1f} MB)")
        if suffix not in TEXT_SUFFIXES or name in ALLOWED_SECRET_PATHS:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                line = content[: match.start()].count("\n") + 1
                findings.append(f"{label} sızıntısı: {name}:{line}")

    for finding in findings:
        print(f"RELEASE GATE: {finding}")
    if findings:
        print(f"RELEASE GATE BAŞARISIZ: {len(findings)} bulgu")
        return 1
    print(f"RELEASE GATE TAMAM: {len(names)} izlenen dosya, bulgu yok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
