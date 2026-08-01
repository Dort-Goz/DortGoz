"""OpenAI-uyumlu yerel model istemcileri.

Ana model: model sunucusu (llama.cpp/Vulkan) — yorumlama + ajan + Türkçe.
Ön eleme:  RTX 4060 vLLM (MiniCPM-V 4.6) — "bu pencerede bir şey var mı?"

Şema-garantili JSON: llama.cpp tarafında response_format/json_schema (GBNF)
kullanılır; vLLM çıktısı yalnızca yönlendirme amaçlıdır, tekrar doğrulanır.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from ..config import settings


def main_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.llama_base_url, api_key=settings.api_key)


def triage_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.vllm_base_url, api_key=settings.api_key)
