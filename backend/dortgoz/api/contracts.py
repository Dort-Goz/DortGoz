"""REST request/response sözleşmeleri.

Bu modeller, UI ile repository arasındaki sınırı açık tutar. Domain modelleri
doğrudan HTTP detaylarıyla kirletilmez; response modelleri domain nesnelerini
JSON'a güvenli biçimde taşır.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..services.analysis_job import AnalysisJobStatus


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(default="mock", min_length=1, deprecated=True)
    config_version: str = Field(default="task-06-v1", min_length=1, deprecated=True)
    feed: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=20_000)
    task_prompt: str = Field(default="", max_length=20_000)
    # Çalışma kipi: "" | dengeli | temkinli | genis (bkz. runner._mode_flags)
    mode: Literal["", "dengeli", "temkinli", "genis"] = ""


class AnalysisAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    video_id: str = Field(min_length=1)
    status: AnalysisJobStatus = AnalysisJobStatus.QUEUED
    status_url: str = Field(min_length=1)
    result_url: str = Field(min_length=1)


class AnalysisProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str = Field(min_length=1)
    status: AnalysisJobStatus


__all__ = [
    "AnalysisAccepted",
    "AnalysisProgress",
    "AnalyzeRequest",
]
