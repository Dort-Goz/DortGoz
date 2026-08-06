"""UUID tabanlı yerel video saklama adaptörü."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock
from uuid import uuid4

from ..domain.video import VideoErrorCode, VideoIngestError


@dataclass(frozen=True)
class StoredVideo:
    video_id: str
    original_filename: str
    stored_filename: str
    absolute_path: Path
    media_path: str
    file_size_bytes: int
    file_hash_sha256: str
    duplicate_of_video_id: str | None = None


class LocalVideoStorage:
    """Kaynak videoyu değiştirmeden güvenli adla yerel media köküne kopyalar."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        allowed_extensions: frozenset[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.allowed_extensions = allowed_extensions or frozenset({".mp4", ".mkv", ".avi", ".mov"})
        self._hash_index: dict[str, str] = {}
        self._hash_lock = Lock()

    async def store(self, source: Path, original_filename: str | None = None) -> StoredVideo:
        return await asyncio.to_thread(self._store_sync, source, original_filename)

    def _store_sync(self, source: Path, original_filename: str | None) -> StoredVideo:
        if not source.is_file():
            raise VideoIngestError(VideoErrorCode.FILE_NOT_FOUND, "video dosyası bulunamadı")
        if source.is_symlink():
            raise VideoIngestError(VideoErrorCode.PATH_REJECTED, "symlink video kaynağı reddedildi")
        name = original_filename or source.name
        if PurePosixPath(name).name != name or PureWindowsPath(name).name != name:
            raise VideoIngestError(VideoErrorCode.PATH_REJECTED, "orijinal dosya adı path içeremez")
        if name in {"", ".", ".."}:
            raise VideoIngestError(VideoErrorCode.PATH_REJECTED, "geçersiz orijinal dosya adı")
        extension = Path(name).suffix.lower()
        if extension not in self.allowed_extensions:
            raise VideoIngestError(
                VideoErrorCode.UNSUPPORTED_CONTAINER,
                f"desteklenmeyen video uzantısı: {extension or '(yok)'}",
            )
        size = source.stat().st_size
        if size <= 0:
            raise VideoIngestError(VideoErrorCode.DECODE_FAILED, "video dosyası boş")
        if size > self.max_bytes:
            raise VideoIngestError(VideoErrorCode.FILE_TOO_LARGE, "video boyut sınırını aşıyor")

        self.root.mkdir(parents=True, exist_ok=True)
        video_id = str(uuid4())
        stored_filename = f"{video_id}{extension}"
        target = (self.root / stored_filename).resolve()
        if not target.is_relative_to(self.root):
            raise VideoIngestError(VideoErrorCode.PATH_REJECTED, "storage kökü dışına çıkış reddedildi")

        digest = hashlib.sha256()
        copied = 0
        try:
            with source.open("rb") as src, target.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > self.max_bytes:
                        raise VideoIngestError(
                            VideoErrorCode.FILE_TOO_LARGE, "video boyut sınırını aşıyor"
                        )
                    digest.update(chunk)
                    dst.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        digest_hex = digest.hexdigest()
        with self._hash_lock:
            duplicate_of = self._hash_index.get(digest_hex)
            self._hash_index.setdefault(digest_hex, video_id)

        return StoredVideo(
            video_id=video_id,
            original_filename=name,
            stored_filename=stored_filename,
            absolute_path=target,
            media_path=stored_filename,
            file_size_bytes=copied,
            file_hash_sha256=digest_hex,
            duplicate_of_video_id=duplicate_of,
        )

    async def remove(self, stored: StoredVideo) -> None:
        await asyncio.to_thread(self._remove_sync, stored)

    def _remove_sync(self, stored: StoredVideo) -> None:
        resolved = stored.absolute_path.resolve()
        if not resolved.is_relative_to(self.root):
            raise VideoIngestError(VideoErrorCode.PATH_REJECTED, "storage kökü dışı silme reddedildi")
        resolved.unlink(missing_ok=True)
        with self._hash_lock:
            if self._hash_index.get(stored.file_hash_sha256) == stored.video_id:
                del self._hash_index[stored.file_hash_sha256]
