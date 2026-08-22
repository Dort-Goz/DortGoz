from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from .. import session
from ..agent.llm import context_size
from ..agent.memory import RISK_ORDER, Ledger
from ..config import settings
from ..domain.taxonomy import (
    CanonicalEventType,
    canonical_event_type_from_ws_label,
    legacy_ws_label_from_canonical,
)
from ..events import (
    AgentStep,
    Event,
    ReviewSample,
    RunStatus,
    WindowEvent,
    WindowReport,
    WindowSignals,
)
from ..services import escalation_policy, exemplar_bank
from ..services.runtime_metrics import CanonicalRunMetrics
from ..services.runtime_policy import decide_runtime_policy
from ..services.runtime_postprocess import RuntimeEvidenceScope, postprocess_finalized_report
from ..services.weight_guard import guard as weight_guard
from ..ws import ConnectionManager
from . import ingest, interpret, perception, windowing
from .candidate_intervals import IntervalConfig, build_candidate_intervals
from .candidate_model import MotionBaselineModel
from .interpret import SYSTEM_TR, SYSTEM_TR_IKINCI, TASK_TR, interpret_window

THUMB_DIR = "_thumbs"
EVIDENCE_DIR = "_evidence"
EVIDENCE_PAD = 4.0
LOGGER = logging.getLogger(__name__)


async def save_thumbnail(video: Path, t: float, run_id: str, name: str) -> str | None:
    try:
        jpeg = await ingest.grab_frame(video, t, width=320)
    except ingest.FFmpegError:
        return None
    out = settings.media_dir / THUMB_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.jpg").write_bytes(jpeg)
    return f"/media/{THUMB_DIR}/{run_id}/{name}.jpg"


def peak_embedding(start: float, end: float,
                   screen_samples: list) -> list[float] | None:
    peak = None
    for sample in screen_samples:
        if start <= sample.timestamp < end:
            if peak is None or sample.anomaly_score > peak.anomaly_score:
                peak = sample
    return getattr(peak, "embedding", None) if peak else None


def _sample_this_window() -> bool:
    rate = settings.shadow_sample_rate
    return rate > 0 and random.random() < rate


async def save_evidence_clip(video: Path, start: float, end: float,
                            run_id: str, name: str) -> str | None:
    if end <= start:
        return None
    out = settings.media_dir / EVIDENCE_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    clip = out / f"{name}.mp4"
    begin = max(0.0, start - EVIDENCE_PAD)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-ss", f"{begin:.2f}", "-to", f"{end + EVIDENCE_PAD:.2f}",
        "-i", str(video), "-c", "copy", "-y", str(clip),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not clip.is_file():
        LOGGER.warning("kanıt klibi kesilemedi (%s): %s", run_id, err.decode()[-200:])
        return None
    return f"/media/{EVIDENCE_DIR}/{run_id}/{name}.mp4"


def resolve_media(video: str) -> Path:
    root = settings.media_dir.resolve()
    path = (root / video.lstrip("/")).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"medya kökü dışında: {video}")
    if not path.is_file():
        raise FileNotFoundError(f"video bulunamadı: {video}")
    return path


def screening_covers(start: float, end: float,
                     spans: list[tuple[float, float]]) -> bool:
    return any(a < end and b > start for a, b in spans)


def window_signals(start: float, end: float, screen_samples: list,
                   profile: list, call: dict) -> WindowSignals:
    peak = None
    for sample in screen_samples:
        if start <= sample.timestamp < end:
            if peak is None or sample.anomaly_score > peak.anomaly_score:
                peak = sample
    motion = [m for m in profile if start <= m.t < end]
    durum_p = call.get("durum_p")
    return WindowSignals(
        durum_p=float(durum_p) if durum_p is not None else None,
        anomaly_score=peak.anomaly_score if peak else None,
        interaction_score=peak.interaction_score if peak else None,
        fall_score=peak.fall_score if peak else None,
        fire_smoke_score=peak.fire_smoke_score if peak else None,
        vehicle_conflict_score=peak.vehicle_conflict_score if peak else None,
        tampering_score=peak.tampering_score if peak else None,
        image_quality=peak.image_quality if peak else None,
        changed=max((m.changed for m in motion), default=None),
        fg=max((m.fg for m in motion), default=None),
        mad=max((m.mad for m in motion), default=None),
        screening_model=peak.source_model if peak else "",
    )


class RunRecorder:

    def __init__(
        self,
        manager: ConnectionManager,
        run_id: str,
        metrics: CanonicalRunMetrics,
        feed: str = "",
    ) -> None:
        self.manager = manager
        self.feed = feed
        self.metrics = metrics
        self.path = settings.runs_dir / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._metrics_written = False

    async def emit(self, payload) -> None:
        event = Event.wrap(payload, feed=self.feed)
        await self.manager.broadcast(event)
        self._fh.write(event.model_dump_json() + "\n")
        self._fh.flush()
        self.metrics.observe_emitted(payload)

    def record_metrics(self) -> None:

        if self._metrics_written:
            return
        envelope = {
            "seq": 0,
            "ts": time.time(),
            "feed": self.feed,
            "payload": self.metrics.to_payload(),
        }
        self._fh.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._fh.flush()
        self._metrics_written = True

    def close(self) -> None:
        self._fh.close()


def perf_text(call: dict, n_ctx: int | None) -> str:
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


async def review_if_closed(
    rec: RunRecorder,
    ledger: Ledger,
    path: Path,
    profile: list[float],
    update,
    model: str,
    *,
    evidence_scope: RuntimeEvidenceScope,
    video_duration: float,
    window_count: int,
    metrics: CanonicalRunMetrics | None = None,
) -> None:
    if not settings.incident_review or update.phase != "sonuclandi":
        return
    inc = ledger.incidents.get(update.incident_id)
    if inc is None:
        return
    span = max(0.0, inc.last_seen - inc.first_seen)
    end = min(inc.last_seen + 5.0, video_duration) if video_duration > 0 \
        else inc.last_seen + 5.0
    start = max(0.0, min(inc.first_seen - 5.0, end - 1.0))
    frames = min(16, max(8, int(span // 12)))
    await rec.emit(AgentStep(node="oversight", status="start",
                             detail=f"olay geneli 2. geçiş {start:.0f}-{end:.0f} sn "
                                    f"({frames} kare)"))
    try:
        keyframes = windowing.select_keyframes(profile, start, end, frames)
        if metrics is not None:
            metrics.keyframes_selected_total += len(keyframes)
        call: dict = {}
        qwen_timing: dict[str, float | int] = {}
        captured_frames = {}
        try:
            with metrics.second_pass_call() if metrics is not None else nullcontext():
                review = await interpret.review_incident(
                    path,
                    (start, end),
                    keyframes,
                    inc.notes,
                    model=model,
                    stats=call,
                    timing=qwen_timing,
                    captured_frames=captured_frames,
                    current_type=canonical_event_type_from_ws_label(
                        inc.anomaly_type
                    ).value,
                    strict_class_rules=inc.anomaly_type in {
                        s.strip() for s in
                        settings.adjudicate_confusable.split(",") if s.strip()
                    },
                )
        finally:
            if metrics is not None:
                metrics.record_qwen_timing(qwen_timing)
        event_type = CanonicalEventType(review["event_type"])
        review_report = WindowReport(
            window_start=start,
            window_end=end,
            anomaly_type=legacy_ws_label_from_canonical(event_type).value,
            summary=review["zirve"],
            events=[
                WindowEvent(
                    t=review["zirve_t"],
                    desc=review["zirve"],
                    evidence=review["evidence"],
                    severity_hint=review["risk"],
                    event_type=event_type,
                )
            ],
            uncertainties=review["belirsizlikler"],
        )
        incident_index = list(ledger.incidents).index(update.incident_id)
        validation = postprocess_finalized_report(
            report=review_report,
            captured_frames=captured_frames,
            scope=evidence_scope,
            window_index=window_count + incident_index,
            video_duration=video_duration,
            workspace_root=settings.runs_dir.resolve().parent,
            evidence_root=settings.runs_dir / "_runtime_evidence",
        )
        if metrics is not None:
            metrics.record_validation(validation)
        policy = decide_runtime_policy(review_report, validation)
        ledger.require_review(
            f"2. geçiş: {policy.review_reason}",
            incident_id=update.incident_id,
        )
        if policy.ledger_report is None:
            await rec.emit(
                AgentStep(
                    node="oversight",
                    status="error",
                    detail=(
                        "2. geçiş evidence kapısından geçmedi; mevcut incident korundu "
                        f"({policy.risk.level.value if policy.risk else 'undetermined'})"
                    ),
                )
            )
            return

        review_update = dict(review)
        review_update["anomaly_type"] = policy.ledger_report.anomaly_type
        revised = ledger.apply_review(update.incident_id, review_update)
        if revised is not None:
            await rec.emit(revised)
        await rec.emit(
            AgentStep(
                node="oversight",
                status="end",
                detail=f"{event_type.value} provisional olarak bütünlendi; "
                "VLM risk ipucu final risk yapılmadı"
                + perf_text(call, await context_size(model or settings.main_model)),
            )
        )
    except Exception as exc:
        ledger.require_review(
            f"2. geçiş çalıştırılamadı: {type(exc).__name__}",
            incident_id=update.incident_id,
        )
        await rec.emit(AgentStep(node="oversight", status="error", detail=str(exc)[:160]))


def _mode_flags(mode: str) -> tuple[bool, bool, bool]:
    if mode == "genis":
        return True, False, True
    if mode == "temkinli":
        return False, True, False
    return settings.dual_read, False, settings.final_sweep


async def run_video(
    manager: ConnectionManager,
    video: str,
    run_id: str,
    *,
    model: str = "",
    system_prompt: str = "",
    task_prompt: str = "",
    feed: str = "",
    mode: str = "",
    live: bool = False,
) -> None:
    metrics = CanonicalRunMetrics(run_id)
    rec = RunRecorder(manager, run_id, metrics, feed=feed)
    evidence_scope = RuntimeEvidenceScope.create(run_id)
    ctx = session.start(run_id, video, feed=feed, reset_chat=not live)
    ledger = ctx.ledger
    effective_model = model or settings.main_model
    dual_or, confirm_and, sweep_on = _mode_flags(mode)
    if mode == "genis" and not system_prompt:
        system_prompt = interpret.SYSTEM_TR_GENIS
    (settings.runs_dir / f"{run_id}.meta.json").write_text(json.dumps({
        "video": video,
        "model": effective_model,
        "system_prompt": system_prompt or SYSTEM_TR,
        "task_prompt": task_prompt or TASK_TR,
        "mode": mode or "dengeli",
        "customized": bool(model or system_prompt or task_prompt or mode),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        path = resolve_media(video)

        custom = " · özel istem" if (system_prompt or task_prompt) else ""
        await rec.emit(RunStatus(run_id=run_id, state="processing", video=video,
                                 detail=f"{video} · {effective_model}{custom}"))
        t_wall = time.time()
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

        cand_spans: list[tuple[float, float]] | None = None
        screen_samples: list = []
        if settings.candidate_screening and not settings.dynamic_windows:
            scorer = MotionBaselineModel()
            if settings.candidate_model_manifest:
                try:
                    from .candidate_model import load_candidate_scorer
                    scorer = load_candidate_scorer(Path(settings.candidate_model_manifest))
                except Exception as exc:
                    await rec.emit(AgentStep(
                        node="perceive", status="error",
                        detail=f"aday model yüklenemedi, baseline'a dönüldü: {str(exc)[:80]}"))
            if hasattr(scorer, "score_video"):
                try:
                    with metrics.siglip_call():
                        screen_samples = await asyncio.to_thread(
                            scorer.score_video, profile, path)
                except Exception as exc:
                    await rec.emit(AgentStep(
                        node="perceive", status="error",
                        detail=f"anlamsal screening düştü, baseline'a dönüldü: "
                               f"{str(exc)[:80]}"))
                    scorer = MotionBaselineModel()
                    screen_samples = scorer.score(profile)
            else:
                screen_samples = scorer.score(profile)
            if settings.candidate_adaptive_threshold:
                from .candidate_intervals import adaptive_saturation_shift
                screen_samples = adaptive_saturation_shift(
                    screen_samples,
                    start_threshold=settings.candidate_start_threshold,
                    saturation=settings.candidate_adaptive_saturation,
                    raised_threshold=settings.candidate_adaptive_raised)
            ivs = build_candidate_intervals(
                screen_samples, analysis_id=run_id, video_id=video,
                duration_seconds=duration,
                model_id=getattr(scorer, "model_id",
                                 getattr(getattr(scorer, "artifact", None), "model_id", "?")),
                config=IntervalConfig(
                    start_threshold=settings.candidate_start_threshold,
                    continue_threshold=settings.candidate_continue_threshold,
                    end_patience=settings.candidate_end_patience,
                    merge_gap_seconds=settings.candidate_merge_gap_seconds,
                    min_duration_seconds=settings.candidate_min_duration_seconds,
                    threshold_version=settings.candidate_threshold_version,
                ))
            cand_spans = [(iv.start_time, iv.end_time) for iv in ivs]
            cov = sum(b - a for a, b in cand_spans)
            scorer_id = getattr(scorer, "model_id",
                                getattr(getattr(scorer, "artifact", None), "model_id", "?"))
            await rec.emit(AgentStep(
                node="perceive", status="end",
                detail=f"aday screening ({scorer_id}): {len(cand_spans)} aralık, "
                       f"kapsama %{100 * cov / max(duration, 1e-9):.0f} — aday dışı "
                       f"pencereler dedektör kurtarması hariç atlanacak"))

        n_ctx = await context_size(effective_model)
        det_enabled = settings.detector_enabled
        prev_end = 0.0
        percep_prefetch: dict[int, asyncio.Task] = {}

        async def _scan_window(widx: int):
            ws_, we_ = wins[widx]
            with metrics.dfine_call():
                return await perception.scan_window(
                    path, ws_, we_, settings.detector_samples)

        for idx, (start, end) in enumerate(wins):
            metrics.windows_seen += 1
            metrics.windows_screened += 1
            if settings.dynamic_windows:
                if start - prev_end > 0:
                    for _ in range(settings.incident_grace_windows + 1):
                        for update in ledger.ingest(WindowReport(
                                window_start=prev_end, window_end=start, summary="")):
                            await rec.emit(update)
                            await review_if_closed(
                                rec,
                                ledger,
                                path,
                                profile,
                                update,
                                model,
                                evidence_scope=evidence_scope,
                                video_duration=duration,
                                window_count=len(wins),
                                metrics=metrics,
                            )
                prev_end = end
            peak = windowing.window_motion(profile, start, end)

            percep = None
            if det_enabled:
                try:
                    task = percep_prefetch.pop(idx, None)
                    percep = await (task if task is not None
                                    else _scan_window(idx))
                except FileNotFoundError as exc:
                    det_enabled = False
                    await rec.emit(AgentStep(
                        node="perceive", status="error",
                        detail=f"dedektör kapatıldı: {str(exc)[:120]}"))
                except Exception as exc:
                    await rec.emit(AgentStep(
                        node="perceive", status="error",
                        detail=f"{start:.0f}-{end:.0f} sn algı hatası: {str(exc)[:100]}"))
            if det_enabled and idx + 1 < len(wins) and (idx + 1) not in percep_prefetch:
                percep_prefetch[idx + 1] = asyncio.create_task(_scan_window(idx + 1))

            gated = not settings.dynamic_windows and peak < gate
            screened_out = bool(cand_spans is not None
                                and not screening_covers(start, end, cand_spans))
            rescued = bool((gated or screened_out) and percep and percep.rescue_persons)
            if rescued:
                metrics.dfine_rescue_count += 1
            if (gated or screened_out) and not rescued:
                metrics.windows_skipped_before_vlm += 1
                reason = (f"etkinlik {peak:.4f} < {gate:.4f}" if gated
                          else "aday-aralık dışı (screening)")
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{start:.0f}-{end:.0f} sn atlandı ({reason}"
                           + (", dedektör: insan yok" if percep is not None else "")
                           + ")",
                ))
                for update in ledger.ingest(WindowReport(
                        window_start=start, window_end=end, summary="")):
                    await rec.emit(update)
                    await review_if_closed(
                        rec,
                        ledger,
                        path,
                        profile,
                        update,
                        model,
                        evidence_scope=evidence_scope,
                        video_duration=duration,
                        window_count=len(wins),
                        metrics=metrics,
                    )
            else:
                hint = ledger.continuity_hint() if settings.carry_context else ""
                await rec.emit(AgentStep(
                    node="interpret", status="start",
                    detail=f"{start:.0f}-{end:.0f} sn ({end - start:.0f} sn, "
                           + (f"DEDEKTÖR KURTARDI: {percep.counts.get('person', 0)} kişi, "
                              f"etkinlik {peak:.4f} < eşik" if rescued
                              else f"etkinlik {peak:.3f} ≥ eşik {gate:.4f}")
                           + (" · süregelen olay bağlamı verildi" if hint else ""),
                ))
                keyframes = windowing.select_keyframes(
                    profile, start, end, settings.keyframes_per_window
                )
                metrics.keyframes_selected_total += len(keyframes)
                ingest.prefetch_frames(path, keyframes)
                for nstart, nend in wins[idx + 1:]:
                    if windowing.window_motion(profile, nstart, nend) >= gate:
                        ingest.prefetch_frames(path, windowing.select_keyframes(
                            profile, nstart, nend, settings.keyframes_per_window))
                        break
                call: dict = {}
                qwen_timing: dict[str, float | int] = {}
                captured_frames = {}
                try:
                    report = await interpret_window(
                        path, (start, end), keyframes,
                        meta=percep.meta_text() if percep else "",
                        model=model, system_prompt=system_prompt,
                        task_prompt=task_prompt,
                        context=hint,
                        stats=call,
                        timing=qwen_timing,
                        captured_frames=captured_frames,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await rec.emit(AgentStep(
                        node="interpret", status="error",
                        detail=f"{start:.0f}-{end:.0f} sn atlandı: {str(exc)[:160]}",
                    ))
                    await rec.emit(RunStatus(
                        run_id=run_id, state="processing", video=video,
                        progress=(idx + 1) / len(wins),
                        speed=end / max(time.time() - t_wall, 1e-6),
                    ))
                    continue
                finally:
                    metrics.record_qwen_timing(qwen_timing)

                escalated = ""
                esc_gate = escalation_policy.resolve()
                esc_hit = (esc_gate.value and not report.events
                           and call.get("durum_p", 0.0) >= esc_gate.value)
                if esc_hit and not esc_gate.acts:
                    await rec.emit(AgentStep(
                        node="interpret", status="end",
                        detail=(f"gölge tırmandırma {start:.0f}-{end:.0f} sn: "
                                f"P(dikkat)={call.get('durum_p', 0.0):.4f} "
                                f"≥ {esc_gate.value:.4f} — {esc_gate.detail}"),
                    ))
                if esc_hit and esc_gate.acts:
                    escalation_timing: dict[str, float | int] = {}
                    try:
                        escalation_frames = {}
                        esc_step = (end - start) / 12
                        esc_keyframes = [start + esc_step * (i + 0.5)
                                         for i in range(12)]
                        esc = await interpret_window(
                            path, (start, end), esc_keyframes,
                            meta=percep.meta_text() if percep else "",
                            model=model, system_prompt=system_prompt,
                            task_prompt=task_prompt, context=hint,
                            think=True,
                            timing=escalation_timing,
                            captured_frames=escalation_frames,
                        )
                        if esc.events:
                            report = esc
                            captured_frames = escalation_frames
                            escalated = (f"sınırda pencereden tırmandırmayla "
                                         f"kurtarıldı (P(dikkat)="
                                         f"{call['durum_p']:.2f})")
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"tırmandırma {start:.0f}-{end:.0f} sn: "
                                    f"P(dikkat)={call['durum_p']:.2f} → "
                                    f"{len(esc.events)} olay"),
                        ))
                    except Exception as exc:
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"tırmandırma başarısız, taban karar "
                                    f"korundu: {str(exc)[:120]}"),
                        ))
                    finally:
                        metrics.record_qwen_timing(escalation_timing)

                need_second = ((dual_or and not report.events)
                               or (confirm_and and report.events))
                if need_second:
                    dual_timing: dict[str, float | int] = {}
                    try:
                        kf12 = windowing.select_keyframes(profile, start, end, 12)
                        dual_frames = {}
                        dual = await interpret_window(
                            path, (start, end), kf12,
                            meta=percep.meta_text() if percep else "",
                            model=model, system_prompt=system_prompt,
                            task_prompt=task_prompt, context=hint,
                            timing=dual_timing,
                            captured_frames=dual_frames,
                        )
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"{'doğrulama' if report.events else 'çift'} "
                                    f"okuma {start:.0f}-{end:.0f} sn "
                                    f"(12 kare): {len(dual.events)} olay"),
                        ))
                        if not report.events and dual.events:
                            et = next((e.t for e in dual.events
                                       if start <= e.t <= end), (start + end) / 2)
                            ca, cb = max(start, et - 4.0), min(end, et + 4.0)
                            if cb - ca < 2.0:
                                ca, cb = start, end
                            conf_kf = [ca + (cb - ca) / 8 * (i + 0.5)
                                       for i in range(8)]
                            conf = await interpret_window(
                                path, (start, end), conf_kf,
                                meta=percep.meta_text() if percep else "",
                                model=model, system_prompt=system_prompt,
                                task_prompt=task_prompt, context=hint,
                                timing=dual_timing,
                            )
                            await rec.emit(AgentStep(
                                node="interpret", status="end",
                                detail=(f"çift-okuma teyidi {start:.0f}-{end:.0f}"
                                        f" sn (8 kare): {len(conf.events)} olay"),
                            ))
                            if conf.events:
                                report = dual
                                captured_frames = dual_frames
                        elif confirm_and and report.events and not dual.events:
                            report = WindowReport(
                                window_start=report.window_start,
                                window_end=report.window_end,
                                summary=report.summary,
                                uncertainties=report.uncertainties + [
                                    "temkinli kip: ilk okumanın olayı ikinci "
                                    "(12 kare) okumada doğrulanmadı — alarm "
                                    "düşürüldü"],
                            )
                    except Exception as exc:
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"ikinci okuma başarısız, taban karar "
                                    f"korundu: {str(exc)[:120]}"),
                        ))
                    finally:
                        metrics.record_qwen_timing(dual_timing)

                ikinci_gorus = ""
                if (settings.second_opinion_model and not report.events
                        and peak >= settings.second_opinion_motion):
                    so_timing: dict[str, float | int] = {}
                    try:
                        so_frames = {}
                        so = await interpret_window(
                            path, (start, end), keyframes,
                            meta=percep.meta_text() if percep else "",
                            model=settings.second_opinion_model,
                            effort=settings.second_opinion_effort,
                            system_prompt=system_prompt or SYSTEM_TR_IKINCI,
                            task_prompt=task_prompt, context=hint,
                            timing=so_timing,
                            captured_frames=so_frames,
                        )
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"ikinci görüş {start:.0f}-{end:.0f} sn "
                                    f"({settings.second_opinion_model}): "
                                    f"{len(so.events)} olay"),
                        ))
                        if so.events:
                            report = so
                            captured_frames = so_frames
                            ikinci_gorus = (
                                f"ikinci görüş modeli olay buldu (birincil "
                                f"okuma olaysızdı, hareket {peak:.2f})")
                    except Exception as exc:
                        await rec.emit(AgentStep(
                            node="interpret", status="end",
                            detail=(f"ikinci görüş başarısız, taban karar "
                                    f"korundu: {str(exc)[:120]}"),
                        ))
                    finally:
                        metrics.record_qwen_timing(so_timing)

                validation = postprocess_finalized_report(
                    report=report,
                    captured_frames=captured_frames,
                    scope=evidence_scope,
                    window_index=idx,
                    video_duration=duration,
                    workspace_root=settings.runs_dir.resolve().parent,
                    evidence_root=settings.runs_dir / "_runtime_evidence",
                )
                metrics.record_validation(validation)
                policy = decide_runtime_policy(report, validation)
                ctx.add_report(report)
                await rec.emit(report)
                sev = ",".join(sorted({e.severity_hint for e in report.events})) or "—"
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"{len(report.events)} olay · {report.anomaly_type} · şiddet {sev}"
                           + (f" · {len(report.uncertainties)} belirsizlik"
                              if report.uncertainties else "")
                           + (f" · P(dikkat)={call['durum_p']:.3f}"
                              if "durum_p" in call else "")
                           + perf_text(call, n_ctx),
                ))

                for uyari in weight_guard.drain_alerts():
                    await rec.emit(AgentStep(node="oversight", status="error",
                                             detail=uyari))

                ledger_report = policy.ledger_report
                serious = ledger.serious(ledger_report) if ledger_report is not None else []
                was_open = ledger.open_incident
                thumb = None
                kanit = None
                if serious and was_open is None:
                    peak = max(serious, key=lambda e: RISK_ORDER.index(e.severity_hint))
                    thumb = await save_thumbnail(path, peak.t, run_id,
                                                 f"{int(start)}")
                    kanit = await save_evidence_clip(path, start, end, run_id,
                                                     f"{int(start)}")
                    exemplar_bank.append(
                        settings.runs_dir,
                        exemplar_bank.key_for(run_id, f"{int(start)}"),
                        rec.feed, peak_embedding(start, end, screen_samples))
                if ledger_report is None:
                    ledger.require_review(policy.review_reason)
                    updates = []
                else:
                    review_reasons = [item for item in (escalated, ikinci_gorus, policy.review_reason) if item]
                    updates = ledger.ingest(
                        ledger_report,
                        thumb,
                        uncertain=" · ".join(review_reasons),
                        evidence=kanit,
                    )
                sig = window_signals(start, end, screen_samples, profile, call)
                if not updates and not serious and _sample_this_window():
                    await rec.emit(ReviewSample(
                        sample_id=uuid.uuid4().hex[:8],
                        t=start, window_start=start, window_end=end,
                        summary=(ledger_report.summary if ledger_report else ""),
                        signals=sig,
                        thumbnail=await save_thumbnail(path, start, run_id,
                                                      f"ornek_{int(start)}"),
                        evidence=await save_evidence_clip(path, start, end, run_id,
                                                         f"ornek_{int(start)}"),
                    ))
                    exemplar_bank.append(
                        settings.runs_dir,
                        exemplar_bank.key_for(run_id, f"ornek_{int(start)}"),
                        rec.feed, peak_embedding(start, end, screen_samples))
                for update in updates:
                    update = update.model_copy(update={"signals": sig})
                    await rec.emit(update)
                    await review_if_closed(
                        rec,
                        ledger,
                        path,
                        profile,
                        update,
                        model,
                        evidence_scope=evidence_scope,
                        video_duration=duration,
                        window_count=len(wins),
                        metrics=metrics,
                    )
                if updates:
                    u = updates[-1]
                    note = {"basladi": "olay AÇILDI", "gelisiyor": "olay genişledi",
                            "sonuclandi": "olay KAPANDI"}.get(u.phase, u.phase)
                    detail = (f"{note} #{u.incident_id} · {u.anomaly_type}/{u.risk} "
                              f"({len(serious)} ciddi gözlem)")
                elif was_open is not None:
                    if policy.observation_held:
                        detail = (
                            f"olay #{was_open.incident_id} validation eksikliği "
                            "nedeniyle değiştirilmeden açık tutuldu"
                        )
                    else:
                        detail = (
                            f"olay #{was_open.incident_id} açık tutuldu "
                            f"(tolerans {ledger.quiet_streak}/{ledger.grace})"
                        )
                elif policy.observation_held:
                    detail = (
                        f"{len(policy.held_event_indices)} gözlem validation "
                        "nedeniyle deftere alınmadı"
                    )
                else:
                    detail = ""
                if detail:
                    await rec.emit(AgentStep(node="ledger", status="end", detail=detail))
            await rec.emit(RunStatus(
                run_id=run_id, state="processing", video=video,
                progress=(idx + 1) / len(wins),
                speed=end / max(time.time() - t_wall, 1e-6),
            ))

        if sweep_on and not ledger.incidents and duration >= 10.0:
            sweep_end = max(1.0, duration - 0.4)
            sweep_kf = [sweep_end / 16 * (i + 0.5) for i in range(16)]
            try:
                sweep_frames: dict = {}
                sweep_rep = await interpret_window(
                    path, (0.0, sweep_end), sweep_kf,
                    model=model, system_prompt=system_prompt,
                    task_prompt=task_prompt, captured_frames=sweep_frames,
                )
                await rec.emit(AgentStep(
                    node="interpret", status="end",
                    detail=f"son tarama (16 kare): {len(sweep_rep.events)} olay"))
                if sweep_rep.events:
                    validation = postprocess_finalized_report(
                        report=sweep_rep, captured_frames=sweep_frames,
                        scope=evidence_scope, window_index=len(wins),
                        video_duration=duration,
                        workspace_root=settings.runs_dir.resolve().parent,
                        evidence_root=settings.runs_dir / "_runtime_evidence",
                    )
                    metrics.record_validation(validation)
                    policy = decide_runtime_policy(sweep_rep, validation)
                    ctx.add_report(sweep_rep)
                    await rec.emit(sweep_rep)
                    if policy.ledger_report is not None:
                        serious = ledger.serious(policy.ledger_report)
                        thumb = None
                        if serious:
                            peak = max(serious,
                                       key=lambda e: RISK_ORDER.index(e.severity_hint))
                            thumb = await save_thumbnail(path, peak.t, run_id, "sweep")
                        reasons = [item for item in
                                   ("son taramayla yakalandı", policy.review_reason)
                                   if item]
                        for update in ledger.ingest(policy.ledger_report, thumb,
                                                    uncertain=" · ".join(reasons)):
                            await rec.emit(update)
            except Exception as exc:
                await rec.emit(AgentStep(node="interpret", status="end",
                                         detail=f"son tarama başarısız: {str(exc)[:100]}"))

        for update in ledger.finalize():
            await rec.emit(update)
            await review_if_closed(
                rec,
                ledger,
                path,
                profile,
                update,
                model,
                evidence_scope=evidence_scope,
                video_duration=duration,
                window_count=len(wins),
                metrics=metrics,
            )
        ctx.finished = True
        await rec.emit(RunStatus(run_id=run_id, state="done", progress=1.0,
                                 video=video, detail=ctx.verdict(),
                                 speed=duration / max(time.time() - t_wall, 1e-6)))
    except asyncio.CancelledError:
        await rec.emit(RunStatus(run_id=run_id, state="idle", video=video,
                                 detail="operatör durdurdu"))
        raise
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        await rec.emit(AgentStep(node="interpret", status="error", detail=detail))
        await rec.emit(RunStatus(run_id=run_id, state="error", video=video, detail=detail))
    finally:
        for _t in list(locals().get("percep_prefetch", {}).values()):
            _t.cancel()
        try:
            rec.record_metrics()
        except Exception:
            LOGGER.exception("canonical_run_metrics_write_failed", extra={"run_id": run_id})
        finally:
            rec.close()


def load_run(run_id: str) -> list[dict]:
    path = settings.runs_dir / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


__all__ = ["run_video", "resolve_media", "load_run", "RunRecorder", "WindowReport"]
