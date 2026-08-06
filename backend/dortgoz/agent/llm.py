"""OpenAI-uyumlu yerel model istemcileri.

Ana model: model sunucusu (llama.cpp/Vulkan) — yorumlama + ajan + Türkçe.
Ön eleme:  RTX 4060 vLLM (MiniCPM-V 4.6) — "bu pencerede bir şey var mı?"

Şema-garantili JSON: llama.cpp tarafında response_format/json_schema (GBNF)
kullanılır; vLLM çıktısı yalnızca yönlendirme amaçlıdır, tekrar doğrulanır.
"""

from __future__ import annotations

import asyncio
import random

from openai import AsyncOpenAI, RateLimitError

from ..config import settings


def main_client() -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.llama_base_url, api_key=settings.api_key)


# ---- geri basınç: uçuş sınırı + 429'da üstel geri çekilme ----
# Semafor döngüye bağlanır; bench (asyncio.run) ve uvicorn farklı döngüler
# kullandığından döngü başına bir semafor tutulur.
_sems: dict[int, asyncio.Semaphore] = {}


def _inflight() -> asyncio.Semaphore:
    key = id(asyncio.get_running_loop())
    if key not in _sems:
        _sems[key] = asyncio.Semaphore(max(1, settings.max_inflight))
    return _sems[key]


async def create_chat(client: AsyncOpenAI, **kwargs):
    """`chat.completions.create` — uçuş sınırı + 429'da geri çekilip yeniden dener.

    Çoklu-akış kipinde 24 koşu sunucuya sınırsız istek yığıyordu; sunucu 429
    döndürünce pencereler ATLANIYORDU (görüntü kaybı!). Doğru davranış geri
    basınç: aynı anda en çok `max_inflight` istek, 429'da 2s→20s üstel bekleme
    (+seğirme) ile `llm_retries` deneme. Yine olmazsa hata yükselir — çağıran
    (pencere yalıtımı) kararını verir.
    """
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
    raise RuntimeError("erişilemez")   # döngü ya döner ya raise eder


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
