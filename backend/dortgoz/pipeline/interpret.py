"""[4] YORUMLAMA — tek VLM çağrısı → şema-garantili WindowReport.

Çoklu-görüntü istemi (kareler + zaman damgaları + [hafta 2] dedektör metaverisi)
llama.cpp'ye gider; JSON `response_format`/json_schema (GBNF) ile üretim anında
garanti edilir, dönüşte ayrıca Pydantic ile doğrulanır (A6: kısıtlı üretim +
savunma katmanı birlikte).

Canlı doğrulama (2026-08-03, `qwen3.6-35b-a3b-vision-256k`):
  - mtmd çoklu-görüntü ✓ (1/2/4/8 kare tek istekte)
  - json_schema/GBNF ✓ (Türkçe özet + zaman damgalı olay listesi, geçerli JSON)
  - 320×240 kare ≈ 80 prompt token; ~0,24 sn/kare kodlama; üretim 73 tok/sn

A7 gereği ayrı ön eleme modeli YOK. A2 kararı (ucuz bakış eklenecek mi) açık —
eklenecekse aynı sunucuya ikinci bir istem modu olarak `glance_window()` gelir,
ayrı model/servis olarak değil.

TODO(hafta 2): dedektör metaverisi + hareket bölgesi görsel işaretleri istemde
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any

from ..agent.llm import main_client
from ..config import settings
from ..events import WindowReport
from .ingest import grab_frame

SYSTEM_TR = (
    "Sen bir güvenlik kamerası görüntü analiz uzmanısın. Sana tek bir kameranın "
    "aynı zaman penceresinden alınmış kareler zaman damgalarıyla veriliyor. "
    "Gördüklerini Türkçe, kısa ve operasyonel dille raporla. Yalnızca karelerde "
    "GÖRDÜĞÜNÜ yaz; emin olmadığın çıkarımları 'uncertainties' alanına koy. "
    "Olay yoksa 'events' boş kalsın — olay uydurma.\n\n"
    "`anomaly_type` pencerenin baskın olay sınıfıdır. Dikkat gerektiren bir durum "
    "yoksa `normal` yaz. Bir şey oluyor ama listedeki sınıflardan hiçbirine "
    "oturmuyorsa `bilinmeyen` yaz — zorlama sınıflandırma yapma.\n\n"
    "`severity_hint` ölçeği: `dusuk` = olağan hareketlilik (yürüyen insan, park "
    "eden araç, sahne/ışık değişimi) — bunlar ALARM DEĞİLDİR; `orta` ve üstünü "
    "yalnız gerçekten müdahale gerektiren durumlar için kullan."
)


def _inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """`$ref`/`$defs` içeren Pydantic şemasını düz şemaya çevirir.

    llama.cpp'nin GBNF dönüştürücüsüne referanssız, kendi kendine yeten bir
    şema vermek en güvenlisi — böylece şema tek kaynaktan (WindowReport)
    türetilirken dilbilgisi üretimi de sorunsuz olur.
    """
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return walk(json.loads(json.dumps(defs[name])))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def report_schema() -> dict[str, Any]:
    """WindowReport'tan modelin üreteceği alanların şemasını türetir.

    `type`, `window_start`, `window_end` çıkarılır: bunlar bizde zaten kesin
    olarak var — modele ürettirmek hem token harcar hem hata yüzeyi açar.
    """
    schema = _inline_defs(WindowReport.model_json_schema())
    props = schema.get("properties", {})
    for field in ("type", "window_start", "window_end"):
        props.pop(field, None)
    schema["required"] = [f for f in ("anomaly_type", "summary", "events", "uncertainties")
                          if f in props]
    schema["additionalProperties"] = False
    schema.pop("title", None)
    return schema


def _image_part(jpeg: bytes) -> dict[str, Any]:
    b64 = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


# ---- [3] UCUZ BAKIŞ (A2 kolu iii) ----

GLANCE_SYSTEM_EN = (
    "You are a surveillance triage filter. You see a few frames from one camera "
    "window. Decide whether the window needs an operator's attention. "
    "Answer with exactly one word: YES or NO."
)
# İstem İngilizce: iç sinyal, kullanıcıya gitmiyor; zorunlu tek-kelime seçimde
# İngilizce daha kararlı. Türkçe yalnız brifing katmanında (derin okuma).

GLANCE_QUESTION = (
    "Does this window contain an incident requiring operator attention "
    "(violence, accident, fire, theft, intrusion, a person down)? "
    "Answer YES or NO."
)


async def glance_window(
    video: Path,
    window: tuple[float, float],
    keyframes: list[float],
    meta: str = "",
) -> float:
    """Ucuz bakış: pencere ilgi çekici mi? **P(YES) olasılığı** döndürür.

    Karar argmax'tan DEĞİL logprobs'tan okunur — canlı ölçümde (2026-08-03)
    argmax `NO` derken YES kütlesi %12,5 çıkan bir pencere görüldü; sert argmax
    onu düşürürdü. Eşik çağıran tarafta, recall'a göre ayarlanır.
    """
    start, end = window
    content: list[dict[str, Any]] = []
    for t in keyframes:
        content.append({"type": "text", "text": f"[t={t:.1f}s]"})
        content.append(_image_part(await grab_frame(video, t)))
    question = GLANCE_QUESTION
    if meta:
        question += f"\n\nDetector data:\n{meta}"
    content.append({"type": "text", "text": question})

    client = main_client()
    resp = await client.chat.completions.create(
        model=settings.main_model,
        messages=[{"role": "system", "content": GLANCE_SYSTEM_EN},
                  {"role": "user", "content": content}],
        max_tokens=2,
        temperature=0,
        logprobs=True,
        top_logprobs=8,
        extra_body={
            "speculative.n_max": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    return _yes_probability(resp)


def _yes_probability(resp) -> float:
    """İlk üretilen token'ın alternatifleri arasından YES kütlesini toplar."""
    lp = resp.choices[0].logprobs
    if not lp or not lp.content:
        # logprobs yoksa argmax'a düş (bilgi kaybı — eşik ayarı yapılamaz)
        return 1.0 if "YES" in (resp.choices[0].message.content or "").upper() else 0.0
    yes = no = 0.0
    for alt in lp.content[0].top_logprobs:
        token = alt.token.strip().upper()
        prob = math.exp(alt.logprob)
        if token.startswith("YES"):
            yes += prob
        elif token.startswith("NO"):
            no += prob
    total = yes + no
    return yes / total if total > 0 else 0.0


async def interpret_window(
    video: Path,
    window: tuple[float, float],
    keyframes: list[float],
    meta: str = "",
) -> WindowReport:
    """Bir pencereyi tek VLM çağrısıyla yorumlar; şema-geçerli WindowReport döner."""
    start, end = window
    content: list[dict[str, Any]] = []
    for t in keyframes:
        content.append({"type": "text", "text": f"[t={t:.1f}s]"})
        content.append(_image_part(await grab_frame(video, t)))

    task = (
        f"Yukarıdaki kareler {start:.0f}-{end:.0f} sn penceresine aittir. "
        "Pencereyi özetle ve dikkat gerektiren olayları zaman damgasıyla listele. "
        "Zaman damgaları kareler üzerinde yazan saniyelerle tutarlı olmalı."
    )
    if meta:
        task += f"\n\nAlgı katmanı verisi:\n{meta}"
    content.append({"type": "text", "text": task})

    client = main_client()
    resp = await client.chat.completions.create(
        model=settings.main_model,
        messages=[{"role": "system", "content": SYSTEM_TR},
                  {"role": "user", "content": content}],
        max_tokens=settings.interpret_max_tokens,
        temperature=0,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "window_report", "strict": True,
                            "schema": report_schema()},
        },
        extra_body={
            # mmproj + MTP birlikte iken istek başına şart (yoksa çökme)
            "speculative.n_max": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
    )
    raw = resp.choices[0].message.content or "{}"
    # GBNF üretim anında garanti eder; Pydantic ikinci savunma katmanı (A6)
    return WindowReport(window_start=start, window_end=end, **json.loads(raw))
