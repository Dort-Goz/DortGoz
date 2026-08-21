from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..domain.video import VideoMetadata
from ..errors import RepositoryDuplicateError, RepositoryError


class VideoRegistry:
    def __init__(self, store_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._videos: dict[str, VideoMetadata] = {}
        self.store_path = store_path.resolve() if store_path is not None else None
        if self.store_path is not None:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self._load()

    @property
    def persistence_mode(self) -> str:
        return "json" if self.store_path is not None else "memory"

    def _load(self) -> None:
        assert self.store_path is not None
        if not self.store_path.is_file():
            return
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            videos = [VideoMetadata.model_validate(item) for item in payload["videos"]]
        except (OSError, ValueError, KeyError) as exc:
            raise RepositoryError(f"video kaydı okunamadı: {exc}") from exc
        self._videos = {video.video_id: video for video in videos}

    def _persist(self) -> None:
        if self.store_path is None:
            return
        payload = json.dumps(
            {"videos": [v.model_dump(mode="json") for v in self._videos.values()]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        tmp = self.store_path.with_suffix(self.store_path.suffix + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.store_path)
        except OSError as exc:
            raise RepositoryError(f"video kaydı yazılamadı: {exc}") from exc

    def create_video(self, metadata: VideoMetadata) -> VideoMetadata:
        with self._lock:
            existing = self._videos.get(metadata.video_id)
            if existing is not None:
                if existing.file_hash_sha256 != metadata.file_hash_sha256:
                    raise RepositoryDuplicateError(
                        f"video_id zaten farklı hash ile kayıtlı: {metadata.video_id}"
                    )
                return existing.model_copy(deep=True)
            self._videos[metadata.video_id] = metadata.model_copy(deep=True)
            self._persist()
            return metadata.model_copy(deep=True)

    def get_video(self, video_id: str) -> VideoMetadata | None:
        with self._lock:
            item = self._videos.get(video_id)
            return item.model_copy(deep=True) if item is not None else None

    def find_video_by_hash(self, file_hash_sha256: str) -> VideoMetadata | None:
        with self._lock:
            item = next(
                (v for v in self._videos.values() if v.file_hash_sha256 == file_hash_sha256),
                None,
            )
            return item.model_copy(deep=True) if item is not None else None

    def clear(self) -> None:
        with self._lock:
            self._videos.clear()
            self._persist()


__all__ = ["VideoRegistry"]
