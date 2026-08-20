from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..ws import ConnectionManager

log = logging.getLogger(__name__)

SEGMENT_GLOB = "seg_*.mp4"


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
        if "/" in f["name"] or f["name"] in seen:
            raise ValueError(f"akış adı geçersiz/yinelenmiş: {f['name']}")
        seen.add(f["name"])
    return feeds


def plan_segments(pending: list[Path], max_backlog: int) -> tuple[list[Path], list[Path]]:
    if len(pending) <= max_backlog:
        return [], pending
    return pending[:-max_backlog], pending[-max_backlog:]


class LiveFeedWorker:
    def __init__(self, name: str, url: str, manager: ConnectionManager,
                 mode: str = "", desc: str = "") -> None:
        self.status = FeedStatus(name=name, url=url, desc=desc)
        self.manager = manager
        self.mode = mode
        self.dir = settings.media_dir / "canli" / name
        self.running = False
        self._done: set[str] = set()
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_seg_mtime: float | None = None


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
        return ["ffmpeg", "-nostdin", "-loglevel", "error",
                "-i", self.status.url, "-an", "-c:v", "copy",
                "-f", "segment", "-segment_time", str(settings.live_segment_seconds),
                "-reset_timestamps", "1", "-strftime", "1",
                str(self.dir / "seg_%s.mp4")]

    async def _ffmpeg_loop(self) -> None:
        backoff = 5.0
        try:
            while self.running:
                try:
                    self._proc = await asyncio.create_subprocess_exec(
                        *self._ffmpeg_cmd(),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE)
                    self.status.state = "akiyor"
                    backoff = 5.0
                    _, stderr = await self._proc.communicate()
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


    def _closed_segments(self) -> list[Path]:
        segs = sorted(self.dir.glob(SEGMENT_GLOB))
        return [s for s in segs[:-1]
                if s.name not in self._done and s.stat().st_size > 0]

    async def _process_loop(self) -> None:
        while self.running:
            worked = await self._step()
            if not worked:
                await asyncio.sleep(2.0)

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
        try:
            from ..pipeline.runner import run_video
            from .triage import store as triage_store

            note = triage_store.feed_note(self.status.name)
            system_prompt = ""
            if note:
                from ..pipeline.interpret import SYSTEM_TR
                system_prompt = SYSTEM_TR + note
            await run_video(self.manager, rel, run_id,
                            feed=self.status.name, mode=self.mode, live=True,
                            system_prompt=system_prompt)
            self.status.segments_done += 1
            self.status.last_error = ""
        except Exception as exc:
            self.status.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.exception("canlı %s: segment işlenemedi: %s", self.status.name, seg.name)
        self._done.add(seg.name)
        self._last_seg_mtime = seg_mtime
        self._refresh_lag()
        await self._snapshot(seg)
        self._prune(seg)
        self.status.state = "akiyor"
        return True

    def _refresh_lag(self) -> None:
        if self._last_seg_mtime is not None:
            self.status.lag_s = round(time.time() - self._last_seg_mtime, 1)

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
        for old in processed[:-settings.live_keep_segments]:
            old.unlink(missing_ok=True)
        runs = sorted(settings.runs_dir.glob(f"canli-{self.status.name}-*.jsonl"))
        for old in runs[:-settings.live_keep_runs]:
            old.unlink(missing_ok=True)
            old.with_name(old.stem + ".meta.json").unlink(missing_ok=True)
        if len(self._done) > 500:
            self._done = set(sorted(self._done)[-200:])


class LiveCctvService:

    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager
        self.workers: dict[str, LiveFeedWorker] = {}

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
                                    mode=mode, desc=f.get("desc", ""))
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
