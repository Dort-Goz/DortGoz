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
from typing import TYPE_CHECKING

from .agent.memory import Incident, Ledger
from .config import settings
from .events import WindowReport

if TYPE_CHECKING:
    from .services.runtime_postprocess import RuntimeWindowValidation

TYPE_TR: dict[str, str] = {
    "kavga": "kavga", "saldiri": "saldırı", "hirsizlik": "hırsızlık",
    "silahli_olay": "silahlı olay", "yangin": "yangın", "patlama": "patlama",
    "arac_kazasi": "araç kazası", "vandalizm": "vandalizm",
    "normal": "olağan", "bilinmeyen": "sınıflandırılamayan anomali",
}

# Şema değerleri ASCII (dusuk/yuksek) — operatöre dönük metin Türkçesini kullanır
RISK_TR: dict[str, str] = {
    "dusuk": "düşük", "orta": "orta", "yuksek": "yüksek", "kritik": "kritik",
}


def _clock(t: float) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


@dataclass
class RunContext:
    """Bir koşunun sohbete taşınan hafızası."""

    run_id: str
    video: str
    feed: str = ""                    # çoklu-akış (demo) kamera etiketi
    duration: float = 0.0
    finished: bool = False
    reports: list[WindowReport] = field(default_factory=list)
    validation_sidecars: list[RuntimeWindowValidation] = field(default_factory=list)
    ledger: Ledger = field(
        default_factory=lambda: Ledger(settings.incident_grace_windows))

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
        review = sum(1 for i in incidents if i.needs_review)
        return (f"{len(incidents)} olay tespit edildi; en ciddisi {_clock(worst.first_seen)} "
                f"itibarıyla {kind} ({RISK_TR.get(worst.risk, worst.risk)} risk)."
                + (f" {review} olay insan incelemesi istiyor." if review else ""))

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


# Akış başına bir bağlam (çoklu-akış demo kipi); "" = tek-akış varsayılanı.
# Ekleme sırası korunur — current() en son başlayanı verir (araçların odağı).
_contexts: dict[str, RunContext] = {}


def start(run_id: str, video: str, feed: str = "") -> RunContext:
    ctx = RunContext(run_id=run_id, video=video, feed=feed)
    _contexts.pop(feed, None)         # aynı akışta yeni koşu → sona taşınsın
    _contexts[feed] = ctx
    # Yeni koşu = yeni bağlam: eski kaydın sohbeti yeni kayda taşınmasın.
    # Demo başlatması N koşuyu art arda açar; sıfırlama idempotent, sorun değil.
    from .agent.graph import reset_history
    reset_history()
    return ctx


def current() -> RunContext | None:
    """En son başlayan koşu — tek akışta TEK bağlam, araçların varsayılan odağı."""
    return next(reversed(_contexts.values()), None)


def all_contexts() -> list[RunContext]:
    """Etkin tüm akışlar, başlama sırasıyla (sohbet brifingi hepsini görür)."""
    return list(_contexts.values())


def clear() -> None:
    _contexts.clear()
