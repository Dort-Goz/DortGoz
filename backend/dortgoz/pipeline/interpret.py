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

import asyncio
import base64
import json
import math
from pathlib import Path
from typing import Any

from ..agent.llm import call_stats, main_client
from ..config import settings
from ..events import WindowReport
from .ingest import grab_frame

SYSTEM_TR = (
    "Sen bir güvenlik kamerası görüntü analiz uzmanısın. Sana tek bir kameranın "
    "aynı zaman penceresinden alınmış kareler zaman damgalarıyla veriliyor. "
    "Gördüklerini Türkçe, kısa ve operasyonel dille raporla. Yalnızca karelerde "
    "GÖRDÜĞÜNÜ yaz; emin olmadığın çıkarımları 'uncertainties' alanına koy. "
    "Olay yoksa 'events' boş kalsın — olay uydurma.\n\n"
    # ⚠ Burada SINIF SÖZLÜĞÜ denendi ve GERİ ALINDI (2026-08-05): dar tanımlar
    # ("hirsizlik = mal alıp götürme" gibi) sınıflandırmayı düzeltmedi ama ALARM
    # kararına sızdı — hiçbir sınıfa tam oturmayan gerçek bir olay (gece kapalı
    # dükkân önünde motosikletle gelip hızla ayrılan iki kişi) 4/4 koşuda
    # `normal`e düştü, yani tek Vandalism yakalaması kayboldu. Sınıf hatasını
    # şema alan SIRASI çözüyor (aşağıdaki report_schema notu), istem değil.
    "`anomaly_type` pencerenin baskın olay sınıfıdır ve olayları yazdıktan "
    "SONRA seçilir. Dikkat gerektiren bir durum yoksa `normal` yaz. Bir şey "
    "oluyor ama listedeki sınıflardan hiçbirine oturmuyorsa `bilinmeyen` yaz "
    "— zorlama sınıflandırma yapma.\n\n"
    "`severity_hint` ölçeği: `dusuk` = olağan hareketlilik (yürüyen insan, park "
    "eden araç, sahne/ışık değişimi) — bunlar ALARM DEĞİLDİR; `orta` ve üstünü "
    "yalnız gerçekten müdahale gerektiren durumlar için kullan."
)

# İki kademeli çıktının sözleşme paragrafı — şemaya (tier_schema) bağlı olduğu
# için SYSTEM_TR'den AYRI tutulur ve etkin sistem istemine mekanik eklenir:
# deney paneli sistem istemini değiştirse de `durum` dalı açıklamasız kalmaz.
TIER_TR = (
    "ÇIKTI KADEMESİ: Önce `summary` alanında gördüğünü betimle, SONRA `durum` "
    "seç. Betimlediklerinde müdahale/dikkat gerektiren (orta ve üstü şiddette) "
    "hiçbir şey yoksa `durum: \"olagan\"` de ve orada dur — olay listesi yazma. "
    "Dikkat gerektiren bir şey varsa ya da EMİN DEĞİLSEN `durum: \"dikkat\"` "
    "seç ve tam raporu üret — kaçırılan olay yanlış alarmdan pahalıdır.\n\n"
    "ÖZET UZUNLUĞU: Olağan pencerede özet TEK KISA CÜMLE (en çok ~15 kelime): "
    "sadece kim ne yapıyor. Kare kare anlatma, saniye saniye zaman damgası verme, "
    "mekânın ne olduğunu HER pencerede yeniden tarif etme — operatör mekânı zaten "
    "biliyor, tekrar onun için gürültüdür. Sahne boşsa tek kelimeyle geç. "
    "Ayrıntılı anlatım YALNIZ `dikkat` penceresinde gerekir."
)

# Kullanıcı istemi şablonu — {start}/{end} pencere sınırlarıyla doldurulur
# (düz replace; deney panelinden gelen serbest metinde kaçış derdi olmasın diye
# str.format kullanılmıyor).
TASK_TR = (
    "Yukarıdaki kareler {start}-{end} sn penceresine aittir. "
    "Pencereyi özetle ve dikkat gerektiren olayları zaman damgasıyla listele. "
    "Zaman damgaları kareler üzerinde yazan saniyelerle tutarlı olmalı."
)


# ---- [5] OLAY ÜZERİNDEN İKİNCİ GEÇİŞ (2026-08-05) ----
# 30 sn'lik pencereler TESPİT için ucuz ve paralel, ama uzun bir olayı parçalıyor:
# ölçümde 270 sn'lik tek saldırı 9 pencereye bölündü, ortadaki pencere bağlamsız
# kaldığı için "normal" dedi ve defterdeki olay İKİYE AYRILDI. Çözüm: olay
# kapandığında sınırları ARTIK BİLİNDİĞİ için tüm aralık TEK çağrıda yeniden
# okunur — anlatı (öncesi/zirve/sonrası) ancak bütünü gören bir bağlamda dürüst olur.
REVIEW_SYSTEM_TR = (
    "Sen bir güvenlik kamerası olay analiz uzmanısın. Sana TEK bir olayın "
    "tamamına yayılmış kareler zaman damgalarıyla veriliyor. Görevin olayı "
    "bütün olarak değerlendirmek: nasıl başladı, en kritik an hangisi, nasıl "
    "sonuçlandı. Türkçe, kısa ve operasyonel yaz. Yalnızca karelerde GÖRDÜĞÜNÜ "
    "yaz; emin olmadığını 'belirsizlikler'e koy. Pencere pencere bakan bir ön "
    "analiz bu olayı parçalı görmüş olabilir — sen bütünlüklü karar ver, "
    "gerekiyorsa sınıfı ve riski düzelt."
)


def review_schema() -> dict[str, Any]:
    """Olay-geneli ikinci geçişin şeması (Bengisu'nun öncesi-zirve-sonrası tasarımı)."""
    return {
        "type": "object",
        "properties": {
            "baslangic": {"type": "string"},     # olay nasıl başladı
            "zirve": {"type": "string"},         # en kritik an
            "sonuc": {"type": "string"},         # nasıl bitti
            "zirve_t": {"type": "number"},       # zirve anının video zamanı (sn)
            # Sınıf listesi tek kaynaktan (events.py) türetilir — taksonomi
            # değişince burası kendiliğinden uyar
            "anomaly_type": {"enum": WindowReport.model_json_schema()
                             ["properties"]["anomaly_type"]["enum"]},
            "risk": {"enum": ["dusuk", "orta", "yuksek", "kritik"]},
            "belirsizlikler": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["baslangic", "zirve", "sonuc", "zirve_t", "anomaly_type", "risk",
                     "belirsizlikler"],
        "additionalProperties": False,
    }


async def review_incident(
    video: Path,
    span: tuple[float, float],
    keyframes: list[float],
    notes: list[str],
    *,
    model: str = "",
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Kapanmış bir olayın TÜM aralığını tek bağlamda yeniden okur."""
    start, end = span
    content = await _frame_parts(video, keyframes)
    task = (
        f"Yukarıdaki kareler {start:.0f}-{end:.0f} sn arasındaki TEK bir olayın "
        f"tamamını kapsıyor ({end - start:.0f} sn). Olayı bütün olarak değerlendir: "
        "nasıl başladı, zirve anı hangisi, nasıl sonuçlandı."
    )
    if notes:
        joined = " · ".join(notes[:12])
        task += (f"\n\nPencere pencere yapılmış ÖN gözlemler (parçalı olabilir, "
                 f"doğrulaman gereken taslaktır): {joined}")
    content.append({"type": "text", "text": task})

    client = main_client()
    resp = await client.chat.completions.create(
        model=model or settings.main_model,
        messages=[{"role": "system", "content": REVIEW_SYSTEM_TR},
                  {"role": "user", "content": content}],
        max_tokens=settings.interpret_max_tokens,
        temperature=0,
        response_format={"type": "json_schema",
                         "json_schema": {"name": "incident_review", "strict": True,
                                         "schema": review_schema()}},
        # Görüntü isteği → spekülasyon kapalı (hız; bkz. interpret_window notu)
        extra_body={"speculative.n_max": 0,
                    "chat_template_kwargs": {"enable_thinking": False}},
    )
    if stats is not None:
        stats.update(call_stats(resp))
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fixed = repair_truncated_json(raw)
        if fixed is None:
            raise
        return json.loads(fixed)


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
    # ⚠ ALAN SIRASI ÜRETİM SIRASIDIR (GBNF şemayı sırayla dilbilgisine çevirir).
    # `anomaly_type` en SONA alındı: sınıf, olaylar SAYILDIKTAN sonra seçilsin.
    # Önceki sırada (sınıf ikinci alan) model tek cümlelik özetten sonra sınıfa
    # bağlanıyordu ve kalabalığın müdahalesine `yangin` diyebiliyordu (2026-08-05).
    order = ("summary", "events", "uncertainties", "anomaly_type")
    schema["properties"] = {k: props[k] for k in order if k in props}
    schema["required"] = [f for f in order if f in props]
    schema["additionalProperties"] = False
    schema.pop("title", None)
    return schema


def tier_schema() -> dict[str, Any]:
    """İki kademeli şema (Cerberus deseni) — üretim token'ı ölçülen darboğaz.

    Alan SIRASI kasıtlı: her iki dal da `summary` ile AÇILIR, `durum` ondan
    sonra gelir — model önce gözlemler, kademeye gözlemden sonra karar verir.
    (İlk sürüm `durum`u ilk token yapmıştı; canlı probda sınırdaki bir `orta`
    olayı olağana yutuldu — betimleme zinciri kırılınca geri çağırma düşüyor.)
    Tasarruf olay/uncertainty yapısının atlanmasından gelir, gözlemden değil.
    """
    full = report_schema()
    rest = {k: v for k, v in full["properties"].items() if k != "summary"}
    return {"oneOf": [
        {"type": "object",
         "properties": {"summary": {"type": "string"}, "durum": {"enum": ["olagan"]}},
         "required": ["summary", "durum"], "additionalProperties": False},
        {"type": "object",
         "properties": {"summary": {"type": "string"},
                        "durum": {"enum": ["dikkat"]}, **rest},
         "required": ["summary", "durum",
                      *[f for f in full["required"] if f != "summary"]],
         "additionalProperties": False},
    ]}


def repair_truncated_json(raw: str) -> str | None:
    """Token sınırında kesilmiş JSON'ı son TAM öğeye kadar kurtarır.

    GBNF üretim anında geçerli bir ÖNEK garanti eder ama bitmiş olmayı garanti
    etmez: olay sayısı çoksa çıktı `max_tokens`'ta ortadan kesilir ve
    `json.loads` patlar. Yarım kalan öğeyi atıp açık yapıları kapatarak
    pencereyi tümden kaybetmek yerine olayların tamamlanmış kısmını kurtarırız.
    """
    stack: list[str] = []
    in_str = esc = False
    cut: int | None = None
    cut_stack: list[str] = []
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                # dize bittiği an güvenli nokta DEĞİL: ardından ':' gelebilir
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if stack:
                stack.pop()
            cut, cut_stack = i + 1, list(stack)   # kapanan yapı = tamamlanmış öğe
        elif ch == ",":
            # Virgül YALNIZ öğe sınırındaysa güvenli: dizi içindeyken (bir sonraki
            # öğeye geçiş) ya da en dış nesnenin alanları arasında. Yarım kalan bir
            # olay nesnesinin İÇİNDEKİ virgülden kesmek geçerli JSON üretir ama
            # şemayı ihlal eder (zorunlu alanları eksik olay) — o yüzden hariç.
            if stack and (stack[-1] == "]" or len(stack) == 1):
                cut, cut_stack = i, list(stack)
    if cut is None or not cut_stack:
        return None
    return raw[:cut] + "".join(reversed(cut_stack))


def _to_report(start: float, end: float, raw: str,
               truncated: bool = False) -> WindowReport:
    """Model JSON'ını WindowReport'a çevirir; iki kademeli `durum` dalını düzler.

    Tel sözleşmesi (events.py) DEĞİŞMEZ: `olagan` dalı normal/boş-olaylı
    WindowReport'a iner — defter ve arayüz kademelerden habersiz kalır.
    """
    note = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fixed = repair_truncated_json(raw)
        if fixed is None:
            raise
        data = json.loads(fixed)          # kurtarma da başarısızsa hata yükselsin
        note = ("⚠ model çıktısı token sınırında kesildi — olay listesi "
                "eksik olabilir (kurtarılan kısım raporlandı)")
    if truncated and note is None:
        note = "⚠ model çıktısı token sınırına dayandı — liste eksik olabilir"

    if data.pop("durum", None) == "olagan":
        report = WindowReport(window_start=start, window_end=end,
                              summary=data.get("summary", ""))
    else:
        data.setdefault("events", [])
        data.setdefault("uncertainties", [])
        report = WindowReport(window_start=start, window_end=end, **data)
    if note:
        # Kesilme operatörden GİZLENMEZ: belirsizlik alanı tam da bunun için var
        report.uncertainties.append(note)
    return report


def _image_part(jpeg: bytes) -> dict[str, Any]:
    b64 = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


async def _frame_parts(video: Path, keyframes: list[float]) -> list[dict[str, Any]]:
    """Kareleri EŞZAMANLI çeker, zaman damgası + görüntü çifti olarak dizer.

    Sıralı ffmpeg çağrıları pencere başına ~0,5-1 sn CPU beklemesiydi ve GPU bu
    sürede boş kalıyordu (2026-08-06 ölçümü, tam bölme koşusu). Kareler kısa
    ömürlü bağımsız süreçler — 6-16'lık pencere için sınırlama gerekmez.
    """
    jpegs = await asyncio.gather(*(grab_frame(video, t) for t in keyframes))
    parts: list[dict[str, Any]] = []
    for t, jpeg in zip(keyframes, jpegs):
        parts.append({"type": "text", "text": f"[t={t:.1f}s]"})
        parts.append(_image_part(jpeg))
    return parts


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
    content = await _frame_parts(video, keyframes)
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


def _dikkat_probability(resp) -> float | None:
    """İki kademeli çıktıda `durum` dal token'ının ham P(dikkat) kütlesi.

    Kritik ayrım: top_logprobs GRAMER MASKESİNDEN ÖNCEKİ model dağılımıdır
    (canlı probda 'normal' %12,5 ile ikinci sıradaydı — enum'da yokken). Yani bu
    değer modelin gerçek inancıdır: `olagan` kararı verilmiş bir pencerede
    yüksek P(dikkat) = sınırda kalmış aday → tırmandırma/yeniden sorgu hedefi.
    Qwen tokenizer'ı 'dikkat'ı 'd…' ile açar — önek eşleşmesi tek harfe bakar.
    """
    lp = resp.choices[0].logprobs
    if not lp or not lp.content:
        return None
    raw = ""
    for tk in lp.content:
        if raw.endswith('"durum": "') or raw.endswith('"durum":"'):
            total = dik = 0.0
            for alt in tk.top_logprobs:
                p = math.exp(alt.logprob)
                total += p
                if alt.token.strip().lower().startswith("d"):
                    dik += p
            return dik / total if total > 0 else None
        raw += tk.token
    return None


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
    *,
    model: str = "",
    system_prompt: str = "",
    task_prompt: str = "",
    tier_prompt: str = "",
    context: str = "",
    think: bool = False,
    stats: dict[str, Any] | None = None,
) -> WindowReport:
    """Bir pencereyi tek VLM çağrısıyla yorumlar; şema-geçerli WindowReport döner.

    `model`/`system_prompt`/`task_prompt`/`tier_prompt` boşsa varsayılanlara
    düşer — deney paneli (arayüz) ve bench bunları koşu başına geçirir.
    `stats` verilmişse ölçümlere ek olarak `durum_p` (dal token'ının ham
    P(dikkat) kütlesi) yazılır — sınırda kalan pencerelerin izi/tırmandırması.

    `think=True` = tırmandırma modu: düşünme açılır (llama.cpp grameri
    </think> SONRASINA erteliyor — canlı doğrulandı 2026-08-06) ve token
    tavanı yükselir (olaylı pencerede düşünce 2.500'ü aşabiliyor). Her
    pencerede KULLANILMAZ (sakin pencerede 12× token ölçüldü) — yalnız
    sınırda kalan pencerenin yeniden sorgusu ve olay-geneli 2. geçiş için.
    """
    start, end = window
    content = await _frame_parts(video, keyframes)

    task = ((task_prompt or TASK_TR)
            .replace("{start}", f"{start:.0f}")
            .replace("{end}", f"{end:.0f}"))
    if meta:
        task += f"\n\nAlgı katmanı verisi:\n{meta}"
    if context:
        task += f"\n\n{context}"
    content.append({"type": "text", "text": task})

    system = system_prompt or SYSTEM_TR
    if settings.two_tier:
        system = f"{system}\n\n{tier_prompt or TIER_TR}"

    client = main_client()
    resp = await client.chat.completions.create(
        model=model or settings.main_model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        max_tokens=(max(4000, settings.interpret_max_tokens) if think
                    else settings.interpret_max_tokens),
        temperature=0,
        # stats isteniyorsa dal token olasılığı da toplanır (yanıt gövdesine
        # birkaç KB ekler, üretimi değiştirmez — grammar maskesi öncesi inanç)
        logprobs=stats is not None,
        top_logprobs=8 if stats is not None else None,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "window_report", "strict": True,
                            "schema": tier_schema() if settings.two_tier
                                      else report_schema()},
        },
        extra_body={
            # Görüntü isteklerinde spekülasyonu KAPAT — çökme için değil HIZ için:
            # MTP taslakları görü token'larında tutmuyor (ölçüm 2026-08-06,
            # thinkingcap-27b-vision: korumasız 41 t/s vs korumalı 52 t/s).
            # ⚠ METİN yolunda (agent/graph.py sohbeti) GÖNDERİLMEZ — orada MTP
            # 36 → 75 t/s kazandırıyor. Eski "mmproj+MTP çöker" notu b10234'te
            # ARTIK GEÇERLİ DEĞİL (korumasız görüntü isteği sorunsuz çalıştı).
            "speculative.n_max": 0,
            "chat_template_kwargs": {"enable_thinking": think},
        },
    )
    if stats is not None:                    # token sayıları + PP/gen hızları
        stats.update(call_stats(resp))
        if settings.two_tier and (p := _dikkat_probability(resp)) is not None:
            stats["durum_p"] = p
    raw = resp.choices[0].message.content or "{}"
    # GBNF üretim anında garanti eder; Pydantic ikinci savunma katmanı (A6).
    # finish_reason "length" = çıktı bütçeye sığmadı → kesilmiş olabilir.
    return _to_report(start, end, raw,
                      truncated=resp.choices[0].finish_reason == "length")
