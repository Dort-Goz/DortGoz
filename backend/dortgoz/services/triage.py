from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from ..config import settings
from ..events import Event

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
    verdict: str = ""
    operator_category: str = ""
    note: str = ""
    decided_wall: float | None = None
    tekrar: int = 1


class TriageStore:
    def __init__(self) -> None:
        self._pending: dict[str, TriageItem] = {}
        self._resolved: list[TriageItem] = []
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self._dismissals: dict[tuple[str, str], int] = {}
        self.rules: dict[tuple[str, str], int] = {}


    def observe(self, event: Event) -> None:
        p = event.payload
        if getattr(p, "type", "") != "incident_update":
            return
        key = f"{event.feed}:{p.incident_id}"
        if key in self._pending:
            item = self._pending[key]
            item.t, item.risk, item.phase = p.t, p.risk, p.phase
            item.title = p.title
            item.model_category = p.anomaly_type
            item.thumbnail = p.thumbnail or item.thumbnail
            item.needs_review = p.needs_review
            item.review_reason = p.review_reason
            return
        if any(r.key == key for r in self._resolved):
            return
        pair = (event.feed, p.anomaly_type)
        if pair in self.rules:
            self.rules[pair] += 1
            self.auto_dismissed += 1
            self._log(TriageItem(
                key=key, feed=event.feed, incident_id=p.incident_id,
                t=p.t, wall=time.time(), title=p.title,
                model_category=p.anomaly_type, risk=p.risk, phase=p.phase,
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
                return
        self._pending[key] = TriageItem(
            key=key, feed=event.feed, incident_id=p.incident_id,
            t=p.t, wall=time.time(), title=p.title,
            model_category=p.anomaly_type, risk=p.risk, phase=p.phase,
            thumbnail=p.thumbnail, needs_review=p.needs_review,
            review_reason=p.review_reason)
        while len(self._pending) > MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))


    def decide(self, key: str, verdict: str, category: str = "",
               note: str = "") -> TriageItem:
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
        item.verdict = verdict
        item.note = note[:500]
        item.decided_wall = time.time()
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]
        self._log(item)
        return item

    def _log(self, item: TriageItem) -> None:
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
            "rules": [{"feed": f, "category": c, "auto_count": n}
                      for (f, c), n in self.rules.items()],
            "categories": CATEGORIES,
        }

    def clear(self) -> None:
        self._pending.clear()
        self._resolved.clear()
        self.dismissed_count = 0
        self.auto_dismissed = 0
        self._dismissals.clear()
        self.rules.clear()


store = TriageStore()
