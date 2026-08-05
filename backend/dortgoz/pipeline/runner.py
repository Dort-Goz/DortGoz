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
from ..agent.memory import RISK_ORDER, Ledger
from ..config import settings
from ..events import AgentStep, Event, RunStatus, WindowReport
from ..ws import ConnectionManager
from . import ingest, windowing
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

        wins = windowing.windows(duration, settings.window_seconds)
        for idx, (start, end) in enumerate(wins):
            peak = windowing.window_motion(profile, start, end)
            if peak < gate:
                # Sert eleme yalnız burada: hareketsiz pencere VLM'e hiç gitmez
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{start:.0f}-{end:.0f} sn atlandı (etkinlik {peak:.4f} < {gate:.4f})",
                ))
                # Ölü pencere süregelen olayı da sonlandırır (olaysız pencere = kapanış)
                for update in ledger.ingest(WindowReport(
                        window_start=start, window_end=end, summary="")):
                    await rec.emit(update)
            else:
                await rec.emit(AgentStep(
                    node="interpret", status="start",
                    detail=f"{start:.0f}-{end:.0f} sn (etkinlik {peak:.3f})",
                ))
                keyframes = windowing.select_keyframes(
                    profile, start, end, settings.keyframes_per_window
                )
                try:
                    report = await interpret_window(
                        path, (start, end), keyframes,
                        model=model, system_prompt=system_prompt,
                        task_prompt=task_prompt,
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
                ctx.reports.append(report)
                await rec.emit(report)
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{len(report.events)} olay",
                ))

                # Defter: ciddi olayları yaşam döngüsüyle olaya dönüştürür
                await rec.emit(AgentStep(node="ledger", status="start"))
                serious = ledger.serious(report)
                thumb = None
                if serious and ledger.open_incident is None:
                    # Şiddet sıralaması RISK_ORDER'dan gelir — sözcük sırası değil
                    peak = max(serious, key=lambda e: RISK_ORDER.index(e.severity_hint))
                    thumb = await save_thumbnail(path, peak.t, run_id,
                                                 f"{int(start)}")
                for update in ledger.ingest(report, thumb):
                    await rec.emit(update)
                await rec.emit(AgentStep(
                    node="ledger", status="end",
                    detail=f"{len(ledger.incidents)} olay defterde",
                ))
            await rec.emit(RunStatus(
                run_id=run_id, state="processing", video=video,
                progress=(idx + 1) / len(wins),
            ))

        for update in ledger.finalize():       # video biterken açık kalan olayı kapat
            await rec.emit(update)
        ctx.finished = True
        # Koşunun nihai kararı operatöre görünür olmalı — sınıf + risk tek satırda
        await rec.emit(RunStatus(run_id=run_id, state="done", progress=1.0,
                                 video=video, detail=ctx.verdict()))
    except asyncio.CancelledError:
        await rec.emit(RunStatus(run_id=run_id, state="idle", video=video,
                                 detail="operatör durdurdu"))
        raise
    except Exception as exc:                       # hattın hatası operatöre görünür olmalı
        await rec.emit(AgentStep(node="interpret", status="error", detail=str(exc)[:300]))
        await rec.emit(RunStatus(run_id=run_id, state="error", video=video,
                                 detail=str(exc)[:300]))
    finally:
        rec.close()


def load_run(run_id: str) -> list[dict]:
    """Kayıtlı koşuyu JSONL'den okur (`/api/runs/{run_id}`)."""
    path = settings.runs_dir / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["run_video", "resolve_media", "load_run", "RunRecorder", "WindowReport"]
