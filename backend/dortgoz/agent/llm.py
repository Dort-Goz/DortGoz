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


# ---- ölçüm yardımcıları (izleme akışında gösterilir) ----

_CTX_CACHE: dict[str, int | None] = {}


def call_stats(resp) -> dict:
    """Bir çağrının token sayıları + llama.cpp hız ölçümleri.

    `timings` OpenAI şemasında yok; llama.cpp ek alan olarak döndürüyor ve
    pydantic bunu `model_extra` içinde tutuyor. Alan yoksa sessizce boş döner
    (başka bir sunucuya bağlanınca hat çalışmaya devam etsin).
    """
    t = (getattr(resp, "model_extra", None) or {}).get("timings") or {}
    u = getattr(resp, "usage", None)
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", None),
        "completion_tokens": getattr(u, "completion_tokens", None),
        "pp_tps": t.get("prompt_per_second"),
        "gen_tps": t.get("predicted_per_second"),
    }


async def context_size(model: str) -> int | None:
    """Modelin slot bağlam boyutu (`n_ctx`) — bağlam doluluk yüzdesi için.

    model sunucusunun `/upstream/<model>/props` ucundan okunur ve önbelleğe alınır.
    ⚠ Erişim kapısı yalnız `/v1/*` açar → burası 403 döner; o durumda
    None döneriz ve arayüz yüzde yerine yalnız token sayısını gösterir.
    """
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
