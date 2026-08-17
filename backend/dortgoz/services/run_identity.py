"""Koşu kimliklerini güvenli, platformlar arası dosya adlarına sınırla."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

_ALLOWED_PUNCTUATION = {"-", "_", "."}
_MAX_RUN_ID_LENGTH = 96
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def require_safe_run_id(value: str) -> str:
    """Traversal ve Windows aygıt adlarını içermeyen tek dosya gövdesi döndür."""

    if not value or len(value) > _MAX_RUN_ID_LENGTH:
        raise ValueError("koşu kimliği boş veya çok uzun")
    if value in {".", ".."} or not value[0].isalnum() or value.endswith("."):
        raise ValueError("koşu kimliği güvenli bir harf veya rakamla başlamalı")
    if any(not (character.isalnum() or character in _ALLOWED_PUNCTUATION) for character in value):
        raise ValueError("koşu kimliği güvenli olmayan karakter içeriyor")

    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.name != value
        or windows.name != value
        or windows.drive
        or windows.root
        or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise ValueError("koşu kimliği tek ve güvenli bir dosya adı olmalı")
    return value


def safe_run_file(runs_dir: Path, run_id: str, suffix: str) -> Path:
    """Koşu dosyasını çöz ve symlink dahil runs kökü dışına çıkışı reddet."""

    safe_id = require_safe_run_id(run_id)
    root = runs_dir.resolve()
    target = (root / f"{safe_id}{suffix}").resolve()
    if not target.is_relative_to(root):
        raise ValueError("koşu dosyası runs kökü dışına çıkıyor")
    return target


__all__ = ["require_safe_run_id", "safe_run_file"]
