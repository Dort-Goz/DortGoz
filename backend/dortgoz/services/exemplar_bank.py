from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

BANK_FILE = "gomme_bankasi.jsonl"
EVIDENCE_RE = re.compile(r"/_evidence/(?P<run>[^/]+)/(?P<name>[^/]+)\.mp4$")


@dataclass(frozen=True)
class Exemplar:
    key: str
    feed: str
    embedding: tuple[float, ...]


def key_for(run_id: str, name: str) -> str:
    return f"{run_id}/{name}"


def key_from_evidence(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    m = EVIDENCE_RE.search(url)
    return key_for(m.group("run"), m.group("name")) if m else None


def append(runs_dir: Path, key: str, feed: str,
           embedding: list[float] | None) -> None:
    if not embedding:
        return
    runs_dir.mkdir(parents=True, exist_ok=True)
    row = {"key": key, "feed": feed,
           "embedding": [round(float(v), 5) for v in embedding]}
    with (runs_dir / BANK_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def load(runs_dir: Path) -> dict[str, Exemplar]:
    path = runs_dir / BANK_FILE
    out: dict[str, Exemplar] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        emb = row.get("embedding")
        if not emb:
            continue
        out[row["key"]] = Exemplar(row["key"], row.get("feed", ""),
                                   tuple(float(v) for v in emb))
    return out


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return -1.0
    return dot / (na * nb)


def benign_exemplars(runs_dir: Path, ledger: Path) -> dict[str, list[Exemplar]]:

    bank = load(runs_dir)
    verdicts: dict[str, tuple[str, str]] = {}
    try:


        text = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = key_from_evidence(row.get("evidence"))
        if key is None or row.get("verdict") not in {"anomali", "sorun_degil"}:
            continue
        verdicts[key] = (row["verdict"], row.get("feed", ""))

    out: dict[str, list[Exemplar]] = {}
    for key, (verdict, feed) in verdicts.items():
        if verdict != "sorun_degil":
            continue
        ex = bank.get(key)
        if ex is None:
            continue
        out.setdefault(feed or ex.feed, []).append(ex)
    return out



HARD_FLOOR_CATEGORIES = frozenset({"silahli_olay", "yangin", "patlama"})
HARD_FLOOR_RISK = frozenset({"kritik"})


@dataclass(frozen=True)
class Match:
    suppress: bool
    shadow: bool
    similarity: float
    precedent: Exemplar | None
    reason: str


class Matcher:


    def __init__(self, runs_dir: Path, ledger: Path) -> None:
        self._runs_dir = runs_dir
        self._ledger = ledger
        self._stamp: float = -1.0
        self._by_feed: dict[str, list[Exemplar]] = {}

    def _refresh(self) -> None:
        try:
            stamp = self._ledger.stat().st_mtime
        except OSError:
            self._by_feed = {}
            return
        if stamp != self._stamp:
            self._by_feed = benign_exemplars(self._runs_dir, self._ledger)
            self._stamp = stamp

    def counts(self) -> dict[str, int]:
        self._refresh()
        return {feed: len(v) for feed, v in self._by_feed.items()}

    def check(self, feed: str, category: str, risk: str,
              embedding: tuple[float, ...] | None, *,
              threshold: float, enabled: bool, shadow: bool) -> Match:
        if not enabled:
            return Match(False, shadow, -1.0, None, "kapalı")
        if category in HARD_FLOOR_CATEGORIES or risk in HARD_FLOOR_RISK:
            return Match(False, shadow, -1.0, None,
                         f"sert taban: {category}/{risk} asla bastırılmaz")
        if not embedding:
            return Match(False, shadow, -1.0, None, "gömme yok")
        self._refresh()
        pool = self._by_feed.get(feed, [])
        if not pool:
            return Match(False, shadow, -1.0, None, "bu kameranın emsali yok")
        sim, hit = nearest(embedding, pool)
        if sim < threshold:
            return Match(False, shadow, sim, hit,
                         f"en yakın emsal {sim:.4f} < {threshold:.4f}")
        return Match(not shadow, shadow, sim, hit,
                     f"emsal {sim:.4f} ≥ {threshold:.4f} ({len(pool)} iyi-huylu örnek)")


def nearest(embedding: tuple[float, ...],
            exemplars: list[Exemplar]) -> tuple[float, Exemplar | None]:
    best, hit = -1.0, None
    for ex in exemplars:
        sim = cosine(embedding, ex.embedding)
        if sim > best:
            best, hit = sim, ex
    return best, hit
