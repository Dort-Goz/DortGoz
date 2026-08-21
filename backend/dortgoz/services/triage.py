from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import settings
from ..events import Event

LEDGER_VERSION = 2

CATEGORIES = ["kavga", "saldiri", "hirsizlik", "silahli_olay", "yangin",
              "patlama", "arac_kazasi", "vandalizm", "bilinmeyen"]
MAX_PENDING = 200
MAX_RESOLVED = 500
RULE_THRESHOLD = 3
RISK = ["dusuk", "orta", "yuksek", "kritik"]

_NOTE_TR = {
    "arac_kazasi": "duran/yavaşlayan araçlar ve yanlarında bekleyen kişiler",
    "hirsizlik": "araç ve eşya çevresindeki olağan yükleme/bekleme hareketleri",
    "kavga": "yakın duran veya el kol hareketi yapan kişiler",
    "saldiri": "yakın temas hâlindeki kişiler",
    "vandalizm": "yapı/eşya yakınında çalışan veya bekleyen kişiler",
    "silahli_olay": "elde taşınan uzun cisimler (alet, şemsiye vb.)",
    "yangin": "egzoz/buhar/yansıma kaynaklı duman-ışık görüntüleri",
    "patlama": "ani ışık/parlama değişimleri",
    "bilinmeyen": "bu kameranın olağan sahne hareketleri",
}


def _config_snapshot() -> dict[str, Any]:
    return {
        "escalate_p": settings.escalate_p,
        "candidate_start_threshold": settings.candidate_start_threshold,
        "candidate_continue_threshold": settings.candidate_continue_threshold,
        "candidate_screening": settings.candidate_screening,
        "second_opinion_model": settings.second_opinion_model,
        "dual_read": settings.dual_read,
        "final_sweep": settings.final_sweep,
    }


def _run_meta(run_id: str) -> dict[str, Any]:
    if not run_id:
        return {}
    try:
        raw = (settings.runs_dir / f"{run_id}.meta.json").read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        meta = json.loads(raw)
    except ValueError:
        return {}
    out = {k: meta.get(k, "") for k in ("model", "mode", "video")}
    prompt = meta.get("system_prompt", "") or ""
    task = meta.get("task_prompt", "") or ""
    out["system_prompt_sha"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    out["task_prompt_sha"] = hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]
    return out


@dataclass
class TriageItem:
    key: str
    feed: str
    incident_id: str
    t: float
    wall: float
    title: str
    model_category: str
    risk: str
    phase: str
    thumbnail: str | None = None
    needs_review: bool = False
    review_reason: str = ""
    run_id: str = ""
    video: str = ""
    model_start: float | None = None
    model_end: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""
    operator_category: str = ""
    operator_start: float | None = None
    operator_end: float | None = None
    reviewer: str = ""
    note: str = ""
    decided_wall: float | None = None
    tekrar: int = 1
    decision_id: str = ""
    supersedes: str | None = None
    ledger_version: int = LEDGER_VERSION
    config: dict[str, Any] = field(default_factory=dict)
    run_meta: dict[str, Any] = field(default_factory=dict)


class TriageStore:
    def __init__(self) -> None:
        self._pending: dict[str, TriageItem] = {}
        self._resolved: list[TriageItem] = []
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self._dismissals: dict[tuple[str, str], int] = {}
        self.rules: dict[tuple[str, str], int] = {}
        self._runs: dict[str, tuple[str, str]] = {}
        self.expired_count = 0


    def _signal_dict(self, payload: Any) -> dict[str, Any]:
        sig = getattr(payload, "signals", None)
        return sig.model_dump() if sig is not None else {}

    def _merge_signals(self, item: TriageItem, payload: Any) -> None:
        incoming = self._signal_dict(payload)
        if not incoming:
            return
        if not item.signals:
            item.signals = incoming
            return
        old = item.signals.get("durum_p")
        new = incoming.get("durum_p")
        if new is not None and (old is None or new > old):
            item.signals = incoming

    def observe(self, event: Event) -> None:
        p = event.payload
        kind = getattr(p, "type", "")
        if kind == "run_status":
            if getattr(p, "run_id", ""):
                self._runs[event.feed] = (p.run_id, getattr(p, "video", ""))
            return
        if kind != "incident_update":
            return
        run_id, video = self._runs.get(event.feed, ("", ""))
        key = f"{event.feed}:{p.incident_id}"
        if key in self._pending:
            item = self._pending[key]
            item.t, item.risk, item.phase = p.t, p.risk, p.phase
            item.title = p.title
            item.model_category = p.anomaly_type
            item.thumbnail = p.thumbnail or item.thumbnail
            item.needs_review = p.needs_review
            item.review_reason = p.review_reason
            if p.olay_baslangic is not None:
                item.model_start = p.olay_baslangic
            if p.olay_bitis is not None:
                item.model_end = p.olay_bitis
            self._merge_signals(item, p)
            return
        if any(r.key == key for r in self._resolved):
            return
        pair = (event.feed, p.anomaly_type)
        if pair in self.rules:
            self.rules[pair] += 1
            self.auto_dismissed += 1
            self._stamp_and_log(TriageItem(
                key=key, feed=event.feed, incident_id=p.incident_id,
                t=p.t, wall=time.time(), title=p.title,
                model_category=p.anomaly_type, risk=p.risk, phase=p.phase,
                run_id=run_id, video=video, signals=self._signal_dict(p),
                model_start=p.olay_baslangic, model_end=p.olay_bitis,
                verdict="sorun_degil", decided_wall=time.time(),
                note=f"otomatik: operatör kuralı ({self._dismissals.get(pair, 0)}× sorun değil)"))
            return
        for item in self._pending.values():
            if item.feed == event.feed and item.model_category == p.anomaly_type:
                item.tekrar += 1
                item.t, item.wall = p.t, time.time()
                if RISK.index(p.risk) > RISK.index(item.risk):
                    item.risk = p.risk
                item.thumbnail = p.thumbnail or item.thumbnail
                self._merge_signals(item, p)
                return
        self._pending[key] = TriageItem(
            key=key, feed=event.feed, incident_id=p.incident_id,
            t=p.t, wall=time.time(), title=p.title,
            model_category=p.anomaly_type, risk=p.risk, phase=p.phase,
            thumbnail=p.thumbnail, needs_review=p.needs_review,
            review_reason=p.review_reason,
            run_id=run_id, video=video, signals=self._signal_dict(p),
            model_start=p.olay_baslangic, model_end=p.olay_bitis)
        while len(self._pending) > MAX_PENDING:
            dropped = self._pending.pop(next(iter(self._pending)))
            dropped.verdict = "expired"
            dropped.decided_wall = time.time()
            dropped.note = "kuyruk taştı: operatör karar veremeden düştü"
            self.expired_count += 1
            self._stamp_and_log(dropped)


    def decide(self, key: str, verdict: str, category: str = "",
               note: str = "", reviewer: str = "",
               operator_start: float | None = None,
               operator_end: float | None = None) -> TriageItem:
        if verdict not in {"anomali", "sorun_degil"}:
            raise ValueError(f"geçersiz karar: {verdict}")
        item = self._pending.pop(key, None)
        if item is None:
            raise KeyError(f"bekleyen kayıt yok: {key}")
        if verdict == "anomali":
            if category not in CATEGORIES:
                raise ValueError(f"geçersiz kategori: {category}")
            item.operator_category = category
            self._dismissals.pop((item.feed, item.model_category), None)
        else:
            self.dismissed_count += 1
            pair = (item.feed, item.model_category)
            self._dismissals[pair] = self._dismissals.get(pair, 0) + 1
            if self._dismissals[pair] >= RULE_THRESHOLD and pair not in self.rules:
                self.rules[pair] = 0
        if operator_start is not None and operator_end is not None:
            if operator_start > operator_end:
                raise ValueError("operatör başlangıcı bitişten sonra olamaz")
        item.verdict = verdict
        item.note = note[:500]
        item.reviewer = reviewer[:120]
        item.operator_start = operator_start
        item.operator_end = operator_end
        item.decided_wall = time.time()
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]
        self._stamp_and_log(item)
        return item

    def revise(self, key: str, verdict: str, category: str = "",
               note: str = "", reviewer: str = "",
               operator_start: float | None = None,
               operator_end: float | None = None) -> TriageItem:
        prior = next((i for i in reversed(self._resolved) if i.key == key), None)
        if prior is None:
            raise KeyError(f"düzeltilecek karar yok: {key}")
        if verdict not in {"anomali", "sorun_degil"}:
            raise ValueError(f"geçersiz karar: {verdict}")
        if verdict == "anomali" and category not in CATEGORIES:
            raise ValueError(f"geçersiz kategori: {category}")
        from dataclasses import replace
        item = replace(
            prior,
            verdict=verdict,
            operator_category=category if verdict == "anomali" else "",
            note=note[:500],
            reviewer=reviewer[:120],
            operator_start=operator_start,
            operator_end=operator_end,
            decided_wall=time.time(),
            decision_id="",
            supersedes=prior.decision_id,
        )
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]
        self._stamp_and_log(item)
        return item

    def _stamp_and_log(self, item: TriageItem) -> None:
        if not item.decision_id:
            item.decision_id = uuid.uuid4().hex
        if not item.config:
            item.config = _config_snapshot()
        if not item.run_meta:
            item.run_meta = _run_meta(item.run_id)
        try:
            settings.runs_dir.mkdir(parents=True, exist_ok=True)
            with (settings.runs_dir / "nobet_defteri.jsonl").open("a") as fh:
                fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        except OSError:
            pass


    def revoke_rule(self, feed: str, category: str) -> None:
        self.rules.pop((feed, category), None)
        self._dismissals.pop((feed, category), None)

    def feed_note(self, feed: str) -> str:
        parts = [_NOTE_TR.get(cat, cat) for (f, cat) in self.rules if f == feed]
        if not parts:
            return ""
        return ("\n\n## Bu kameraya özgü OLAĞAN durumlar (operatör geri bildirimi)\n"
                + "".join(f"- {p} bu kamerada olağandır; tek başına alarm üretme.\n"
                          for p in parts))


    def snapshot(self) -> dict:
        confirmed = [asdict(i) for i in reversed(self._resolved)
                     if i.verdict == "anomali"]
        return {
            "pending": [asdict(i) for i in reversed(list(self._pending.values()))],
            "confirmed": confirmed,
            "dismissed_count": self.dismissed_count,
            "auto_dismissed": self.auto_dismissed,
            "expired_count": self.expired_count,
            "rules": [{"feed": f, "category": c, "auto_count": n}
                      for (f, c), n in self.rules.items()],
            "categories": CATEGORIES,
        }

    def clear(self) -> None:
        self._pending.clear()
        self._resolved.clear()
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self.expired_count = 0
        self._dismissals.clear()
        self.rules.clear()
        self._runs.clear()


store = TriageStore()
