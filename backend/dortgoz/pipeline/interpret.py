from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..agent.llm import call_stats, create_chat, main_client
from ..config import settings
from ..domain.evidence import FRAME_TIMESTAMP_TOLERANCE_SECONDS
from ..domain.taxonomy import CanonicalEventType, legacy_ws_label_from_canonical
from ..events import EventEvidenceRef, FrameReference, Risk, WindowReport
from ..tools.protocols import VlmSchemaError
from ..utils import inline_defs
from .ingest import grab_frame, shared_clip
from .thinking import thinking_extra


def learned_category_block() -> str:

    if not settings.category_rules_enabled:
        return ""
    from ..services import category_rules

    return category_rules.prompt_block(category_rules.load(settings.runs_dir))


SYSTEM_TR = (
    "Sen bir güvenlik kamerası görüntü analiz uzmanısın. Sana tek bir kameranın "
    "aynı zaman penceresinden alınmış kareler zaman damgalarıyla veriliyor. "
    "Gördüklerini Türkçe, kısa ve operasyonel dille raporla. Yalnızca karelerde "
    "GÖRDÜĞÜNÜ yaz; emin olmadığın çıkarımları 'uncertainties' alanına koy. "
    "Olay yoksa 'events' boş kalsın — olay uydurma. Her olayın `evidence` "
    "alanında yalnızca sana verilen FRAME_ID değerlerini kullan; yeni kare kimliği "
    "uydurma. Kanıt iddiası kısa, Türkçe ve yalnız gözlemlenebilir olmalı; kimlik "
    "iddiası kurma, hukukî hüküm verme.\n\n"
    "OLAY EŞİĞİ: `events` listesi yalnız operatör müdahalesi gerektirebilecek "
    "durumlar içindir. Sahnenin olağan işleyişini olay olarak yazma. Araç "
    "geçişi, trafiğin akması, kavşakta durup kalkma, yaya yürümesi, park etme, "
    "bir kişinin durması veya beklemesi olay DEĞİLDİR — bunlar `summary` "
    "alanına aittir.\n\n"
    "KAMERA VE YAYIN SORUNU OLAY DEĞİLDİR: kameranın sarsılması, açı veya "
    "preset değişimi, far parlaması, gece gürültüsü, sıkıştırma bozulması, "
    "kararma, donma, sahnenin aniden başka bir görüntüye geçmesi. Bunları "
    "`events` içine yazma; kayda değerse `uncertainties` alanına tek satır yaz.\n\n"
    "`event_type` olayın SINIFIDIR, sahnedeki nesnenin türü değildir. Bir sınıfı "
    "ancak o sınıfın gerektirdiği EYLEMİ karelerde gördüysen seç:\n"
    "- `physical_fight`: karşılıklı fiziksel şiddet, boğuşma, yumruk, itişme\n"
    "- `assault`: bir kişinin başkasına tek taraflı saldırısı, darp\n"
    "- `possible_theft`: eşya veya para alma, gizleme, ödemeden çıkma\n"
    "- `possible_armed_incident`: görünür silah (tabanca, tüfek, bıçak)\n"
    "- `fire_smoke`: alev veya duman\n"
    "- `explosion`: patlama ânı, şok dalgası, ani parlama\n"
    "- `vehicle_collision`: araçların çarpışması, devrilmesi, yoldan çıkması\n"
    "- `vandalism`: mala kasıtlı zarar verme\n"
    "Listedeki eylemi gördüysen o sınıfı seç; şiddet görüp silah görmediysen "
    "`possible_armed_incident` YAZMA. Araç görmek çarpışma, kişi görmek "
    "hırsızlık veya silah demek DEĞİLDİR. Hiçbir sınıfın eylemi görünmüyorsa "
    "olayı hiç yazma; şüpheni `uncertainties` alanına koy. `unknown_anomaly` "
    "yalnız açıkça anormal bir şey oluyor ama yukarıdaki sınıfların hiçbirine "
    "oturmuyorsa kullanılır.\n\n"
    "Mümkünse iki ayrı destekleyici kare, "
    "tek kare yeterliyse en az bir kare göster; kanıt yoksa olayı kesinleştirme, "
    "belirsizliği `uncertainties` alanına yaz.\n\n"
    "`anomaly_type` pencerenin baskın canonical olay sınıfıdır ve olayları yazdıktan "
    "SONRA seçilir. Dikkat gerektiren bir durum yoksa `normal` yaz. Bir şey "
    "oluyor ama listedeki sınıflardan hiçbirine oturmuyorsa `unknown_anomaly` yaz "
    "— zorlama sınıflandırma yapma.\n\n"
    "`severity_hint` ölçeği: `dusuk` = olağan hareketlilik (yürüyen insan, park "
    "eden araç, sahne/ışık değişimi) — bunlar ALARM DEĞİLDİR; `orta` ve üstünü "
    "yalnız gerçekten müdahale gerektiren durumlar için kullan."
)


MAGAZA_EK = (
    "\n\nBu bir mağaza veya kasa kamerasıdır. Ürünü CEBE veya giysi içine "
    "GİZLEME, kasadan izinsiz para alma, ödemeden kasa hattını geçip çıkma "
    "`possible_theft` kapsamında dikkat gerektirir. Şunlar NORMAL işleyiştir, "
    "olay yazma: tezgâhın veya kasanın ARKASINDAKİ personelin ürün alması, "
    "eğilmesi, çekmece açması; müşterinin ödeme sırasında ürünü poşete koyması; "
    "rafta ürün inceleyip yerine bırakma; kameraya yaklaşmak; güvenlik "
    "görevlisinin rutin devriyesi."
)

SYSTEM_TR_GENIS = SYSTEM_TR + MAGAZA_EK

IKINCI_EK = (
    "\n\nİKİNCİ GÖRÜŞ VERİYORSUN: Başka bir model bu pencereyi zaten okudu ve "
    "dikkat gerektiren bir şey bulmadı, ama sahnede belirgin hareket var. "
    "Görevin ONUN KAÇIRMIŞ OLABİLECEĞİ ince olayları aramaktır: yerde "
    "hareketsiz yatan kişi; silaha benzer nesne; kapı, vitrin veya kilit "
    "zorlanması; duman veya alev; bir kişiye yönelen fiziksel temas; çarpışmış "
    "ya da yoldan çıkmış araç; bir ürünün cebe veya çantaya gizlenmesi.\n\n"
    "⚠ Daha dikkatli bakmak, daha çok olay yazmak demek değildir. Yukarıdaki "
    "olay eşiği ve sınıf kuralları burada da geçerlidir: aradığın eylemi "
    "karelerde GÖRMEDİYSEN olay yazma. Uzanmak, dokunmak, tutmak veya incelemek "
    "tek başına olay değildir. Gördüğünü `summary`de betimle, şüpheni "
    "`uncertainties` alanına koy. Rapor KISA olsun."
)

SYSTEM_TR_IKINCI = SYSTEM_TR + IKINCI_EK


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

TASK_TR = (
    "Yukarıdaki kareler {start}-{end} sn penceresine aittir. "
    "Pencereyi özetle ve dikkat gerektiren olayları zaman damgasıyla listele. "
    "Her evidence kaydında yalnız ilgili karenin FRAME_ID değerini aynen kullan."
)


class VlmEvidenceContractError(VlmSchemaError):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code=code)


REVIEW_SYSTEM_TR = (
    "Sen bir güvenlik kamerası olay analiz uzmanısın. Sana TEK bir olayın "
    "tamamına yayılmış kareler zaman damgalarıyla veriliyor. Görevin olayı "
    "bütün olarak değerlendirmek: nasıl başladı, en kritik an hangisi, nasıl "
    "sonuçlandı — ve olayın GERÇEK başlangıç/bitiş saniyelerini sayısal ver (kare zamanlarından yorumla; olay iki kare arasında başlamış olabilir). "
    "Türkçe, kısa ve operasyonel yaz. Yalnızca karelerde GÖRDÜĞÜNÜ "
    "yaz; emin olmadığını 'belirsizlikler'e koy. Pencere pencere bakan bir ön "
    "analiz bu olayı parçalı görmüş olabilir — sen bütünlüklü karar ver, "
    "gerekiyorsa sınıfı yeniden değerlendir. Her evidence kaydında yalnız sana "
    "verilen FRAME_ID değerlerini aynen kullan. "
    "Risk alanı yalnız model ipucudur; final risk değildir."
)

REVIEW_SYSTEM_STRICT_TR = REVIEW_SYSTEM_TR + (
    " Ön analiz yalnız doğrulanacak bir taslaktır. Videoda müdahale gerektiren "
    "somut olay yoksa event_type normal ve risk dusuk seç. Yürüme, bekleme, "
    "telefon kullanma, ürüne uzanma, ürünü elde tutma ve olağan alışveriş olay "
    "değildir. possible_theft için ürünü gizleme, ödeme yapmadan çıkma veya "
    "izinsiz alma eylemi videoda görünmelidir. Şüphe tek başına olay değildir."
)


class IncidentReviewResult(BaseModel):

    model_config = ConfigDict(extra="forbid")

    baslangic: str
    baslangic_t: float = Field(ge=0, allow_inf_nan=False)
    zirve: str
    zirve_t: float = Field(ge=0, allow_inf_nan=False)
    sonuc: str
    bitis_t: float = Field(ge=0, allow_inf_nan=False)
    event_type: CanonicalEventType
    risk: Risk
    evidence: list[EventEvidenceRef] = Field(min_length=1)
    belirsizlikler: list[str]


def review_schema(frame_ids: list[str] | None = None) -> dict[str, Any]:
    schema = inline_defs(IncidentReviewResult.model_json_schema())
    schema.pop("title", None)
    evidence = schema.get("properties", {}).get("evidence")
    if evidence is not None:
        _drop_evidence_timestamp(evidence)
        _constrain_evidence_frame_ids(evidence, frame_ids)
    return schema


def _constrain_evidence_frame_ids(
    evidence_schema: dict[str, Any],
    frame_ids: list[str] | None,
) -> None:
    if not frame_ids:
        return
    item = evidence_schema.get("items", {})
    props = item.get("properties")
    if props is not None and "frame_id" in props:
        props["frame_id"] = {"enum": list(frame_ids)}


def _drop_evidence_timestamp(evidence_schema: dict[str, Any]) -> None:
    item = evidence_schema.get("items", {})
    item.get("properties", {}).pop("timestamp", None)
    if "required" in item:
        item["required"] = [k for k in item["required"] if k != "timestamp"]


def _anchor_event_time(event: dict[str, Any], start: float, end: float) -> None:
    evidence = event.get("evidence") or []
    anchored = next(
        (ref["timestamp"] for ref in evidence
         if isinstance(ref, dict) and isinstance(ref.get("timestamp"), int | float)),
        None,
    )
    if anchored is not None:
        event["t"] = float(anchored)
        return
    raw_t = event.get("t")
    if not isinstance(raw_t, int | float) or not start <= raw_t <= end:
        event["t"] = (start + end) / 2


def _fill_evidence_timestamps(
    evidence_dicts: list[Any],
    frame_refs: list[FrameReference],
) -> None:
    allowed = {frame.frame_id: frame.timestamp for frame in frame_refs}
    for item in evidence_dicts:
        if isinstance(item, dict) and "timestamp" not in item:
            item["timestamp"] = allowed.get(item.get("frame_id"), 0.0)


async def review_incident(
    video: Path,
    span: tuple[float, float],
    keyframes: list[float],
    notes: list[str],
    *,
    model: str = "",
    stats: dict[str, Any] | None = None,
    timing: dict[str, float | int] | None = None,
    captured_frames: dict[str, tuple[FrameReference, bytes]] | None = None,
    current_type: str = "",
) -> dict[str, Any]:
    start, end = span
    frame_refs = build_video_references(start, end)
    content = await _video_parts(video, start, end, frame_refs)
    task = (
        f"Yukarıdaki video {start:.0f}-{end:.0f} sn arasındaki TEK bir olayın "
        f"tamamını kapsıyor ({end - start:.0f} sn). Olayı bütün olarak değerlendir: "
        "nasıl başladı, zirve anı hangisi, nasıl sonuçlandı. "
        "baslangic/zirve/sonuc alanlarına sahnede GÖRÜNENİ anlatan kısa Türkçe "
        "cümleler yaz; kare adı (f_001 gibi) veya saniye değeri yazma — zaman "
        "bilgisi zaten *_t alanlarında."
    )
    if current_type:
        task += (
            f"\n\nÖn analiz bu olayı {current_type} olarak sınıfladı. Bütünü "
            "gördükten sonra en doğru sınıfı sen seç: ön analiz doğruysa koru, "
            "kareler farklı bir sınıfı gösteriyorsa düzelt. Kararını kanıt "
            "kareleriyle destekle."
        )
    if notes:
        joined = " · ".join(notes[:12])
        task += (f"\n\nPencere pencere yapılmış ÖN gözlemler (parçalı olabilir, "
                 f"doğrulaman gereken taslaktır): {joined}")
    content.append({"type": "text", "text": task})

    client = main_client()
    started = time.monotonic()
    try:
        resp = await create_chat(client,
            model=model or settings.second_opinion_model,
            messages=[{
                "role": "system",
                "content": REVIEW_SYSTEM_STRICT_TR
                if settings.incident_review_strict else REVIEW_SYSTEM_TR,
            },
                      {"role": "user", "content": content}],
            max_tokens=settings.interpret_max_tokens,
            temperature=0,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "incident_review", "strict": True,
                                             "schema": review_schema(
                                                 [f.frame_id for f in frame_refs]
                                             )}},
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    finally:
        _record_qwen_timing(timing, started)
    if stats is not None:
        stats.update(call_stats(resp))
    raw = resp.choices[0].message.content or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fixed = repair_truncated_json(raw)
        if fixed is None:
            raise
        payload = json.loads(fixed)
    if isinstance(payload, dict):
        _fill_evidence_timestamps(payload.get("evidence") or [], frame_refs)
        ev = payload.get("evidence") or []
        ev_ts = [r["timestamp"] for r in ev
                 if isinstance(r, dict) and isinstance(r.get("timestamp"), int | float)]
        fallback = ev_ts[0] if ev_ts else (start + end) / 2
        for key in ("baslangic_t", "zirve_t", "bitis_t"):
            v = payload.get(key)
            if not (isinstance(v, int | float) and start <= v <= end):
                payload[key] = fallback
        b, z, e = sorted((payload["baslangic_t"], payload["zirve_t"], payload["bitis_t"]))
        payload["baslangic_t"], payload["zirve_t"], payload["bitis_t"] = b, z, e
    review = IncidentReviewResult.model_validate(payload)
    _guard_incident_review_evidence(review, frame_refs)
    await _capture_evidence_frames(
        video,
        frame_refs,
        [evidence.frame_id for evidence in review.evidence],
        captured_frames,
    )
    return review.model_dump(mode="json")


ADJUDICATE_TYPES = [
    "normal", "unknown_anomaly",
    "physical_fight", "assault", "possible_theft", "possible_armed_incident",
    "fire_smoke", "explosion", "vehicle_collision", "vandalism",
]

ADJUDICATE_SYSTEM_TR = (
    "Sen güvenlik kamerası olay sınıflandırma uzmanısın. Sana olay diye "
    "işaretlenmiş bir kaydın kareleri veriliyor. Ön analiz yanılmış olabilir; "
    "görevin doğru sınıfı vermektir, bir anomali sınıfı seçmek zorunda "
    "değilsin.\n\n"
    "Önce şunu sor: karelerde operatör müdahalesi gerektiren somut bir EYLEM var "
    "mı? Yoksa `normal` seç. Olağan trafik akışı, yürüyen veya bekleyen "
    "insanlar, park etme, kamera sarsıntısı, açı değişimi, far parlaması ve "
    "görüntü bozulması `normal`dir. Bir şey oluyor ama aşağıdaki sınıfların "
    "hiçbirinin eylemi görünmüyorsa `unknown_anomaly` seç.\n\n"
    "Somut eylem varsa öncelik sırasıyla: görünür bir silah (tabanca, tüfek) "
    "varsa her durumda possible_armed_incident; patlama ânı (şok dalgası, ani "
    "parlama) explosion, sonrasındaki yangın fire_smoke; araçların ÇARPIŞMASI "
    "veya yoldan çıkması vehicle_collision; possible_theft YALNIZ mal alma veya "
    "gizleme eylemi görünüyorsa; kişiler arası fiziksel şiddet physical_fight "
    "veya assault; mala kasıtlı zarar vandalism.\n\n"
    "Sınıfı tahmin etme. Eylemi görmediysen `normal` ya da `unknown_anomaly` seç."
)


async def adjudicate_category(
    video: Path,
    span: tuple[float, float],
    keyframes: list[float],
    *,
    model: str = "",
    stats: dict[str, Any] | None = None,
    timing: dict[str, float | int] | None = None,
) -> tuple[str, float] | None:

    start, end = span
    frame_refs = build_video_references(start, end)
    content = await _video_parts(video, start, end, frame_refs)
    content.append({"type": "text", "text":
                    f"Video {start:.0f}-{end:.0f} sn arasındaki tek bir "
                    "olaya aittir. Olayın sınıfı nedir?"})
    client = main_client()
    started = time.monotonic()
    try:
        resp = await create_chat(client,
            model=model or settings.main_model,
            messages=[{"role": "system", "content": ADJUDICATE_SYSTEM_TR},
                      {"role": "user", "content": content}],
            max_tokens=64,
            temperature=0,
            logprobs=True,
            top_logprobs=8,
            response_format={"type": "json_schema", "json_schema": {
                "name": "sinif_hakemi", "strict": True,
                "schema": {"type": "object", "additionalProperties": False,
                           "properties": {"event_type": {
                               "type": "string", "enum": ADJUDICATE_TYPES}},
                           "required": ["event_type"]}}},
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    finally:
        _record_qwen_timing(timing, started)
    if stats is not None:
        stats.update(call_stats(resp))
    raw = resp.choices[0].message.content or "{}"
    try:
        value = json.loads(raw).get("event_type")
    except json.JSONDecodeError:
        return None
    if value not in ADJUDICATE_TYPES:
        return None
    lp = getattr(resp.choices[0], "logprobs", None)
    conf = _enum_confidence(getattr(lp, "content", None) or [], raw, value)
    return value, conf


def _enum_confidence(tokens: list, raw: str, value: str) -> float:

    try:
        start = raw.index(value)
    except ValueError:
        return 1.0
    pos = 0
    conf = 1.0
    prefix = ""
    for t in tokens:
        text = getattr(t, "token", "") or ""
        t_start, t_end = pos, pos + len(text)
        pos = t_end
        if t_end <= start or not text:
            continue
        if t_start >= start + len(value):
            break
        adaylar = [v for v in ADJUDICATE_TYPES if v.startswith(prefix)]
        if len(adaylar) <= 1:
            break
        secilen_p = math.exp(getattr(t, "logprob", 0.0))
        gecerli_p = 0.0
        for alt in getattr(t, "top_logprobs", None) or []:
            alt_text = (getattr(alt, "token", "") or "")
            parca = alt_text[max(0, start - t_start):]
            uzanti = prefix + parca
            if any(v.startswith(uzanti) or uzanti.startswith(v)
                   for v in adaylar):
                gecerli_p += math.exp(getattr(alt, "logprob", -50.0))
        conf *= secilen_p / max(gecerli_p, secilen_p)
        parca = text[max(0, start - t_start):]
        kalan = start + len(value) - max(t_start, start)
        prefix += parca[:kalan]
    return max(0.0, min(conf, 1.0))


def report_schema(frame_ids: list[str] | None = None) -> dict[str, Any]:
    schema = inline_defs(WindowReport.model_json_schema())
    props = schema.get("properties", {})
    for field in ("type", "window_start", "window_end"):
        props.pop(field, None)
    props["anomaly_type"] = {"enum": [item.value for item in CanonicalEventType]}
    event_schema = props["events"]["items"]
    event_props = event_schema["properties"]
    event_props["event_type"] = {"enum": [item.value for item in CanonicalEventType]}
    event_props["evidence"]["minItems"] = 1
    _drop_evidence_timestamp(event_props["evidence"])
    _constrain_evidence_frame_ids(event_props["evidence"], frame_ids)
    event_schema["required"] = list(event_props)
    event_schema["additionalProperties"] = False
    order = ("summary", "events", "uncertainties", "anomaly_type")
    schema["properties"] = {k: props[k] for k in order if k in props}
    schema["required"] = [f for f in order if f in props]
    schema["additionalProperties"] = False
    schema.pop("title", None)
    return schema


def tier_schema(frame_ids: list[str] | None = None) -> dict[str, Any]:
    full = report_schema(frame_ids)
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
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if stack:
                stack.pop()
            cut, cut_stack = i + 1, list(stack)
        elif ch == ",":
            if stack and (stack[-1] == "]" or len(stack) == 1):
                cut, cut_stack = i, list(stack)
    if cut is None or not cut_stack:
        return None
    return raw[:cut] + "".join(reversed(cut_stack))


def _to_report(
    start: float,
    end: float,
    raw: str,
    truncated: bool = False,
    frame_refs: list[FrameReference] | None = None,
) -> WindowReport:
    note = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fixed = repair_truncated_json(raw)
        if fixed is None:
            raise
        data = json.loads(fixed)
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
        if frame_refs is not None:
            for event in data["events"]:
                if isinstance(event, dict):
                    _fill_evidence_timestamps(event.get("evidence") or [], frame_refs)
                    _anchor_event_time(event, start, end)
            if note is not None:
                allowed_ids = {frame.frame_id for frame in frame_refs}
                salvaged = []
                for event in data["events"]:
                    if not isinstance(event, dict):
                        continue
                    event["evidence"] = [
                        ref for ref in (event.get("evidence") or [])
                        if isinstance(ref, dict) and ref.get("frame_id") in allowed_ids
                    ]
                    if event["evidence"]:
                        salvaged.append(event)
                data["events"] = salvaged
        raw_anomaly_type = data.get("anomaly_type")
        try:
            canonical_type = CanonicalEventType(raw_anomaly_type)
        except (TypeError, ValueError) as exc:
            if note is not None:
                data["anomaly_type"] = legacy_ws_label_from_canonical(
                    CanonicalEventType.UNKNOWN_ANOMALY
                ).value
            elif frame_refs is not None:
                raise VlmSchemaError(
                    f"canonical olmayan VLM anomaly_type: {raw_anomaly_type}",
                    code="INVALID_VLM_EVENT_TYPE",
                ) from exc
        else:
            data["anomaly_type"] = legacy_ws_label_from_canonical(canonical_type).value
        report = WindowReport(window_start=start, window_end=end, **data)
        if frame_refs is not None:
            _guard_evidence_references(report, frame_refs)
    if note:
        report.uncertainties.append(note)
    return report


def build_frame_references(keyframes: list[float]) -> list[FrameReference]:

    return [
        FrameReference(frame_id=f"f_{index:03d}", timestamp=timestamp)
        for index, timestamp in enumerate(keyframes)
    ]


def _guard_evidence_references(
    report: WindowReport,
    frame_refs: list[FrameReference],
) -> None:

    allowed = {frame.frame_id: frame.timestamp for frame in frame_refs}
    for event_index, event in enumerate(report.events):
        if event.event_type is None:
            raise VlmEvidenceContractError(
                "MISSING_VLM_EVENT_TYPE",
                f"events[{event_index}] canonical event_type içermiyor",
            )
        if not event.evidence:
            raise VlmEvidenceContractError(
                "MISSING_VLM_EVIDENCE_REFERENCE",
                f"events[{event_index}] en az bir evidence referansı içermeli",
            )
        for evidence_index, evidence in enumerate(event.evidence):
            expected_timestamp = allowed.get(evidence.frame_id)
            if expected_timestamp is None:
                raise VlmEvidenceContractError(
                    "INVALID_VLM_EVIDENCE_REFERENCE",
                    f"events[{event_index}].evidence[{evidence_index}] bilinmeyen "
                    f"frame_id kullanıyor: {evidence.frame_id}",
                )
            if abs(evidence.timestamp - expected_timestamp) > \
                    FRAME_TIMESTAMP_TOLERANCE_SECONDS:
                raise VlmEvidenceContractError(
                    "INVALID_VLM_EVIDENCE_TIMESTAMP",
                    f"{evidence.frame_id} için timestamp {evidence.timestamp}, "
                    f"beklenen {expected_timestamp}",
                )


def _guard_incident_review_evidence(
    review: IncidentReviewResult,
    frame_refs: list[FrameReference],
) -> None:

    allowed = {frame.frame_id: frame.timestamp for frame in frame_refs}
    for evidence_index, evidence in enumerate(review.evidence):
        expected_timestamp = allowed.get(evidence.frame_id)
        if expected_timestamp is None:
            raise VlmEvidenceContractError(
                "INVALID_INCIDENT_REVIEW_EVIDENCE_REFERENCE",
                f"evidence[{evidence_index}] bilinmeyen frame_id kullanıyor: {evidence.frame_id}",
            )
        if abs(evidence.timestamp - expected_timestamp) > FRAME_TIMESTAMP_TOLERANCE_SECONDS:
            raise VlmEvidenceContractError(
                "INVALID_INCIDENT_REVIEW_EVIDENCE_TIMESTAMP",
                f"{evidence.frame_id} için timestamp {evidence.timestamp}, "
                f"beklenen {expected_timestamp}",
            )


def _image_part(jpeg: bytes) -> dict[str, Any]:
    b64 = base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def build_video_references(start: float, end: float) -> list[FrameReference]:
    count = min(260, max(1, math.ceil(end - start)))
    return [
        FrameReference(
            frame_id=f"f_{index:03d}",
            timestamp=min(end, start + index),
        )
        for index in range(count)
    ]


async def _video_parts(
    video: Path,
    start: float,
    end: float,
    frame_refs: list[FrameReference],
) -> list[dict[str, Any]]:
    duration = end - start
    if duration <= 0 or duration > settings.video_input_max_seconds:
        raise VlmEvidenceContractError(
            "VIDEO_DURATION_INVALID",
            f"EVREN video süresi 0-{settings.video_input_max_seconds:.0f} sn arasında olmalı",
        )
    clip = await asyncio.wait_for(
        shared_clip(video, start, end, settings.video_input_width),
        timeout=settings.vlm_context_clip_timeout_seconds,
    )
    if len(clip) > 190 * 1024 * 1024:
        raise VlmEvidenceContractError("VIDEO_BODY_TOO_LARGE", "EVREN video gövdesi çok büyük")
    mapping = "\n".join(
        f"{frame.frame_id}: klip {frame.timestamp - start:.3f} sn, video {frame.timestamp:.3f} sn"
        for frame in frame_refs
    )
    encoded = _encoded_clip(clip)
    return [
        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{encoded}"}},
        {"type": "text", "text": "Kanıt zaman çizelgesi:\n" + mapping},
    ]

_encoded_cache: dict[int, tuple[int, str]] = {}

def _encoded_clip(clip: bytes) -> str:
    key = id(clip)
    cached = _encoded_cache.get(key)
    if cached is not None and cached[0] == len(clip):
        return cached[1]
    encoded = base64.b64encode(clip).decode()
    _encoded_cache.clear()
    _encoded_cache[key] = (len(clip), encoded)
    return encoded


async def _capture_evidence_frames(
    video: Path,
    frame_refs: list[FrameReference],
    frame_ids: list[str],
    captured_frames: dict[str, tuple[FrameReference, bytes]] | None,
) -> None:
    if captured_frames is None:
        return
    allowed = {frame.frame_id: frame for frame in frame_refs}
    selected = [allowed[frame_id] for frame_id in dict.fromkeys(frame_ids) if frame_id in allowed]
    jpegs = await asyncio.gather(
        *(grab_frame(video, frame.timestamp) for frame in selected)
    )
    captured_frames.update(
        {frame.frame_id: (frame, jpeg) for frame, jpeg in zip(selected, jpegs)}
    )


async def _frame_parts(
    video: Path,
    frame_refs: list[FrameReference],
    *,
    captured_frames: dict[str, tuple[FrameReference, bytes]] | None = None,
    include_timestamps: bool = False,
    frame_width: int = 512,
) -> list[dict[str, Any]]:
    jpegs = await asyncio.gather(
        *(grab_frame(video, frame.timestamp, frame_width) for frame in frame_refs)
    )
    parts: list[dict[str, Any]] = []
    for frame, jpeg in zip(frame_refs, jpegs):
        if captured_frames is not None:
            captured_frames[frame.frame_id] = (frame, jpeg)
        parts.append({
            "type": "text",
            "text": (
                f"FRAME_ID: {frame.frame_id}\n"
                f"VIDEO_TIMESTAMP_SECONDS: {frame.timestamp:.3f}"
                if include_timestamps
                else f"FRAME_ID: {frame.frame_id}"
            ),
        })
        parts.append(_image_part(jpeg))
    return parts


GLANCE_SYSTEM_EN = (
    "You are a surveillance triage filter. You see a few frames from one camera "
    "window. Decide whether the window needs an operator's attention. "
    "Answer with exactly one word: YES or NO."
)

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
    start, end = window
    frame_refs = build_video_references(start, end)
    content = await _video_parts(video, start, end, frame_refs)
    question = GLANCE_QUESTION
    if meta:
        question += f"\n\nDetector data:\n{meta}"
    content.append({"type": "text", "text": question})

    client = main_client()
    resp = await create_chat(client,
        model=settings.video_model,
        messages=[{"role": "system", "content": GLANCE_SYSTEM_EN},
                  {"role": "user", "content": content}],
        max_tokens=2,
        temperature=0,
        logprobs=True,
        top_logprobs=8,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    return _yes_probability(resp)


def _dikkat_probability(resp) -> float | None:
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
    lp = resp.choices[0].logprobs
    if not lp or not lp.content:
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
    effort: str = "",
    stats: dict[str, Any] | None = None,
    timing: dict[str, float | int] | None = None,
    captured_frames: dict[str, tuple[FrameReference, bytes]] | None = None,
    frame_width: int = 512,
) -> WindowReport:
    start, end = window
    frame_refs = build_video_references(start, end)
    request_frame_ids = [frame.frame_id for frame in frame_refs]
    content = await _video_parts(video, start, end, frame_refs)

    task = ((task_prompt or TASK_TR)
            .replace("Yukarıdaki kareler", "Yukarıdaki video")
            .replace("{start}", f"{start:.0f}")
            .replace("{end}", f"{end:.0f}"))
    task += (
        "\n\nKanıt için zaman çizelgesindeki en yakın FRAME_ID değerini seç. "
        "Olay t alanını videonun mutlak saniyesi olarak yaz."
    )
    if meta:
        task += f"\n\nAlgı katmanı verisi:\n{meta}"
    if context:
        task += f"\n\n{context}"
    content.append({"type": "text", "text": task})

    system = system_prompt or SYSTEM_TR
    system = system.replace("aynı zaman penceresinden alınmış kareler", "aynı zaman penceresinin videosu")
    system += "\n\nVideo hareketini ve olayların zamansal sırasını birlikte değerlendir."
    if settings.two_tier:
        system = f"{system}\n\n{tier_prompt or TIER_TR}"
    system += learned_category_block()

    client = main_client()
    started = time.monotonic()
    try:
        resp = await create_chat(client,
            model=model or settings.video_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            max_tokens=settings.interpret_max_tokens,
            temperature=0,
            logprobs=stats is not None,
            top_logprobs=8 if stats is not None else None,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "window_report", "strict": True,
                                "schema": tier_schema(request_frame_ids)
                                          if settings.two_tier
                                          else report_schema(request_frame_ids)},
            },
            extra_body=thinking_extra(think=False, effort="", budget=0),
        )
    finally:
        _record_qwen_timing(timing, started)
    if stats is not None:
        stats.update(call_stats(resp))
        if settings.two_tier and (p := _dikkat_probability(resp)) is not None:
            stats["durum_p"] = p
    raw = resp.choices[0].message.content or "{}"
    report = _to_report(
        start,
        end,
        raw,
        truncated=resp.choices[0].finish_reason == "length",
        frame_refs=frame_refs,
    )
    await _capture_evidence_frames(
        video,
        frame_refs,
        [evidence.frame_id for event in report.events for evidence in event.evidence],
        captured_frames,
    )
    return report


def _record_qwen_timing(
    timing: dict[str, float | int] | None,
    started: float,
) -> None:
    if timing is None:
        return
    timing["calls"] = int(timing.get("calls", 0)) + 1
    timing["total_ms"] = float(timing.get("total_ms", 0.0)) + max(
        0.0,
        (time.monotonic() - started) * 1000.0,
    )
