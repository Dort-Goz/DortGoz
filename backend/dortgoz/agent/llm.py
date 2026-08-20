from __future__ import annotations

import asyncio
import random

from openai import AsyncOpenAI, RateLimitError

from ..config import settings

_clients: dict[int, AsyncOpenAI] = {}


def main_client() -> AsyncOpenAI:
    try:
        key = id(asyncio.get_running_loop())
    except RuntimeError:
        key = 0
    if key not in _clients:
        _clients[key] = AsyncOpenAI(base_url=settings.llama_base_url,
                                    api_key=settings.api_key)
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
        except RateLimitError:
            if attempt == settings.llm_retries - 1:
                raise
            await asyncio.sleep(delay + random.uniform(0, 1))
            delay = min(delay * 2, 20.0)
    raise RuntimeError("erişilemez")


def triage_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.vllm_base_url, api_key=settings.api_key)


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
    if model in _CTX_CACHE:
        return _CTX_CACHE[model]
    import asyncio
    import json
    import urllib.request

    base = settings.llama_base_url.rsplit("/v1", 1)[0]

    def fetch() -> int | None:
        try:
            with urllib.request.urlopen(f"{base}/upstream/{model}/props", timeout=5) as f:
                props = json.load(f)
            n = props.get("default_generation_settings", {}).get("n_ctx")
            return int(n) if n else None
        except Exception:
            return None

    ctx = await asyncio.to_thread(fetch)
    _CTX_CACHE[model] = ctx
    return ctx
