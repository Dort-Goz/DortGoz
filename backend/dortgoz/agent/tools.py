from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import settings
from ..events import Event, ToolCall, UICommand
from ..services.action_dispatcher import ACTION_SPECS
from ..services.action_dispatcher import dispatcher as action_dispatcher
from ..ws import ConnectionManager

EVIDENCE_DIR = "_evidence"


def _tool(name: str, desc: str, props: dict[str, dict]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            },
        },
    }


_GEREKCE = {"type": "string",
            "description": "Bu aracı neden kullandığının tek cümlelik gerekçesi (Türkçe)"}

TOOLS: list[dict] = [
    _tool("videoya_git",
          "Video oynatıcıyı belirtilen saniyeye götürür. Operatöre bir anı "
          "GÖSTERMEK istediğinde kullan.",
          {"t": {"type": "number", "description": "video zamanı (saniye)"},
           "gerekce": _GEREKCE}),
    _tool("olayi_vurgula",
          "Zaman çizelgesinde bir olay kartını vurgular. Bir olaydan söz "
          "ederken operatörün onu görmesi için kullan.",
          {"incident_id": {"type": "string", "description": "olay defterindeki kimlik"},
           "gerekce": _GEREKCE}),
    _tool("pencere_sorgula",
          "Belirtilen saniyeyi kapsayan analiz penceresinin TAM raporunu getirir "
          "(özet, olaylar, belirsizlikler). Defterde olmayan ayrıntı sorulunca "
          "UYDURMAK yerine bunu kullan.",
          {"t": {"type": "number", "description": "video zamanı (saniye)"},
           "gerekce": _GEREKCE}),
    _tool("yeniden_incele",
          "Belirtilen anın çevresini (±15 sn) daha yoğun karelerle ve derin "
          "akıl yürütmeyle YENİDEN inceler. Kayıttaki rapor yetersiz ya da "
          "çelişkiliyse kullan — maliyetlidir, gerektiğinde başvur.",
          {"t": {"type": "number", "description": "video zamanı (saniye)"},
           "gerekce": _GEREKCE}),
    _tool("kanit_klibi_olustur",
          "Belirtilen aralığı ayrı bir kanıt klibi olarak keser ve arşivler. "
          "Operatör kanıt/raporlama istediğinde kullan.",
          {"start": {"type": "number", "description": "başlangıç (saniye)"},
           "end": {"type": "number", "description": "bitiş (saniye)"},
           "gerekce": _GEREKCE}),
    _tool("olay_raporu_olustur",
          "Tamamlanmış gerçek analizden yerel olay raporu ve kanıt paketi oluşturur.",
          {"incident_id": {"type": "string", "description": "olay kimliği"},
           "feed": {"type": "string", "description": "kamera adı; ana kayıt için boş dize"},
           "gerekce": _GEREKCE}),
    _tool("emniyet_bildirimi_hazirla",
          "Kanıtlı suç olayı için emniyet bildirimi taslağını operatör onayına sunar. "
          "Dış kuruma gönderim yapmaz.",
          {"incident_id": {"type": "string", "description": "olay kimliği"},
           "feed": {"type": "string", "description": "kamera adı; ana kayıt için boş dize"},
           "gerekce": _GEREKCE}),
    _tool("acil_saglik_bildirimi_hazirla",
          "Kanıtlı yüksek riskli olay için acil sağlık bildirimi taslağını operatör "
          "onayına sunar. Dış kuruma gönderim yapmaz.",
          {"incident_id": {"type": "string", "description": "olay kimliği"},
           "feed": {"type": "string", "description": "kamera adı; ana kayıt için boş dize"},
           "gerekce": _GEREKCE}),
    _tool("guvenlik_uyarisi_hazirla",
          "Kanıtlı olay için yerel güvenlik uyarısı taslağını operatör onayına sunar. "
          "Fiziksel alarm çalıştırmaz.",
          {"incident_id": {"type": "string", "description": "olay kimliği"},
           "feed": {"type": "string", "description": "kamera adı; ana kayıt için boş dize"},
           "gerekce": _GEREKCE}),
    _tool("alan_guvenligi_iste",
          "Kanıtlı yüksek riskli olay için alan güvenliği talebini operatör onayına "
          "sunar. Fiziksel saha işlemi yapmaz.",
          {"incident_id": {"type": "string", "description": "olay kimliği"},
           "feed": {"type": "string", "description": "kamera adı; ana kayıt için boş dize"},
           "gerekce": _GEREKCE}),
    _tool("aksiyon_durumunu_sorgula",
          "Daha önce operatör onayına sunulan aksiyon taslağının durumunu getirir.",
          {"request_id": {"type": "string", "description": "aksiyon istek kimliği"},
           "gerekce": _GEREKCE}),
]


async def execute(name: str, args: dict[str, Any], manager: ConnectionManager) -> str:
    gerekce = str(args.get("gerekce", ""))
    try:
        result = await _dispatch(name, args, manager)
    except Exception as exc:
        result = f"HATA: {exc}"
    await manager.broadcast(Event.wrap(ToolCall(
        tool=name, args={k: v for k, v in args.items() if k != "gerekce"},
        rationale=gerekce, result=result[:300],
    )))
    return result


async def _dispatch(name: str, args: dict[str, Any], manager: ConnectionManager) -> str:
    from .. import session
    ctx = session.current()

    if name == "videoya_git":
        t = float(args["t"])
        await manager.broadcast(Event.wrap(
            UICommand(action="seek_video", args={"t": t})))
        return f"Oynatıcı {t:.0f}. saniyeye alındı."

    if name == "olayi_vurgula":
        iid = str(args["incident_id"])
        if ctx and iid not in ctx.ledger.incidents:
            known = ", ".join(ctx.ledger.incidents) or "—"
            return f"HATA: '{iid}' defterde yok. Mevcut kimlikler: {known}"
        await manager.broadcast(Event.wrap(
            UICommand(action="highlight_incident", args={"incident_id": iid})))
        return f"{iid} olayı vurgulandı."

    if name == "pencere_sorgula":
        if ctx is None:
            return "HATA: çözümlenmiş kayıt yok."
        t = float(args["t"])
        hits = [r for r in ctx.reports if r.window_start <= t < r.window_end]
        if not hits:
            return (f"{t:.0f}. saniyeyi kapsayan pencere yok "
                    f"(kayıt {ctx.duration:.0f} sn).")
        out = []
        for r in hits:
            lines = [f"Pencere {r.window_start:.0f}-{r.window_end:.0f} sn "
                     f"(sınıf: {r.anomaly_type}): {r.summary}"]
            lines += [f"- t={e.t:.0f}s [{e.severity_hint}] {e.desc}" for e in r.events]
            lines += [f"? belirsiz: {u}" for u in r.uncertainties]
            out.append("\n".join(lines))
        return "\n\n".join(out)

    if name == "yeniden_incele":
        if ctx is None:
            return "HATA: çözümlenmiş kayıt yok."
        return await _reexamine(ctx, float(args["t"]))

    if name == "kanit_klibi_olustur":
        if ctx is None:
            return "HATA: çözümlenmiş kayıt yok."
        return await _evidence_clip(ctx, float(args["start"]), float(args["end"]))

    if name == "olay_raporu_olustur":
        _, url = await action_dispatcher.create_report(
            str(args.get("feed", "")), str(args["incident_id"])
        )
        return f"Gerçek analiz raporu hazır: {url}"

    if name in ACTION_SPECS:
        request, created = action_dispatcher.request(
            name,
            str(args["incident_id"]),
            str(args.get("feed", "")),
            str(args.get("gerekce", "")),
        )
        if created:
            await manager.broadcast(Event.wrap(request, feed=request.feed))
            return (
                f"{request.action_label} operatöre sunuldu "
                f"(request_id={request.request_id}). Henüz hazırlanmadı ve dış kuruma "
                "gönderilmedi."
            )
        return (
            f"{request.action_label} zaten operatör kararı bekliyor "
            f"(request_id={request.request_id})."
        )

    if name == "aksiyon_durumunu_sorgula":
        return action_dispatcher.status_text(str(args["request_id"]))

    return f"HATA: bilinmeyen araç '{name}'"


async def _reexamine(ctx, t: float) -> str:
    from ..pipeline.interpret import interpret_window
    from ..pipeline.runner import resolve_media

    video = resolve_media(ctx.video)
    start = max(0.0, t - 15.0)
    end = min(ctx.duration or t + 15.0, t + 15.0)
    if end - start < 2.0:
        return "HATA: aralık çok kısa."
    n = 8
    keys = [start + (end - start) * i / (n - 1) for i in range(n)]
    report = await interpret_window(video, (start, end), keys, think=True)
    lines = [f"Yeniden inceleme {start:.0f}-{end:.0f} sn: {report.summary}"]
    lines += [f"- t={e.t:.0f}s [{e.severity_hint}] {e.desc}" for e in report.events]
    lines += [f"? belirsiz: {u}" for u in report.uncertainties]
    return "\n".join(lines)


async def _evidence_clip(ctx, start: float, end: float) -> str:
    from ..pipeline.runner import resolve_media

    if end <= start:
        return "HATA: bitiş başlangıçtan önce."
    video = resolve_media(ctx.video)
    out_dir = settings.media_dir / EVIDENCE_DIR / ctx.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"kanit_{start:.0f}_{end:.0f}.mp4"
    out = out_dir / name
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
        "-i", str(video), "-c", "copy", "-y", str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not out.is_file():
        return f"HATA: klip kesilemedi: {err.decode()[-200:]}"
    url = f"/media/{EVIDENCE_DIR}/{ctx.run_id}/{name}"
    return f"Kanıt klibi hazır: {url} ({end - start:.0f} sn)."


def tool_names() -> list[str]:
    return [t["function"]["name"] for t in TOOLS]


def parse_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}
