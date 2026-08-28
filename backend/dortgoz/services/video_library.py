from __future__ import annotations

from pathlib import Path
from threading import Lock

from ..config import settings

VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".avi", ".mov"})

_dataset: dict[str, Path] | None = None
_dataset_root: Path | None = None
_lock = Lock()


def _scan(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file():
            found.setdefault(path.name, path.resolve())
    return found


def dataset_index() -> dict[str, Path]:
    """Veri kümesindeki videolar: dosya adı → tam yol.

    UCF-Crime dosya adları küme genelinde tekildir; sınıf klasörü adın kendi
    içindedir (Burglary054_x264.mp4). Bu yüzden arayüz sözleşmesi düz dosya
    adı olarak kalır ve yol çözümlemesi tek yerde toplanır.
    """
    # ponytail: küme salt okunur, tarama süreç başına bir kez yapılır.
    # Kümeye video eklenirse backend'i yeniden başlatın.
    global _dataset, _dataset_root
    root = settings.ucf_dir
    with _lock:
        if root is None or not root.is_dir():
            _dataset, _dataset_root = {}, None
            return {}
        if _dataset is None or _dataset_root != root:
            _dataset, _dataset_root = _scan(root), root
        return _dataset


def dataset_path(name: str) -> Path | None:
    return dataset_index().get(Path(name).name)


def catalog() -> list[str]:
    """Kaynak listesi: yerel medya klasörü + veri kümesi."""
    local: set[str] = set()
    if settings.media_dir.exists():
        local = {
            path.name
            for path in settings.media_dir.iterdir()
            if path.suffix.lower() in VIDEO_SUFFIXES
        }
    return sorted(local | set(dataset_index()))


def reset_cache() -> None:
    global _dataset, _dataset_root
    with _lock:
        _dataset, _dataset_root = None, None
