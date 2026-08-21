from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..domain.candidate import ScreeningSample


class FeatureCacheKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    video_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(min_length=1)
    feature_version: str = Field(min_length=1)

    @property
    def digest(self) -> str:
        raw = f"{self.video_hash_sha256}:{self.model_id}:{self.feature_version}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FeatureCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: FeatureCacheKey
    samples: list[ScreeningSample]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JsonFeatureCache:

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: FeatureCacheKey) -> Path:
        target = (self.root / f"{key.digest}.json").resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("feature cache yolu kök dışına çıkamaz")
        return target

    def save(self, key: FeatureCacheKey, samples: list[ScreeningSample]) -> FeatureCacheEntry:
        entry = FeatureCacheEntry(key=key, samples=samples)
        target = self.path_for(key)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        return entry

    def load(self, key: FeatureCacheKey) -> FeatureCacheEntry | None:
        target = self.path_for(key)
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"feature cache okunamadı: {target.name}") from exc
        entry = FeatureCacheEntry.model_validate(payload)
        if entry.key != key:
            raise ValueError("feature cache key dosya içeriğiyle eşleşmiyor")
        return entry


__all__ = ["FeatureCacheEntry", "FeatureCacheKey", "JsonFeatureCache"]
