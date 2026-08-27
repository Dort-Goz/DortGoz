from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..domain.video import VideoMetadata
from ..ws import ConnectionManager
from .execution_coordinator import ExecutionCoordinator, LivePreemptionTimeout
from .run_identity import require_safe_run_id

log = logging.getLogger(__name__)

SEGMENT_GLOB = "seg_*.mp4"
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
PREVIEW_BUFFER_LIMIT = 4 * 1024 * 1024
PREVIEW_IDLE_TIMEOUT = 15.0
PREVIEW_QUEUE_LIMIT = 24
STALL_SEGMENTS = 3
# Boşta yoklama aralığı. Doğrudan uyarı gecikmesine biner: segment kapandıktan
# sonra fark edilmesi ortalama bunun yarısı kadar gecikir. Yoklama tek bir glob
# ve stat'tir, bu yüzden sıklastırmak ölçülebilir bir yük getirmez.
POLL_IDLE_SECONDS = 0.5
PrepareRun = Callable[[str, str, Path], Awaitable[VideoMetadata]]
FinalizeRun = Callable[[str], Awaitable[object]]


@dataclass
class FeedStatus:

    name: str
    url: str
    desc: str = ""
    state: str = "baslatiliyor"
    lag_s: float | None = None
    dropped_s: float = 0.0
    segments_done: int = 0
    last_error: str = ""
    snapshot: str = ""


def load_feeds(path: Path | None = None) -> list[dict]:
    import json

    p = path or settings.live_feeds_path
    if not p.is_file():
        example = p.with_name(p.stem + ".example.json")
        if example.is_file():
            p = example
        else:
            raise FileNotFoundError(f"canlı akış listesi yok: {p}")
    feeds = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(feeds, list) or not feeds:
        raise ValueError("akış listesi boş")
    seen: set[str] = set()
    for f in feeds:
        if not isinstance(f, dict) or not f.get("name") or not f.get("url"):
            raise ValueError(f"geçersiz akış girdisi: {f!r}")
        try:
            name = require_safe_run_id(f["name"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"akış adı geçersiz: {f['name']!r}") from exc
        if name in seen:
            raise ValueError(f"akış adı geçersiz/yinelenmiş: {f['name']}")
        seen.add(name)
    return feeds


def plan_segments(pending: list[Path], max_backlog: int) -> tuple[list[Path], list[Path]]:
    cut = max(0, len(pending) - max(0, max_backlog))
    return pending[:cut], pending[cut:]


def _drop_count(items: list, keep: int) -> int:
    return max(0, len(items) - max(0, keep))


class LiveFeedWorker:
    def __init__(self, name: str, url: str, manager: ConnectionManager,
                 mode: str = "", desc: str = "",
                 prepare_run: PrepareRun | None = None,
                 finalize_run: FinalizeRun | None = None,
                 execution_coordinator: ExecutionCoordinator | None = None,
                 on_preview: Callable[[str, bytes], None] | None = None) -> None:
        self.on_preview = on_preview
        self.status = FeedStatus(name=name, url=url, desc=desc)
        self.manager = manager
        self.mode = mode
        self.prepare_run = prepare_run
        self.finalize_run = finalize_run
        self.execution_coordinator = execution_coordinator
        self.dir = settings.media_dir / "canli" / name
        self.running = False
        self._done: set[str] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_seg_mtime: float | None = None
        self._started_at = time.time()
        self.segments_failed = 0
        self.preview_frame: bytes | None = None
        self.preview_seq = 0
        self._preview_event = asyncio.Event()


    def start(self) -> None:
        self.running = True
        self.dir.mkdir(parents=True, exist_ok=True)
        self._wipe_stale()
        self._tasks = [asyncio.create_task(self._ffmpeg_loop(),
                                           name=f"canli-cek-{self.status.name}"),
                       asyncio.create_task(self._process_loop(),
                                           name=f"canli-isle-{self.status.name}")]

    def _wipe_stale(self) -> None:
        for stale in self.dir.glob(SEGMENT_GLOB):
            stale.unlink(missing_ok=True)

    async def stop(self) -> None:
        self.running = False
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t


    def _ffmpeg_cmd(self) -> list[str]:
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
               "-i", self.status.url, "-an",
               "-map", "0:v:0", "-c:v", "copy",
               "-f", "segment", "-segment_time", str(settings.live_segment_seconds),
               "-reset_timestamps", "1", "-strftime", "1",
               str(self.dir / "seg_%s.mp4")]
        if settings.live_preview:
            cmd += ["-map", "0:v:0", "-c:v", "mjpeg",
                    "-vf", f"fps={settings.live_preview_fps},"
                           f"scale={settings.live_preview_width}:-2",
                    "-q:v", str(settings.live_preview_quality),
                    "-f", "image2pipe", "-"]
        return cmd

    def _publish_preview(self, frame: bytes) -> None:
        self.preview_frame = frame
        self.preview_seq += 1
        waiters, self._preview_event = self._preview_event, asyncio.Event()
        waiters.set()
        if self.on_preview is not None:
            self.on_preview(self.status.name, frame)

    async def _read_preview(self, stream: asyncio.StreamReader) -> None:
        buffer = bytearray()
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return
            buffer.extend(chunk)
            while True:
                end = buffer.find(JPEG_EOI)
                if end == -1:
                    break
                start = buffer.find(JPEG_SOI)
                if start == -1 or start > end:
                    del buffer[: end + 2]
                    continue
                self._publish_preview(bytes(buffer[start : end + 2]))
                del buffer[: end + 2]
            if len(buffer) > PREVIEW_BUFFER_LIMIT:
                del buffer[:-PREVIEW_BUFFER_LIMIT]

    async def preview_frames(self):
        last = -1
        while self.running:
            frame, seq = self.preview_frame, self.preview_seq
            if frame is not None and seq != last:
                last = seq
                yield frame
                continue
            waiter = self._preview_event
            try:
                await asyncio.wait_for(waiter.wait(), timeout=PREVIEW_IDLE_TIMEOUT)
            except TimeoutError:
                if self.preview_frame is None:
                    return

    async def _ffmpeg_loop(self) -> None:
        backoff = 5.0
        try:
            while self.running:
                try:
                    self._proc = await asyncio.create_subprocess_exec(
                        *self._ffmpeg_cmd(),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE)
                    self.status.state = "akiyor"
                    self._started_at = time.time()
                    backoff = 5.0
                    assert self._proc.stdout is not None
                    assert self._proc.stderr is not None
                    reader = asyncio.create_task(
                        self._read_preview(self._proc.stdout),
                        name=f"canli-onizleme-{self.status.name}")
                    try:
                        stderr = await self._drain_stderr(self._proc.stderr)
                        await self._proc.wait()
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
                    if not self.running:
                        return
                    tail = (stderr or b"")[-300:].decode(errors="replace").strip()
                    self.status.state = "hata"
                    self.status.last_error = tail or f"ffmpeg çıktı ({self._proc.returncode})"
                    log.warning("canlı %s: çekici düştü: %s", self.status.name, tail)
                except FileNotFoundError:
                    self.status.state = "hata"
                    self.status.last_error = "ffmpeg bulunamadı"
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        finally:
            if self._proc and self._proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self._proc.kill()


    async def _drain_stderr(self, stream: asyncio.StreamReader) -> bytes:
        tail = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return tail
            tail = (tail + chunk)[-300:]
            self.status.last_error = tail.decode(errors="replace").strip()

    def _closed_segments(self) -> list[Path]:
        segs = sorted(self.dir.glob(SEGMENT_GLOB))
        return [s for s in segs[:-1]
                if s.name not in self._done and s.stat().st_size > 0]

    async def _process_loop(self) -> None:
        while self.running:
            worked = await self._step()
            if not worked:
                await asyncio.sleep(POLL_IDLE_SECONDS)

    async def _step(self) -> bool:
        drop, pending = plan_segments(self._closed_segments(),
                                      settings.live_max_backlog)
        for old in drop:
            self.status.dropped_s += settings.live_segment_seconds
            self._done.add(old.name)
            old.unlink(missing_ok=True)
        if drop:
            log.warning("canlı %s: %d segment atlandı (canlıya yetişme)",
                        self.status.name, len(drop))
        if not pending:
            self._refresh_lag()
            return False

        seg = pending[0]
        seg_mtime = seg.stat().st_mtime
        rel = seg.relative_to(settings.media_dir).as_posix()
        run_id = f"canli-{self.status.name}-{seg.stem.removeprefix('seg_')}"
        self.status.state = "isleniyor"
        live_lease = None
        if self.execution_coordinator is not None:
            try:
                live_lease = await self.execution_coordinator.acquire_live()
            except LivePreemptionTimeout as exc:
                self.status.state = "hata"
                self.status.last_error = str(exc)
                return False
        try:
            from ..pipeline.runner import run_video
            from .triage import store as triage_store

            from ..pipeline.interpret import SYSTEM_TR

            system_prompt = (SYSTEM_TR + self._camera_note()
                             + triage_store.feed_note(self.status.name))
            if self.prepare_run is not None:
                await self.prepare_run(run_id, rel, seg)
            await run_video(self.manager, rel, run_id,
                            feed=self.status.name, mode=self.mode, live=True,
                            system_prompt=system_prompt)
            if self.finalize_run is not None:
                await self.finalize_run(run_id)
            self.status.segments_done += 1
            self.status.last_error = ""
            self._last_seg_mtime = seg_mtime
        except Exception as exc:
            self.segments_failed += 1
            self.status.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("canlı %s: segment işlenemedi: %s", self.status.name, seg.name)
        finally:
            if live_lease is not None:
                await live_lease.release_async()
        self._done.add(seg.name)
        self._refresh_lag()
        await self._snapshot(seg)
        self._prune(seg)
        self.status.state = "akiyor"
        return True

    def _camera_note(self) -> str:
        # Modelin sahneyi doğru yorumlaması için kameranın ne çektiğini bilmesi gerekir:
        # bağlamsız bakan model otoyol trafiğini kaza veya hırsızlık olarak etiketliyor.
        desc = self.status.desc.strip()
        return f"\n\nKAMERA: {desc}\nSahneyi bu kameranın bağlamında yorumla." if desc else ""

    def _idle_seconds(self) -> float:
        newest = max((s.stat().st_mtime for s in self.dir.glob(SEGMENT_GLOB)),
                     default=self._started_at)
        return time.time() - newest

    def _refresh_lag(self) -> None:
        if self._last_seg_mtime is not None:
            self.status.lag_s = round(time.time() - self._last_seg_mtime, 1)
        if self.status.state != "akiyor":
            return
        idle = self._idle_seconds()
        if idle > STALL_SEGMENTS * settings.live_segment_seconds:
            self.status.state = "hata"
            self.status.last_error = f"akış {idle:.0f} sn'dir segment üretmiyor"

    async def _snapshot(self, seg: Path) -> None:
        out = self.dir / "latest.jpg"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-sseof", "-1", "-i", str(seg),
            "-frames:v", "1", "-vf", "scale=320:-2", str(out),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        if out.is_file():
            self.status.snapshot = f"/media/canli/{self.status.name}/latest.jpg"

    def _prune(self, current: Path) -> None:
        processed = sorted(p for p in self.dir.glob(SEGMENT_GLOB)
                           if p.name in self._done)
        for old in processed[:_drop_count(processed, settings.live_keep_segments)]:
            old.unlink(missing_ok=True)
        runs = sorted(settings.runs_dir.glob(f"canli-{self.status.name}-*.jsonl"))
        for old in runs[:_drop_count(runs, settings.live_keep_runs)]:
            old.unlink(missing_ok=True)
            old.with_name(old.stem + ".meta.json").unlink(missing_ok=True)
        if len(self._done) > 500:
            self._done = set(sorted(self._done)[-200:])


class LiveCctvService:

    def __init__(
        self,
        manager: ConnectionManager,
        prepare_run: PrepareRun | None = None,
        finalize_run: FinalizeRun | None = None,
        execution_coordinator: ExecutionCoordinator | None = None,
    ) -> None:
        self.manager = manager
        self.prepare_run = prepare_run
        self.finalize_run = finalize_run
        self.execution_coordinator = execution_coordinator
        self.workers: dict[str, LiveFeedWorker] = {}
        self._preview_subscribers: set[asyncio.Queue] = set()

    def _fan_out_preview(self, feed: str, frame: bytes) -> None:
        for queue in self._preview_subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait((feed, frame))

    async def preview_all(self):
        queue: asyncio.Queue = asyncio.Queue(maxsize=PREVIEW_QUEUE_LIMIT)
        self._preview_subscribers.add(queue)
        try:
            for worker in self.workers.values():
                if worker.preview_frame is not None:
                    self._fan_out_preview(worker.status.name, worker.preview_frame)
            while True:
                yield await queue.get()
        finally:
            self._preview_subscribers.discard(queue)

    @property
    def active(self) -> bool:
        return bool(self.workers)

    async def start(self, mode: str = "", feeds: list[dict] | None = None) -> list[FeedStatus]:
        if self.active:
            raise RuntimeError("canlı kip zaten çalışıyor — önce durdurun")
        feed_list = feeds or load_feeds()
        if len(feed_list) > settings.max_feeds:
            raise RuntimeError(
                f"akış sınırı {settings.max_feeds}, listede {len(feed_list)} var")
        for f in feed_list:
            worker = LiveFeedWorker(f["name"], f["url"], self.manager,
                                    mode=mode, desc=f.get("desc", ""),
                                    prepare_run=self.prepare_run,
                                    finalize_run=self.finalize_run,
                                    execution_coordinator=self.execution_coordinator,
                                    on_preview=self._fan_out_preview)
            worker.start()
            self.workers[f["name"]] = worker
        log.info("canlı kip başladı: %d akış", len(self.workers))
        return self.status()

    async def stop(self) -> None:
        workers, self.workers = list(self.workers.values()), {}
        for w in workers:
            await w.stop()
        log.info("canlı kip durdu")

    def status(self) -> list[FeedStatus]:
        for w in self.workers.values():
            w._refresh_lag()
        return [w.status for w in self.workers.values()]

    def preview_frames(self, feed: str):
        worker = self.workers.get(feed)
        if worker is None or not settings.live_preview:
            return None
        return worker.preview_frames()
