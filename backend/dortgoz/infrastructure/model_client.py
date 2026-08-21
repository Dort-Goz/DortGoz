from __future__ import annotations

import asyncio
from typing import Any, Protocol

from openai import AsyncOpenAI


class LocalModelClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LocalModelClient(Protocol):
    async def complete_json(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout_seconds: float,
        strict_schema: bool,
    ) -> str: ...


class OpenAICompatibleLocalClient:

    def __init__(self, *, base_url: str, api_key: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def complete_json(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout_seconds: float,
        strict_schema: bool,
    ) -> str:
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=0,
                    max_tokens=max_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": strict_schema,
                            "schema": schema,
                        },
                    },
                    extra_body={
                        "speculative.n_max": 0,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise LocalModelClientError("VLM_TIMEOUT", "Yerel VLM zaman aşımına uğradı.") from exc
        except Exception as exc:
            raise LocalModelClientError(
                "MODEL_UNAVAILABLE", "Yerel VLM sunucusuna erişilemedi."
            ) from exc
        raw = response.choices[0].message.content
        if not raw:
            raise LocalModelClientError("VLM_EMPTY_RESPONSE", "Yerel VLM boş yanıt döndürdü.")
        if response.choices[0].finish_reason == "length":
            raise LocalModelClientError("VLM_TRUNCATED", "Yerel VLM çıktısı token sınırında kesildi.")
        return raw


__all__ = ["LocalModelClient", "LocalModelClientError", "OpenAICompatibleLocalClient"]
