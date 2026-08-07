"""Yerel VLM'yi mevcut bounded agent sözleşmesine bağlayan hibrit adapter."""

from __future__ import annotations

from ..agent.state import EventAgentState
from ..config import Settings
from ..domain.context import ContextClip, KeyframeRef
from ..domain.video import VideoMetadata
from ..infrastructure.model_client import OpenAICompatibleLocalClient
from ..services.evidence_validator import validate_evidence
from .context_clip import LocalContextClipTool
from .keyframes import LocalKeyframeTool
from .local_vlm import LocalVlmManifest, LocalVlmTool
from .mock_agent import MockAgentTools


def _replace(state: EventAgentState, **updates: object) -> EventAgentState:
    data = state.model_dump()
    data.update(updates)
    return EventAgentState.model_validate(data)


class LocalVlmAgentTools:
    """VLM/context/keyframe gerçek; dense CV Görev 09'a kadar açıkça mock fallback.

    Bu sınıf candidate dışındaki hiçbir video zamanına erişmez. Teknik CV ve
    dense-analysis araçları henüz gerçek adapter olmadığı için bunlar mock
    sonuç üretir; bu sınır API profilinde/dokümantasyonda korunur.
    """

    def __init__(
        self,
        *,
        metadata: VideoMetadata,
        settings: Settings,
        manifest: LocalVlmManifest,
        fallback_tools: MockAgentTools | None = None,
    ) -> None:
        workspace_root = settings.media_dir.parent
        self.metadata = metadata
        self.before_seconds = settings.vlm_context_before_seconds
        self.after_seconds = settings.vlm_context_after_seconds
        self.keyframes = LocalKeyframeTool(
            media_root=settings.media_dir,
            workspace_root=workspace_root,
        )
        self.context = LocalContextClipTool(
            media_root=settings.media_dir,
            workspace_root=workspace_root,
            timeout_seconds=settings.vlm_context_clip_timeout_seconds,
        )
        self.vlm = LocalVlmTool(
            client=OpenAICompatibleLocalClient(
                base_url=settings.llama_base_url,
                api_key=settings.api_key,
            ),
            manifest=manifest,
            workspace_root=workspace_root,
            timeout_seconds=settings.vlm_timeout_seconds,
        )
        self.fallback = fallback_tools or MockAgentTools()

    async def run_cv_only(self, state: EventAgentState) -> EventAgentState:
        return await self.fallback.run_cv_only(state)

    async def run_dense_analysis(self, state: EventAgentState) -> EventAgentState:
        return await self.fallback.run_dense_analysis(state)

    async def expand_context(self, state: EventAgentState) -> EventAgentState:
        context, frames = await self._materialize(state, expanded=True)
        return _replace(
            state,
            context_clip=context,
            keyframes=frames,
            image_quality=state.image_quality,
        )

    async def run_vlm(
        self, state: EventAgentState, *, strict_schema: bool
    ) -> EventAgentState:
        context, frames = await self._materialize(
            state,
            expanded=state.context_expanded,
        )
        result = await self.vlm.verify(
            state.candidate,
            context,
            frames,
            attempt=state.vlm_attempts + 1,
            strict_schema=strict_schema,
        )
        return _replace(state, context_clip=context, keyframes=frames, vlm_result=result)

    async def validate_evidence(self, state: EventAgentState) -> EventAgentState:
        return _replace(
            state,
            validation=validate_evidence(state, workspace_root=self.vlm.workspace_root),
        )

    async def _materialize(
        self, state: EventAgentState, *, expanded: bool
    ) -> tuple[ContextClip, list[KeyframeRef]]:
        before = self.before_seconds if expanded else 0.0
        after = self.after_seconds if expanded else 0.0
        context = await self.context.create(
            self.metadata,
            state.candidate,
            analysis_id=state.analysis_id,
            before_seconds=before,
            after_seconds=after,
            expanded=expanded,
        )
        frames = await self.keyframes.capture(
            self.metadata,
            state.candidate,
            analysis_id=state.analysis_id,
            clip_start=context.clip_start,
            clip_end=context.clip_end,
        )
        return context, frames


__all__ = ["LocalVlmAgentTools"]
