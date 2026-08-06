"""Koşu orkestrasyonu — bir videoyu uçtan uca işleyip olay akışına yazar.

Hafta 1 hattı:  alım → hareket profili → pencereleme → kare seçimi → tek VLM
çağrısı → şema-geçerli WindowReport. Algı katmanı (hafta 2) araya `meta` olarak
girecek; imzalar bunun için hazır.

Her koşu ayrıca `runs/<run_id>.jsonl` olarak diske yazılır — `/api/runs` bunu
listeler, `ws.replay_jsonl` yeniden oynatabilir (demo ve regresyon için).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .. import session
from ..agent.llm import context_size
from ..agent.memory import RISK_ORDER, Ledger
from ..config import settings
from ..events import AgentStep, Event, RunStatus, WindowReport
from ..ws import ConnectionManager
from . import ingest, interpret, windowing
from .interpret import SYSTEM_TR, TASK_TR, interpret_window

THUMB_DIR = "_thumbs"       # media/ altında; /media mount'u üzerinden servis edilir


async def save_thumbnail(video: Path, t: float, run_id: str, name: str) -> str | None:
    """Olayın tepe anından küçük resim yazar; `/media/...` altında URL döndürür."""
    try:
        jpeg = await ingest.grab_frame(video, t, width=320)
    except ingest.FFmpegError:
        return None
    out = settings.media_dir / THUMB_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.jpg").write_bytes(jpeg)
    return f"/media/{THUMB_DIR}/{run_id}/{name}.jpg"


def resolve_media(video: str) -> Path:
    """`/media` altındaki göreli yolu güvenle çözer (dizin dışına çıkışı reddeder)."""
    root = settings.media_dir.resolve()
    path = (root / video.lstrip("/")).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"medya kökü dışında: {video}")
    if not path.is_file():
        raise FileNotFoundError(f"video bulunamadı: {video}")
    return path


class RunRecorder:
    """Koşu olaylarını hem WS'e yayınlar hem JSONL'e yazar."""

    def __init__(self, manager: ConnectionManager, run_id: str) -> None:
        self.manager = manager
        self.path = settings.runs_dir / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")

    async def emit(self, payload) -> None:
        event = Event.wrap(payload)
        await self.manager.broadcast(event)
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def perf_text(call: dict, n_ctx: int | None) -> str:
    """`… · 742/98304 tok (%0,8) · PP 2843 / gen 130 t/s` — izleme satırının kuyruğu.

    Bağlam doluluğu operatöre "taşmaya ne kadar var" der (uzun olay 2. geçişi ve
    çok kareli istemler burada görünür); PP/gen hızları da yavaşlamayı (ör. GTT
    taşması) koşu sırasında fark ettirir. n_ctx okunamazsa (erişim kapısı /props
    açmaz) yüzde atlanır, token sayıları yine gösterilir.

    ⚠ Buradaki **PP, config.yaml etiketindeki PP ile AYNI ŞEY DEĞİL**: bu ölçüme
    kare KODLAMA süresi de giriyor (6 kare ≈ 1,5 sn sabit ek yük) ve prompt token
    sayısı görece küçük olduğu için oran düşük çıkar (ölçüldü: 65 t/s, etiket
    2013). Regresyon sanma — etiketle kıyaslanacak sayı `benchsuite`in METİN
    ölçümüdür; buradaki değer pencere başına GERÇEK maliyeti gösterir.
    """
    if not call:
        return ""
    pt, ct = call.get("prompt_tokens"), call.get("completion_tokens")
    bits = []
    if pt is not None:
        fill = f" (%{100 * pt / n_ctx:.1f})" if n_ctx else ""
        bits.append(f"{pt}+{ct or 0}/{n_ctx or '?'} tok{fill}")
    pp, gen = call.get("pp_tps"), call.get("gen_tps")
    if pp or gen:
        bits.append(f"PP {pp or 0:.0f} / gen {gen or 0:.0f} t/s")
    return " · " + " · ".join(bits) if bits else ""


async def review_if_closed(rec: "RunRecorder", ledger: Ledger, path: Path,
                           profile: list[float], update, model: str) -> None:
    """Olay KAPANDIĞINDA tüm aralığı tek bağlamda yeniden okur ve kartı düzeltir.

    Sınırlar ancak olay kapanınca bilinir — asıl kazanç bu: 30 sn'lik pencereler
    tespit için ucuz kalır, anlatı ise bütünü gören tek çağrıdan gelir. Yalnız
    ÇOK PENCEREYE yayılmış olaylarda çalışır (tek pencerelik olayda zaten bütün
    görülmüştü) ve maliyeti gerçek olay başına bir çağrıdır.
    """
    if not settings.incident_review or update.phase != "sonuclandi":
        return
    inc = ledger.incidents.get(update.incident_id)
    if inc is None:
        return
    span = max(0.0, inc.last_seen - inc.first_seen)
    if span < settings.window_seconds:      # tek pencerelik olay → ikinci geçiş gereksiz
        return
    start = max(0.0, inc.first_seen - 5.0)
    end = inc.last_seen + 5.0
    frames = min(16, max(8, int(span // 12)))
    await rec.emit(AgentStep(node="oversight", status="start",
                             detail=f"olay geneli 2. geçiş {start:.0f}-{end:.0f} sn "
                                    f"({frames} kare)"))
    try:
        keyframes = windowing.select_keyframes(profile, start, end, frames)
        call: dict = {}
        review = await interpret.review_incident(
            path, (start, end), keyframes, inc.notes, model=model, stats=call)
        revised = ledger.apply_review(update.incident_id, review)
        if revised is not None:
            await rec.emit(revised)
        await rec.emit(AgentStep(
            node="oversight", status="end",
            detail=f"{review.get('anomaly_type','?')} / {review.get('risk','?')} "
                   f"olarak bütünlendi" + perf_text(call, await context_size(
                       model or settings.main_model))))
    except Exception as exc:                 # 2. geçiş bir EK'tir, koşuyu düşürmez
        await rec.emit(AgentStep(node="oversight", status="error",
                                 detail=str(exc)[:160]))


async def run_video(
    manager: ConnectionManager,
    video: str,
    run_id: str,
    *,
    model: str = "",
    system_prompt: str = "",
    task_prompt: str = "",
) -> None:
    """Bir videoyu işler; iptal edilirse (stop_run) durumu temiz bırakır.

    Deney seçenekleri (model/istemler) boşsa varsayılan; her koşunun etkin
    yapılandırması `runs/<id>.meta.json`'a yazılır — hangi istem hangi çıktıyı
    üretti sorusu (ablation/kanıt disiplini) her zaman cevaplanabilir kalır.
    """
    rec = RunRecorder(manager, run_id)
    ctx = session.start(run_id, video)      # sohbet analiz sonrası buradan devam eder
    ledger = ctx.ledger
    effective_model = model or settings.main_model
    (settings.runs_dir / f"{run_id}.meta.json").write_text(json.dumps({
        "video": video,
        "model": effective_model,
        "system_prompt": system_prompt or SYSTEM_TR,
        "task_prompt": task_prompt or TASK_TR,
        "customized": bool(model or system_prompt or task_prompt),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        path = resolve_media(video)

        custom = " · özel istem" if (system_prompt or task_prompt) else ""
        await rec.emit(RunStatus(run_id=run_id, state="processing", video=video,
                                 detail=f"{video} · {effective_model}{custom}"))
        await rec.emit(AgentStep(node="perceive", status="start", detail="hareket profili"))
        duration = await ingest.probe_duration(path)
        ctx.duration = duration
        profile = await ingest.motion_profile(path, settings.base_fps)
        gate = (ingest.adaptive_gate(profile, minimum=settings.motion_gate)
                if settings.motion_gate_adaptive else settings.motion_gate)
        await rec.emit(AgentStep(
            node="perceive", status="end",
            detail=f"{duration:.0f} sn, {len(profile)} örnek, "
                   f"taban {ingest.noise_floor(profile):.4f} → eşik {gate:.4f}",
        ))

        if settings.dynamic_windows:
            # Pencereler ETKİNLİĞE hizalanır: ölü bölge hiç pencere olmaz,
            # pencere olayın başladığı yerde açılır (sabit ızgara olayı bölüyordu)
            wins = windowing.activity_windows(
                profile, duration, gate,
                min_len=settings.window_min_seconds,
                max_len=settings.window_seconds,
                preroll=settings.window_preroll,
                quiet_tail=settings.window_quiet_tail,
            )
            await rec.emit(AgentStep(
                node="perceive", status="end",
                detail=f"etkinliğe hizalı {len(wins)} pencere "
                       f"({sum(b - a for a, b in wins):.0f} sn işlenecek, "
                       f"{duration - sum(b - a for a, b in wins):.0f} sn ölü bölge atlanıyor)",
            ))
        else:
            wins = windowing.windows(duration, settings.window_seconds)

        n_ctx = await context_size(effective_model)   # bağlam doluluğu için (bir kez)
        prev_end = 0.0
        for idx, (start, end) in enumerate(wins):
            if settings.dynamic_windows:
                # Pencereler arası boşluk = sürekli sessizlik → defter olayı kapatmalı
                if start - prev_end > 0:
                    for _ in range(settings.incident_grace_windows + 1):
                        for update in ledger.ingest(WindowReport(
                                window_start=prev_end, window_end=start, summary="")):
                            await rec.emit(update)
                            await review_if_closed(rec, ledger, path, profile, update, model)
                prev_end = end
            peak = windowing.window_motion(profile, start, end)
            if not settings.dynamic_windows and peak < gate:
                # Sert eleme yalnız burada: hareketsiz pencere VLM'e hiç gitmez
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{start:.0f}-{end:.0f} sn atlandı (etkinlik {peak:.4f} < {gate:.4f})",
                ))
                # Ölü pencere süregelen olayı da sonlandırır (olaysız pencere = kapanış)
                for update in ledger.ingest(WindowReport(
                        window_start=start, window_end=end, summary="")):
                    await rec.emit(update)
                    # Olay burada da kapanabilir → 2. geçiş bu yolda da çalışmalı
                    await review_if_closed(rec, ledger, path, profile, update, model)
            else:
                hint = ledger.continuity_hint() if settings.carry_context else ""
                await rec.emit(AgentStep(
                    node="interpret", status="start",
                    detail=f"{start:.0f}-{end:.0f} sn ({end - start:.0f} sn, "
                           f"etkinlik {peak:.3f} ≥ eşik {gate:.4f})"
                           + (" · süregelen olay bağlamı verildi" if hint else ""),
                ))
                keyframes = windowing.select_keyframes(
                    profile, start, end, settings.keyframes_per_window
                )
                call: dict = {}
                try:
                    report = await interpret_window(
                        path, (start, end), keyframes,
                        model=model, system_prompt=system_prompt,
                        task_prompt=task_prompt,
                        # Süregelen olayın bağlamı bir sonraki pencereye taşınır
                        context=hint,
                        stats=call,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # TEK pencere hatası koşuyu ÖLDÜRMEZ: saatlik kayıtlarda
                    # 19. dakikadaki bir ayrıştırma hatası tüm analizi çöpe
                    # atıyordu (2026-08-05). Pencere atlanır, koşu sürer.
                    await rec.emit(AgentStep(
                        node="interpret", status="error",
                        detail=f"{start:.0f}-{end:.0f} sn atlandı: {str(exc)[:160]}",
                    ))
                    await rec.emit(RunStatus(
                        run_id=run_id, state="processing", video=video,
                        progress=(idx + 1) / len(wins),
                    ))
                    continue

                # Sınırda kalan pencere: karar `olagan` ama modelin ham inancı
                # dikkat dalında kayda değer kütle bırakmış → BİR düşünmeli
                # yeniden sorgu. Taban karar ASLA kaybolmaz — düşünme, token
                # bütçesini yiyip JSON üretmeden bitebiliyor (2026-08-06 ölçümü).
                if (settings.escalate_p and not report.events
                        and call.get("durum_p", 0.0) >= settings.escalate_p):
                    try:
                        esc = await interpret_window(
                            path, (start, end), keyframes,
                            model=model, system_prompt=system_prompt,
                            task_prompt=task_prompt, context=hint,
                            think=True,
                        )
                        if esc.events:
                            report = esc
                        await rec.emit(AgentStep(
                            node="interpret", status="done",
                            detail=(f"tırmandırma {start:.0f}-{end:.0f} sn: "
                                    f"P(dikkat)={call['durum_p']:.2f} → "
                                    f"{len(esc.events)} olay"),
                        ))
                    except Exception as exc:
                        await rec.emit(AgentStep(
                            node="interpret", status="done",
                            detail=(f"tırmandırma başarısız, taban karar "
                                    f"korundu: {str(exc)[:120]}"),
                        ))

                ctx.reports.append(report)
                await rec.emit(report)
                sev = ",".join(sorted({e.severity_hint for e in report.events})) or "—"
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{len(report.events)} olay · {report.anomaly_type} · şiddet {sev}"
                           + (f" · {len(report.uncertainties)} belirsizlik"
                              if report.uncertainties else "")
                           + perf_text(call, n_ctx),
                ))

                # Defter: ciddi olayları yaşam döngüsüyle olaya dönüştürür.
                # İzleme satırı NE DEĞİŞTİĞİNİ yazar (eski "N olay defterde"
                # sayacı hata ayıklamada işe yaramıyordu): açıldı/genişledi/
                # kapandı + tolerans sayacı, yani defterin kararı görünür olur.
                serious = ledger.serious(report)
                was_open = ledger.open_incident
                thumb = None
                if serious and was_open is None:
                    # Şiddet sıralaması RISK_ORDER'dan gelir — sözcük sırası değil
                    peak = max(serious, key=lambda e: RISK_ORDER.index(e.severity_hint))
                    thumb = await save_thumbnail(path, peak.t, run_id,
                                                 f"{int(start)}")
                updates = ledger.ingest(report, thumb)
                for update in updates:
                    await rec.emit(update)
                    await review_if_closed(rec, ledger, path, profile, update, model)
                if updates:
                    u = updates[-1]
                    note = {"basladi": "olay AÇILDI", "gelisiyor": "olay genişledi",
                            "sonuclandi": "olay KAPANDI"}.get(u.phase, u.phase)
                    detail = (f"{note} #{u.incident_id} · {u.anomaly_type}/{u.risk} "
                              f"({len(serious)} ciddi gözlem)")
                elif was_open is not None:
                    # Sessiz pencere ama olay tolerans sayesinde açık kaldı
                    detail = (f"olay #{was_open.incident_id} açık tutuldu "
                              f"(tolerans {ledger.quiet_streak}/{ledger.grace})")
                else:
                    detail = ""            # anlatacak bir şey yok → satır üretme
                if detail:
                    await rec.emit(AgentStep(node="ledger", status="end", detail=detail))
            await rec.emit(RunStatus(
                run_id=run_id, state="processing", video=video,
                progress=(idx + 1) / len(wins),
            ))

        for update in ledger.finalize():       # video biterken açık kalan olayı kapat
            await rec.emit(update)
            await review_if_closed(rec, ledger, path, profile, update, model)
        ctx.finished = True
        # Koşunun nihai kararı operatöre görünür olmalı — sınıf + risk tek satırda
        await rec.emit(RunStatus(run_id=run_id, state="done", progress=1.0,
                                 video=video, detail=ctx.verdict()))
    except asyncio.CancelledError:
        await rec.emit(RunStatus(run_id=run_id, state="idle", video=video,
                                 detail="operatör durdurdu"))
        raise
    except Exception as exc:                       # hattın hatası operatöre görünür olmalı
        # Hata metni exception TÜRÜNÜ de taşır (boş mesajlı hatalar görünür kalsın)
        detail = f"{type(exc).__name__}: {exc}"[:300]
        await rec.emit(AgentStep(node="interpret", status="error", detail=detail))
        await rec.emit(RunStatus(run_id=run_id, state="error", video=video, detail=detail))
    finally:
        rec.close()


def load_run(run_id: str) -> list[dict]:
    """Kayıtlı koşuyu JSONL'den okur (`/api/runs/{run_id}`)."""
    path = settings.runs_dir / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["run_video", "resolve_media", "load_run", "RunRecorder", "WindowReport"]
