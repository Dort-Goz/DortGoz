"""Koşu bağlamı — analiz bittikten SONRA da yaşayan hafıza.

Operatör sohbetinin varlık sebebi bu: analiz kapandığında elde yalnız bir olay
listesi değil, "ne olduğuna dair karar" da kalmalı. Sohbet düğümü bu bağlamı
sistem istemine gömer; böylece operatör "ne oldu?", "neden yüksek risk?",
"18. saniyede ne vardı?" diye sorabilir ve ajan koşuyu hatırlar.

Tek koşuluk, süreç içi (A4: minimal backend). Kalıcılık gerekirse zaten
`runs/<id>.jsonl` var — oradan yeniden kurulabilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent.memory import Incident, Ledger
from .events import WindowReport

TYPE_TR: dict[str, str] = {
    "kavga": "kavga", "saldiri": "saldırı", "hirsizlik": "hırsızlık",
    "silahli_olay": "silahlı olay", "yangin": "yangın", "patlama": "patlama",
    "arac_kazasi": "araç kazası", "vandalizm": "vandalizm",
    "normal": "olağan", "bilinmeyen": "sınıflandırılamayan anomali",
}


def _clock(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


@dataclass
class RunContext:
    """Bir koşunun sohbete taşınan hafızası."""

    run_id: str
    video: str
    duration: float = 0.0
    finished: bool = False
    reports: list[WindowReport] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)

    @property
    def incidents(self) -> list[Incident]:
        return sorted(self.ledger.incidents.values(), key=lambda i: i.first_seen)

    def verdict(self) -> str:
        """Koşunun tek cümlelik kararı — sohbetin çıkış noktası."""
        incidents = self.incidents
        if not incidents:
            return "Kayıtta müdahale gerektiren bir olay tespit edilmedi."
        worst = max(incidents, key=lambda i: ["dusuk", "orta", "yuksek", "kritik"].index(i.risk))
        kind = TYPE_TR.get(worst.anomaly_type, worst.anomaly_type)
        return (f"{len(incidents)} olay tespit edildi; en ciddisi {_clock(worst.first_seen)} "
                f"itibarıyla {kind} ({worst.risk} risk).")

    def briefing(self) -> str:
        """Sistem istemine gömülen tam koşu bağlamı."""
        lines = [
            f"## Çözümlenen kayıt: {self.video}",
            f"Süre: {_clock(self.duration)} · durum: "
            f"{'analiz tamamlandı' if self.finished else 'analiz sürüyor'}",
            "",
            f"### Karar: {self.verdict()}",
        ]

        if self.incidents:
            lines += ["", "### Olay defteri"]
            for inc in self.incidents:
                kind = TYPE_TR.get(inc.anomaly_type, inc.anomaly_type)
                lines.append(
                    f"- [{inc.incident_id}] {_clock(inc.first_seen)}–{_clock(inc.last_seen)} · "
                    f"{kind} · risk {inc.risk} · durum {inc.phase} — {inc.title}"
                )
                for note in inc.notes:
                    lines.append(f"    · {note}")

        if self.reports:
            lines += ["", "### Pencere pencere gözlem"]
            for r in self.reports:
                kind = TYPE_TR.get(r.anomaly_type, r.anomaly_type)
                lines.append(f"- {_clock(r.window_start)}–{_clock(r.window_end)} "
                             f"({kind}): {r.summary}")
                for e in r.events:
                    lines.append(f"    · {_clock(e.t)} [{e.severity_hint}] {e.desc}")
                for u in r.uncertainties:
                    lines.append(f"    ? belirsiz: {u}")
        return "\n".join(lines)


# Süreç içinde tek etkin koşu (A4: kuyruk/işçi altyapısı yok)
_current: RunContext | None = None


def start(run_id: str, video: str) -> RunContext:
    global _current
    _current = RunContext(run_id=run_id, video=video)
    return _current


def current() -> RunContext | None:
    return _current


def clear() -> None:
    global _current
    _current = None
