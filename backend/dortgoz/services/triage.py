"""Anomali nöbet kuyruğu — operatör insan-döngüde karar katmanı.

Sistem tespit eder, İNSAN hükmeder: defterin açtığı her olay (herhangi bir
akıştan/koşudan, `incident_update` yayınından toplanır) nöbet kuyruğuna düşer.
Operatör her kaydı inceler ve karara bağlar:

- **sorun_degil** — yanlış/önemsiz; kayıt kuyruğun dışına alınır (sayısı tutulur).
- **anomali** — doğrulanmış; operatör taksonomiden kategori seçer (modelin
  önerisini düzeltebilir) ve kayıt "bu oturumda tespit edilenler" listesine
  geçer.

Durum sunucu tarafındadır (görüntüleyiciler arasında ortak, yenilemeye
dayanıklı) ve her karar `runs/nobet_defteri.jsonl`'e eklenir — oturum sonrası
iz (kim ne zaman neye ne dedi) kaybolmaz. Bellek 7/24 bütçelidir: bekleyen ve
çözülen listeler son-N ile sınırlanır.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from ..config import settings
from ..events import Event

# Operatörün seçebileceği kategoriler (events.AnomalyType eksi "normal" —
# "normal" bir kategori değil karardır: onun yolu "sorun_degil").
CATEGORIES = ["kavga", "saldiri", "hirsizlik", "silahli_olay", "yangin",
              "patlama", "arac_kazasi", "vandalizm", "bilinmeyen"]
MAX_PENDING = 200
MAX_RESOLVED = 500


@dataclass
class TriageItem:
    key: str                       # "<feed>:<incident_id>"
    feed: str
    incident_id: str
    t: float                       # olay video zamanı
    wall: float                    # kuyruğa düşme anı (epoch)
    title: str
    model_category: str            # modelin önerisi
    risk: str
    phase: str
    thumbnail: str | None = None
    needs_review: bool = False
    review_reason: str = ""
    # Operatör kararı (bekleyende boş):
    verdict: str = ""              # "" | "anomali" | "sorun_degil"
    operator_category: str = ""
    note: str = ""
    decided_wall: float | None = None


class TriageStore:
    def __init__(self) -> None:
        self._pending: dict[str, TriageItem] = {}
        self._resolved: list[TriageItem] = []
        self.dismissed_count = 0

    # ---- alım (WS yayın dinleyicisi) ----

    def observe(self, event: Event) -> None:
        p = event.payload
        if getattr(p, "type", "") != "incident_update":
            return
        key = f"{event.feed}:{p.incident_id}"
        if key in self._pending:      # yaşam döngüsü güncellemesi: kartı tazele
            item = self._pending[key]
            item.t, item.risk, item.phase = p.t, p.risk, p.phase
            item.title = p.title
            item.model_category = p.anomaly_type
            item.thumbnail = p.thumbnail or item.thumbnail
            item.needs_review = p.needs_review
            item.review_reason = p.review_reason
            return
        if any(r.key == key for r in self._resolved):
            return                    # karar verilmiş olaya geri dönülmez
        self._pending[key] = TriageItem(
            key=key, feed=event.feed, incident_id=p.incident_id,
            t=p.t, wall=time.time(), title=p.title,
            model_category=p.anomaly_type, risk=p.risk, phase=p.phase,
            thumbnail=p.thumbnail, needs_review=p.needs_review,
            review_reason=p.review_reason)
        # 7/24 bütçesi: kuyruk taşarsa EN ESKİ bekleyen düşer (karar verilmeden
        # kaybolan sayılmaz — operatör yetişemiyorsa bu zaten görünür sorundur)
        while len(self._pending) > MAX_PENDING:
            self._pending.pop(next(iter(self._pending)))

    # ---- operatör kararı ----

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
        else:
            self.dismissed_count += 1
        item.verdict = verdict
        item.note = note[:500]
        item.decided_wall = time.time()
        self._resolved.append(item)
        del self._resolved[:-MAX_RESOLVED]
        self._log(item)
        return item

    def _log(self, item: TriageItem) -> None:
        """Karar izi: oturum kapansa da nöbet defteri diskte kalır."""
        try:
            settings.runs_dir.mkdir(parents=True, exist_ok=True)
            with (settings.runs_dir / "nobet_defteri.jsonl").open("a") as fh:
                fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        except OSError:
            pass                      # disk hatası kararı düşürmez

    # ---- görünüm ----

    def snapshot(self) -> dict:
        confirmed = [asdict(i) for i in reversed(self._resolved)
                     if i.verdict == "anomali"]
        return {
            "pending": [asdict(i) for i in reversed(list(self._pending.values()))],
            "confirmed": confirmed,
            "dismissed_count": self.dismissed_count,
            "categories": CATEGORIES,
        }

    def clear(self) -> None:          # testler için
        self._pending.clear()
        self._resolved.clear()
        self.dismissed_count = 0


store = TriageStore()   # süreç-küresel tekil — tüm akışlar tek nöbet kuyruğu
