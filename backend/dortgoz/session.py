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

RISK_TR: dict[str, str] = {
    "dusuk": "düşük", "orta": "orta", "yuksek": "yüksek", "kritik": "kritik",
}


BRIEFING_RECENT_WINDOWS = 40
MAX_NORMAL_REPORTS = 200


def _is_quiet(r: WindowReport) -> bool:
    return r.anomaly_type == "normal" and not r.events


@dataclass
class RunContext:

    run_id: str
    video: str
    feed: str = ""
    duration: float = 0.0
    finished: bool = False
    reports: list[WindowReport] = field(default_factory=list)
    dropped_quiet: int = 0
    ledger: Ledger = field(
        default_factory=lambda: Ledger(settings.incident_grace_windows))

    @property
    def incidents(self) -> list[Incident]:
        return sorted(self.ledger.incidents.values(), key=lambda i: i.first_seen)

    def add_report(self, report: WindowReport) -> None:
        self.reports.append(report)
        quiet = [i for i, r in enumerate(self.reports) if _is_quiet(r)]
        excess = len(quiet) - MAX_NORMAL_REPORTS
        if excess > 0:
            drop = set(quiet[:excess])
            self.reports = [r for i, r in enumerate(self.reports) if i not in drop]
            self.dropped_quiet += excess

    def verdict(self) -> str:
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


_contexts: dict[str, RunContext] = {}


def start(run_id: str, video: str, feed: str = "", *,
          reset_chat: bool = True) -> RunContext:
    ctx = RunContext(run_id=run_id, video=video, feed=feed)
    _contexts.pop(feed, None)
    _contexts[feed] = ctx
    if reset_chat:
        from .agent.graph import reset_history
        reset_history()
    return ctx


def current() -> RunContext | None:
    return next(reversed(_contexts.values()), None)


def all_contexts() -> list[RunContext]:
    return list(_contexts.values())


def clear() -> None:
    _contexts.clear()
