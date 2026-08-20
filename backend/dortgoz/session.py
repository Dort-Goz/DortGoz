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
from .config import settings
from .events import WindowReport
from .utils import format_clock

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


# 7/24 canlı akışta bağlam sınırsız büyür: 24 akış × 2 pencere/dk brifingi
# ~1 saatte 96K bağlamı doldurur (2026-08-14 dayanıklılık incelemesi).
# Sınırlar anomali bilgisini HİÇ atmaz — yalnız sakin pencereler bütçelenir;
# tam kayıt her zaman runs/<id>.jsonl'dedir.
BRIEFING_RECENT_WINDOWS = 40      # brifinge tam metin giren son pencere sayısı
MAX_NORMAL_REPORTS = 200          # RAM'de tutulan olaysız sakin pencere sayısı


def _is_quiet(r: WindowReport) -> bool:
    return r.anomaly_type == "normal" and not r.events


@dataclass
class RunContext:
    """Bir koşunun sohbete taşınan hafızası."""

    run_id: str
    video: str
    feed: str = ""                    # çoklu-akış (demo) kamera etiketi
    duration: float = 0.0
    finished: bool = False
    reports: list[WindowReport] = field(default_factory=list)
    dropped_quiet: int = 0            # RAM bütçesi için atılan sakin pencere sayısı
    ledger: Ledger = field(
        default_factory=lambda: Ledger(settings.incident_grace_windows))

    @property
    def incidents(self) -> list[Incident]:
        return sorted(self.ledger.incidents.values(), key=lambda i: i.first_seen)

    def add_report(self, report: WindowReport) -> None:
        """Rapor ekler; sakin pencereler RAM bütçesini aşarsa en eskiler atılır.

        Anomali içeren pencereler asla atılmaz. Atılan sakin pencereler sayaçta
        tutulur (brifing dürüst kalsın); zaman-sorgu araçları atılmış bir
        pencereyi bulamazsa tam kayıt koşu dosyasındadır.
        """
        self.reports.append(report)
        quiet = [i for i, r in enumerate(self.reports) if _is_quiet(r)]
        excess = len(quiet) - MAX_NORMAL_REPORTS
        if excess > 0:
            drop = set(quiet[:excess])
            self.reports = [r for i, r in enumerate(self.reports) if i not in drop]
            self.dropped_quiet += excess

    def verdict(self) -> str:
        """Koşunun tek cümlelik kararı — sohbetin çıkış noktası."""
        incidents = self.incidents
        if not incidents:
            return "Kayıtta müdahale gerektiren bir olay tespit edilmedi."
        worst = max(incidents, key=lambda i: ["dusuk", "orta", "yuksek", "kritik"].index(i.risk))
        kind = TYPE_TR.get(worst.anomaly_type, worst.anomaly_type)
        review = sum(1 for i in incidents if i.needs_review)
        return (f"{len(incidents)} olay tespit edildi; en ciddisi {format_clock(worst.first_seen)} "
                f"itibarıyla {kind} ({RISK_TR.get(worst.risk, worst.risk)} risk)."
                + (f" {review} olay insan incelemesi istiyor." if review else ""))

    def briefing(self) -> str:
        """Sistem istemine gömülen tam koşu bağlamı."""
        lines = [
            f"## Çözümlenen kayıt: {self.video}",
            f"Süre: {format_clock(self.duration)} · durum: "
            f"{'analiz tamamlandı' if self.finished else 'analiz sürüyor'}",
            "",
            f"### Karar: {self.verdict()}",
        ]

        if self.incidents:
            lines += ["", "### Olay defteri"]
            for inc in self.incidents:
                kind = TYPE_TR.get(inc.anomaly_type, inc.anomaly_type)
                lines.append(
                    f"- [{inc.incident_id}] {format_clock(inc.first_seen)}–{format_clock(inc.last_seen)} · "
                    f"{kind} · risk {inc.risk} · durum {inc.phase} — {inc.title}"
                )
                for note in inc.notes:
                    lines.append(f"    · {note}")

        if self.reports:
            lines += ["", "### Pencere pencere gözlem"]
            # Sınırsız büyüme engeli (7/24 canlı akış): eski SAKİN pencereler
            # tek sayım satırına iner; anomali pencereleri yaşta bakılmaksızın
            # tam kalır, son pencereler her durumda tam kalır.
            old = self.reports[:-BRIEFING_RECENT_WINDOWS] \
                if len(self.reports) > BRIEFING_RECENT_WINDOWS else []
            recent = self.reports[len(old):]
            hidden = self.dropped_quiet + sum(1 for r in old if _is_quiet(r))
            if hidden:
                lines.append(f"(… {hidden} sakin pencere özetlendi — tam kayıt "
                             f"koşu dosyasında)")
            for r in old:
                if _is_quiet(r):
                    continue
                lines += self._report_lines(r)
            for r in recent:
                lines += self._report_lines(r)
        return "\n".join(lines)

    @staticmethod
    def _report_lines(r: WindowReport) -> list[str]:
        kind = TYPE_TR.get(r.anomaly_type, r.anomaly_type)
        lines = [f"- {format_clock(r.window_start)}–{format_clock(r.window_end)} "
                 f"({kind}): {r.summary}"]
        for e in r.events:
            lines.append(f"    · {format_clock(e.t)} [{e.severity_hint}] {e.desc}")
        for u in r.uncertainties:
            lines.append(f"    ? belirsiz: {u}")
        return lines


# Akış başına bir bağlam (çoklu-akış demo kipi); "" = tek-akış varsayılanı.
# Ekleme sırası korunur — current() en son başlayanı verir (araçların odağı).
_contexts: dict[str, RunContext] = {}


def start(run_id: str, video: str, feed: str = "", *,
          reset_chat: bool = True) -> RunContext:
    ctx = RunContext(run_id=run_id, video=video, feed=feed)
    _contexts.pop(feed, None)         # aynı akışta yeni koşu → sona taşınsın
    _contexts[feed] = ctx
    # Yeni koşu = yeni bağlam: eski kaydın sohbeti yeni kayda taşınmasın.
    # Demo başlatması N koşuyu art arda açar; sıfırlama idempotent, sorun değil.
    # İSTİSNA — canlı kip (reset_chat=False): her 30 sn'lik segment yeni bir
    # "koşu"dur; operatör sohbetini segment başına silmek sohbeti kullanılmaz
    # yapar. Canlı akışta sohbet süreklidir, bağlam segmentle tazelenir.
    if reset_chat:
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
