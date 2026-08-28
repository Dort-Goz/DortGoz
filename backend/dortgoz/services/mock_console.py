from __future__ import annotations

import asyncio
import contextlib
import math
import random
import time

from ..config import settings
from ..events import (
    ActivityStrip,
    AgentStep,
    BoundingBox,
    ChatMessage,
    Event,
    IncidentUpdate,
    RunStatus,
    ToolCall,
    WindowSignals,
)
from ..ws import ConnectionManager
from .action_dispatcher import dispatcher as action_dispatcher
from .live_cctv import FeedStatus, load_feeds

_DISCLAIMER = "\n\n_Arayüz test yanıtı — gerçek analiz değildir._"

_PHASE_TR = {"basladi": "başladı", "gelisiyor": "gelişiyor", "sonuclandi": "sonuçlandı"}


async def _nap(seconds: float) -> None:
    await asyncio.sleep(seconds / max(settings.mock_speed, 0.01))


def _last_incident(manager: ConnectionManager, feed: str) -> IncidentUpdate | None:
    for event in reversed(list(manager._history)):
        payload = event.payload
        if isinstance(payload, IncidentUpdate) and (not feed or event.feed == feed):
            return payload
    return None


def _span(incident: IncidentUpdate) -> str:
    start = incident.olay_baslangic if incident.olay_baslangic is not None else incident.t
    end = incident.olay_bitis if incident.olay_bitis is not None else incident.t
    return f"{start:.0f}–{end:.0f} sn"


def _investigation_answer(prompt: str, incident: IncidentUpdate) -> str:
    head = (
        f"**{incident.incident_id} · {incident.title}** "
        f"({incident.anomaly_type}, risk {incident.risk}, {_span(incident)})\n\n"
    )
    low = prompt.casefold()
    if "kim" in low:
        body = (
            "- **K1**: olayı başlatan kişi; ilk temas anında en ayırt edilebilir durumda.\n"
            "- **K2**: sahnedeki ikinci kişi; geri çekilme hareketi görülüyor.\n"
            f"- En net görünüm: t≈{incident.t:.0f} sn.\n"
            "- Kimlik veya suçluluk çıkarımı yapılmadı; bulgular görüntüyle sınırlıdır."
        )
    elif "zincir" in low or "öncesini" in low or "oncesini" in low:
        start = incident.olay_baslangic if incident.olay_baslangic is not None else incident.t
        end = incident.olay_bitis if incident.olay_bitis is not None else incident.t + 6
        body = (
            "1. Öncesi: sahne sakin, olağan hareket düzeni.\n"
            f"2. İlk kritik hareket: t≈{start:.0f} sn.\n"
            f"3. Dönüm noktası: t≈{incident.t:.0f} sn — {incident.detail or incident.title}.\n"
            f"4. Sonuç: t≈{end:.0f} sn sonrasında taraflar sahneden ayrılıyor.\n"
            "5. Kayıt dışı: olayın öncesine ait görüntü bu kayıtta yok."
        )
    elif "kanıt" in low or "kanit" in low:
        body = (
            "- Destekleyen: hareket profili ve pencere raporu aynı aralığı gösteriyor.\n"
            "- Zayıflatan: tek kamera açısı; temasın niteliği kısmen belirsiz.\n"
            "- Kör nokta: sahnenin sağ kesiti görüş alanı dışında.\n"
            f"- En güçlü kanıt: t≈{incident.t:.0f} sn karesi."
        )
    else:
        body = (
            f"- Kategori odağı: `{incident.anomaly_type}` örüntüsü inceleniyor.\n"
            f"- {incident.review_reason or incident.detail or 'Ek belirsizlik kaydı yok.'}\n"
            f"- Kritik an: t≈{incident.t:.0f} sn."
        )
    return head + body + _DISCLAIMER


def _general_answer(text: str, incident: IncidentUpdate | None) -> str:
    if incident is None:
        return (
            "Şu anda kayıtlı olay yok. Bir koşu başlatın veya zaman çizelgesinden olay seçin; "
            "seçili olay sorularınıza bağlam olarak eklenir." + _DISCLAIMER
        )
    return (
        f"Odaktaki olay: **{incident.incident_id} · {incident.title}** "
        f"(risk {incident.risk}, durum {_PHASE_TR.get(incident.phase, incident.phase)}).\n"
        f"- Aralık: {_span(incident)}\n"
        f"- Not: {incident.detail or 'ayrıntı kaydı yok'}\n"
        f'Sorunuz: "{text[:120]}" — gerçek modda bu yanıt ajan grafiğinden gelir.'
        + _DISCLAIMER
    )


async def mock_chat(
    text: str,
    manager: ConnectionManager,
    *,
    dialogue_id: str = "",
    feed: str = "",
    referenced_event_id: str = "",
) -> str:
    key = dialogue_id.strip() or "legacy"
    incident = _last_incident(manager, feed)

    async def step(node: str, status: str, detail: str) -> None:
        await manager.broadcast(
            Event.wrap(
                AgentStep(node=node, status=status, detail=detail, dialogue_id=key),
                feed=feed,
            )
        )

    await step("context", "end", f"kamera: {feed or 'ana kamera'}")
    await _nap(0.15)
    await step("agent", "start", "soru inceleniyor")
    if incident is not None:
        await _nap(0.2)
        await manager.broadcast(
            Event.wrap(
                ToolCall(
                    tool="olayi_aydinlat",
                    args={"incident_id": incident.incident_id},
                    rationale="Soru seçili olaya bağlandı",
                    result=f"{incident.incident_id} bağlamı yüklendi",
                    dialogue_id=key,
                ),
                feed=feed,
            )
        )
    await _nap(0.2)
    await step("agent", "end", "yanıt hazırlandı")
    if incident is not None and text.startswith("Olayı aydınlat:"):
        answer = _investigation_answer(text, incident)
    else:
        answer = _general_answer(text, incident)
    for index in range(0, len(answer), 48):
        await manager.broadcast(
            Event.wrap(
                ChatMessage(
                    role="agent",
                    text=answer[index : index + 48],
                    streaming=True,
                    dialogue_id=key,
                ),
                feed=feed,
            )
        )
        await _nap(0.05)
    await manager.broadcast(
        Event.wrap(
            ChatMessage(role="agent", text="", streaming=False, dialogue_id=key),
            feed=feed,
        )
    )
    return answer


def placeholder_frame(timestamp: float) -> bytes:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270">'
        '<rect width="480" height="270" fill="#0d1117"/>'
        '<rect x="1" y="1" width="478" height="268" fill="none" stroke="#30363d"/>'
        '<text x="240" y="125" text-anchor="middle" fill="#7d8590" '
        'font-family="monospace" font-size="15">temsili kanıt karesi</text>'
        f'<text x="240" y="150" text-anchor="middle" fill="#e6edf3" '
        f'font-family="monospace" font-size="13">t = {timestamp:.1f} sn · MOCK</text>'
        "</svg>"
    )
    return svg.encode("utf-8")


_DEFAULT_FEEDS = [
    {"name": "mock-giris", "desc": "Giriş Kapısı"},
    {"name": "mock-otopark", "desc": "Otopark"},
    {"name": "mock-koridor", "desc": "Koridor B"},
    {"name": "mock-depo", "desc": "Depo Alanı"},
]

_LIVE_SCENARIOS = [
    ("hirsizlik", "yuksek", "Zorla giriş şüphesi", "Kapı bölgesinde zorlayıcı temas", False, ""),
    ("kavga", "orta", "İtişme şüphesi", "İki kişi arasında kısa temas", True,
     "Tek kamera açısı, temas net değil"),
    ("yangin", "kritik", "Duman şüphesi", "Raf hattı üzerinde duman benzeri hareket", False, ""),
    ("vandalizm", "orta", "Vandalizm şüphesi", "Cephe üzerinde sprey hareketi", True,
     "Işık yetersiz, davranış belirsiz"),
    ("arac_kazasi", "yuksek", "Araç çarpışması", "Park halindeki araca temas", False, ""),
]

_AUTO_ACTIONS = {
    "hirsizlik": "emniyet_bildirimi_hazirla",
    "yangin": "acil_saglik_bildirimi_hazirla",
    "arac_kazasi": "acil_saglik_bildirimi_hazirla",
}


def mock_event_clip() -> str | None:
    root = settings.media_dir
    if not root.is_dir():
        return None
    for candidate in sorted(root.glob("*.mp4")):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return f"/media/{candidate.name}"
    return None


class MockLiveService:
    def __init__(self, manager: ConnectionManager) -> None:
        self.manager = manager
        self.active = False
        self._statuses: list[FeedStatus] = []
        self._task: asyncio.Task[None] | None = None
        self._seq = 0
        self._clip = mock_event_clip()
        self._activity_end: dict[str, float] = {}

    async def start(self, mode: str = "", feeds: list[dict] | None = None) -> list[FeedStatus]:
        if self.active:
            raise RuntimeError("canlı kip zaten açık")
        specs = feeds
        if specs is None:
            try:
                specs = load_feeds()
            except (FileNotFoundError, ValueError):
                specs = _DEFAULT_FEEDS
        self._statuses = [
            FeedStatus(
                name=spec["name"],
                url=f"mock://{spec['name']}",
                desc=spec.get("desc", ""),
                state="bekliyor",
            )
            for spec in specs[: settings.max_feeds]
        ]
        for index, status in enumerate(self._statuses):
            self._write_snapshot(status, index)
        self.active = True
        self._task = asyncio.create_task(self._run(), name="dortgoz-mock-live")
        return list(self._statuses)

    async def stop(self) -> None:
        self.active = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._statuses = []

    def status(self) -> list[FeedStatus]:
        return list(self._statuses)

    def preview_frames(self, feed: str):
        return None

    def _write_snapshot(self, status: FeedStatus, tick: int) -> None:
        root = settings.media_dir / "canli-mock" / status.name
        root.mkdir(parents=True, exist_ok=True)
        hue = sum(map(ord, status.name)) * 37 % 360
        x = 60 + (tick * 37 + hue) % 480
        y = 190 + (tick * 11) % 70
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
            '<rect width="640" height="360" fill="#0d1117"/>'
            f'<rect y="150" width="640" height="70" fill="hsl({hue} 25% 14%)"/>'
            '<rect y="300" width="640" height="60" fill="#161b22"/>'
            f'<circle cx="{x}" cy="{y}" r="13" fill="#8b949e"/>'
            f'<rect x="{x - 8}" y="{y + 11}" width="16" height="42" rx="4" fill="#8b949e"/>'
            '<text x="320" y="348" text-anchor="middle" fill="#7d8590" '
            'font-family="monospace" font-size="14">'
            f'{time.strftime("%H:%M:%S")} · MOCK</text>'
            "</svg>"
        )
        (root / "latest.svg").write_text(svg, encoding="utf-8")
        status.snapshot = f"/media/canli-mock/{status.name}/latest.svg"

    async def _emit_activity(
        self, status: FeedStatus, rng: random.Random, *,
        quiet: bool = False, status_name: str = "", risk: str | None = None,
    ) -> None:
        gate = 0.006
        span = float(settings.window_seconds)
        levels = []
        for _ in range(int(span)):
            if quiet and rng.random() < 0.8:
                levels.append(0)
            elif rng.random() < 0.25:
                levels.append(0)
            else:
                levels.append(rng.randint(1, 3))
        resolved = status_name or (
            "sakin" if all(level == 0 for level in levels) else
            "eleme" if rng.random() < 0.25 else "hareket"
        )
        self._activity_end[status.name] = self._activity_end.get(status.name, 0.0) + span
        await self.manager.broadcast(
            Event.wrap(
                ActivityStrip(
                    window_start=self._activity_end[status.name] - span,
                    window_end=self._activity_end[status.name],
                    content_start=time.time() - span,
                    gate=gate,
                    peak=gate * (1 + max(levels)),
                    status=resolved,
                    risk=risk,
                    levels=levels,
                ),
                feed=status.name,
                live=True,
            )
        )

    async def _propose_action(
        self, feed: str, incident_id: str, anomaly: str, title: str,
    ) -> None:
        action = _AUTO_ACTIONS.get(anomaly)
        if action is None:
            return
        try:
            request, created = action_dispatcher.request(
                action,
                incident_id,
                feed,
                f"{title} için yerel taslak operatör onayına sunuldu",
            )
        except (KeyError, ValueError):
            return
        if not created:
            return
        await self.manager.broadcast(
            Event.wrap(request, feed=request.feed, live=request.live)
        )
        await self.manager.broadcast(
            Event.wrap(
                ToolCall(
                    tool=action,
                    args={"incident_id": incident_id},
                    rationale="Kanıtlı yüksek riskli olay operatör onayına sunulur",
                    result=(
                        f"{request.action_label} operatöre sunuldu · "
                        "dış kuruma gönderim yok"
                    ),
                ),
                feed=feed,
                live=True,
            )
        )

    async def _emit_incident(self, status: FeedStatus, rng: random.Random) -> None:
        self._seq += 1
        run_id = f"canli-mock-{status.name}-{self._seq:04d}"
        anomaly, risk, title, detail, needs_review, reason = rng.choice(_LIVE_SCENARIOS)
        t = float(rng.randint(4, 50))
        incident_id = f"MLI-{self._seq:03d}"
        status.state = "isleniyor"
        await self.manager.broadcast(
            Event.wrap(
                RunStatus(run_id=run_id, state="processing", progress=0.2,
                          detail="Canlı segment işleniyor (mock)"),
                feed=status.name,
                live=True,
            )
        )
        await self._emit_activity(
            status, rng,
            status_name="anomali" if risk in {"yuksek", "kritik"} else "dikkat",
            risk=risk,
        )
        await _nap(0.4)
        await self.manager.broadcast(
            Event.wrap(
                IncidentUpdate(
                    incident_id=incident_id,
                    t=t,
                    phase="basladi",
                    title=title,
                    anomaly_type=anomaly,
                    risk=risk,
                    detail=detail,
                    needs_review=needs_review,
                    review_reason=reason,
                    olay_baslangic=max(0.0, t - 1),
                    boxes=[
                        BoundingBox(x1=0.3, y1=0.2, x2=0.6, y2=0.85,
                                    label="person", conf=0.88, track_id=self._seq)
                    ],
                    signals=WindowSignals(
                        durum_p=round(rng.uniform(0.55, 0.92), 2),
                        anomaly_score=round(rng.uniform(0.5, 0.9), 2),
                        image_quality=round(rng.uniform(0.6, 0.9), 2),
                        screening_model="mock-screening-v1",
                    ),
                ),
                feed=status.name,
                live=True,
            )
        )
        await _nap(0.5)
        await self.manager.broadcast(
            Event.wrap(
                IncidentUpdate(
                    incident_id=incident_id,
                    t=t + 6,
                    phase="sonuclandi",
                    title=title,
                    anomaly_type=anomaly,
                    risk=risk,
                    detail=detail,
                    needs_review=needs_review,
                    review_reason=reason,
                    evidence=self._clip,
                    olay_baslangic=max(0.0, t - 1),
                    olay_bitis=t + 6,
                ),
                feed=status.name,
                live=True,
            )
        )
        await _nap(0.3)
        if not needs_review:
            await self._propose_action(status.name, incident_id, anomaly, title)
        await self.manager.broadcast(
            Event.wrap(
                RunStatus(run_id=run_id, state="done", progress=1.0,
                          detail="Canlı segment tamamlandı (mock)"),
                feed=status.name,
                live=True,
            )
        )
        status.state = "bekliyor"

    async def _run(self) -> None:
        rng = random.Random(20260827)
        tick = 0
        down = ""
        down_until = 0
        next_incident = rng.randint(4, 8)
        while self.active:
            await _nap(2.0)
            tick += 1
            for index, status in enumerate(self._statuses):
                if status.name == down:
                    if tick >= down_until:
                        down = ""
                        status.state = "bekliyor"
                        status.last_error = ""
                    else:
                        status.state = "hata"
                        status.lag_s = None
                        status.last_error = "mock: akış koptu, yeniden deneniyor"
                        continue
                status.lag_s = round(8 + 26 * abs(math.sin((tick + index * 5) / 7.0)), 1)
                if (tick + index) % 2 == 0:
                    status.segments_done += 1
                    self._write_snapshot(status, tick + index)
                    await self._emit_activity(status, rng, quiet=index % 3 == 0)
            if tick >= next_incident:
                next_incident = tick + rng.randint(6, 14)
                healthy = [s for s in self._statuses if s.state != "hata"]
                if healthy:
                    await self._emit_incident(rng.choice(healthy), rng)
            if not down and tick % 45 == 0 and len(self._statuses) > 1:
                target = rng.choice(self._statuses)
                down = target.name
                down_until = tick + 6
