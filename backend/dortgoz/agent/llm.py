from __future__ import annotations

import asyncio
import random

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from ..config import settings

_clients: dict[int, AsyncOpenAI] = {}


def main_client() -> AsyncOpenAI:
    try:
        key = id(asyncio.get_running_loop())
    except RuntimeError:
        key = 0
    if key not in _clients:
        _clients[key] = AsyncOpenAI(base_url=settings.llama_base_url,
                                    api_key=settings.api_key,
                                    timeout=settings.vlm_timeout_seconds,
                                    max_retries=0)
    return _clients[key]


_sems: dict[int, asyncio.Semaphore] = {}


def _inflight() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _sems:
        _sems[key] = asyncio.Semaphore(max(1, settings.max_inflight))
    return _sems[key]


async def create_chat(client: AsyncOpenAI, **kwargs):
    delay = 2.0
    for attempt in range(settings.llm_retries):
        try:
            async with _inflight():
                return await client.chat.completions.create(**kwargs)
        except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError):
            if attempt == settings.llm_retries - 1:
                raise
            await asyncio.sleep(delay + random.uniform(0, 1))
            delay = min(delay * 2, 20.0)
    raise RuntimeError("erişilemez")

async def create_embedding(client: AsyncOpenAI, **kwargs):
    delay = 2.0
    for attempt in range(settings.llm_retries):
        try:
            async with _inflight():
                return await client.embeddings.create(**kwargs)
        except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError):
            if attempt == settings.llm_retries - 1:
                raise
            await asyncio.sleep(delay + random.uniform(0, 1))
            delay = min(delay * 2, 20.0)
    raise RuntimeError("erişilemez")



_CTX_CACHE: dict[str, int | None] = {}


def call_stats(resp) -> dict:
    t = (getattr(resp, "model_extra", None) or {}).get("timings") or {}
    u = getattr(resp, "usage", None)
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", None),
        "completion_tokens": getattr(u, "completion_tokens", None),
        "pp_tps": t.get("prompt_per_second"),
        "gen_tps": t.get("predicted_per_second"),
    }


async def context_size(model: str) -> int | None:
    if model not in _CTX_CACHE:
        _CTX_CACHE[model] = {
            "llm-fast": 262_144,
            "llm-large": 262_144,
            "vlm": 262_144,
            "router": 40_960,
            "guard": 32_768,
        }.get(model)
    return _CTX_CACHE[model]
