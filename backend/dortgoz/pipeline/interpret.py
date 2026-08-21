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
from ..services.weight_guard import guard as weight_guard
from ..tools.protocols import VlmSchemaError
from ..utils import inline_defs
from .ingest import grab_frame
from .thinking import thinking_extra, thinking_on


def learned_category_block() -> str:
    """Onaylanmış kategori ölçütlerini isteme ekler (yoksa boş döner)."""
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
    "iddiası kurma, hukukî hüküm verme. Ancak şüpheli davranış SOMUT gözlemse "
    "raporla: ürünü ödeme yapmadan çantaya/cebe koyma, kasadan izinsiz para "
    "alma gibi davranışlar `possible_theft` kapsamında dikkat gerektirir. "
    "Her event için `event_type` alanında "
    "yalnız canonical şemadaki değerlerden birini kullan. Mümkünse iki ayrı "
    "destekleyici kare, "
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


SYSTEM_TR_GENIS = SYSTEM_TR.replace(
    "raporla: ürünü ödeme yapmadan çantaya/cebe koyma, kasadan izinsiz para "
    "alma gibi davranışlar `possible_theft` kapsamında dikkat gerektirir. ",
    "raporla: ürünü CEBE veya giysi içine GİZLEME, kasadan izinsiz para alma, "
    "ödemeden kasa hattını geçip çıkma `possible_theft` kapsamında dikkat "
    "gerektirir; kasa önünde ödeme sırasında ürünü poşete koymak NORMAL "
    "alışveriştir. ",
)

SYSTEM_TR_IKINCI = (
    "Sen bir güvenlik kamerası görüntü analiz uzmanısın ve İKİNCİ GÖRÜŞ veriyorsun. "
    "Başka bir model bu pencereyi zaten okudu ve dikkat gerektiren bir şey bulmadı, "
    "ama sahnede belirgin hareket var. Görevin ONUN KAÇIRMIŞ OLABİLECEĞİ ince "
    "olayları aramaktır.\n\n"
    "Özellikle şunlara bak: bir ürünün cebe/çantaya/giysi içine GİZLENMESİ; kasadan "
    "veya çekmeceden izinsiz para alınması; yerde hareketsiz yatan kişi; silah benzeri "
    "nesne; kapı/vitrin/kilit zorlanması; duman, alev veya ani parlama; bir kişiye "
    "yönelen fiziksel temas.\n\n"
    "⚠ UZANMAK GİZLEMEK DEĞİLDİR: bir nesneye uzanmak, dokunmak, tutmak veya "
    "incelemek tek başına olay değildir. Ürünün cebe, çantaya veya giysi içine "
    "girdiğini ya da ödenmeden dışarı çıkarıldığını KARELERDE GÖRMEDİYSEN hırsızlık "
    "yazma; gördüklerini `summary`de betimle ve şüpheni `uncertainties`e koy.\n\n"
    "Şunlar NORMAL işleyiştir, olay yazma: tezgâhın veya kasanın ARKASINDAKİ "
    "personelin ürün alması, eğilmesi, çekmece açması; müşterinin ödeme sırasında "
    "ürünü poşete koyması; rafta ürün inceleyip yerine bırakma; kameraya bakmak veya "
    "yaklaşmak; güvenlik görevlisinin rutin devriyesi ve müşteriyle konuşması.\n\n"
    "Yalnızca karelerde GÖRDÜĞÜNÜ yaz; kimlik iddiası kurma, hukukî hüküm verme. "
    "Emin olmadığın çıkarımı `uncertainties` alanına koy. Her olayın `evidence` "
    "alanında yalnız sana verilen FRAME_ID değerlerini kullan. `severity_hint` "
    "ölçeğinde `dusuk` = olağan hareketlilik (ALARM DEĞİL); `orta` ve üstünü yalnız "
    "gerçekten müdahale gerektiren durumlar için kullan. Rapor KISA olsun."
)


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
    raw_t = event.get("t")
    tolerance = 2.0
    if isinstance(raw_t, int | float) and start - tolerance <= raw_t <= end + tolerance:
        return
    evidence = event.get("evidence") or []
    anchored = next(
        (ref["timestamp"] for ref in evidence
         if isinstance(ref, dict) and isinstance(ref.get("timestamp"), int | float)),
        (start + end) / 2,
    )
    event["t"] = float(anchored)


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
    frame_refs = build_frame_references(keyframes)
    if not frame_refs:
        raise VlmEvidenceContractError(
            "NO_REVIEW_FRAMES", "2. geçiş için seçilebilir kare yok"
        )
    content = await _frame_parts(
        video,
        frame_refs,
        captured_frames=captured_frames,
        include_timestamps=True,
    )
    task = (
        f"Yukarıdaki kareler {start:.0f}-{end:.0f} sn arasındaki TEK bir olayın "
        f"tamamını kapsıyor ({end - start:.0f} sn). Olayı bütün olarak değerlendir: "
        "nasıl başladı, zirve anı hangisi, nasıl sonuçlandı."
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
            model=model or settings.main_model,
            messages=[{"role": "system", "content": REVIEW_SYSTEM_TR},
                      {"role": "user", "content": content}],
            max_tokens=settings.interpret_max_tokens,
            temperature=0,
            response_format={"type": "json_schema",
                             "json_schema": {"name": "incident_review", "strict": True,
                                             "schema": review_schema(
                                                 [f.frame_id for f in frame_refs]
                                             )}},
            extra_body={"speculative.n_max": 0,
                        "chat_template_kwargs": {"enable_thinking": False}},
        )
    finally:
        _record_qwen_timing(timing, started)
    if stats is not None:
        stats.update(call_stats(resp))
    raw = resp.choices[0].message.content or "{}"
    weight_guard.record(raw)
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
    return review.model_dump(mode="json")


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
    content = await _frame_parts(video, build_frame_references(keyframes))
    question = GLANCE_QUESTION
    if meta:
        question += f"\n\nDetector data:\n{meta}"
    content.append({"type": "text", "text": question})

    client = main_client()
    resp = await create_chat(client,
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
    eff = effort or settings.interpret_effort
    thinks = thinking_on(think=think, effort=eff)
    frame_refs = build_frame_references(keyframes)
    request_frame_ids = [frame.frame_id for frame in frame_refs]
    content = await _frame_parts(video, frame_refs, captured_frames=captured_frames,
                                 frame_width=frame_width)

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
    system += learned_category_block()

    client = main_client()
    started = time.monotonic()
    try:
        resp = await create_chat(client,
            model=model or settings.main_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": content}],
            max_tokens=(max(4000, settings.interpret_max_tokens) if thinks
                        else settings.interpret_max_tokens),
            temperature=(settings.interpret_think_temp
                         if thinks and settings.interpret_think_temp > 0 else 0),
            logprobs=stats is not None,
            top_logprobs=8 if stats is not None else None,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "window_report", "strict": True,
                                "schema": tier_schema(request_frame_ids)
                                          if settings.two_tier
                                          else report_schema(request_frame_ids)},
            },
            extra_body={
                "speculative.n_max": 0,
                **thinking_extra(think=think, effort=eff,
                                 budget=settings.interpret_think_budget),
            },
        )
    finally:
        _record_qwen_timing(timing, started)
    if stats is not None:
        stats.update(call_stats(resp))
        if settings.two_tier and (p := _dikkat_probability(resp)) is not None:
            stats["durum_p"] = p
    raw = resp.choices[0].message.content or "{}"
    weight_guard.record(raw)
    return _to_report(
        start,
        end,
        raw,
        truncated=resp.choices[0].finish_reason == "length",
        frame_refs=frame_refs,
    )


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
