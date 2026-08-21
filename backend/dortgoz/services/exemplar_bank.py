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


def key_from_evidence(url: str | None) -> str | None:
    if not url:
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
        text = path.read_text(encoding="utf-8")
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
    """Kamera başına 'sorun değil' denmiş tespitlerin gömmeleri."""
    bank = load(runs_dir)
    verdicts: dict[str, tuple[str, str]] = {}
    try:
        text = ledger.read_text(encoding="utf-8")
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


def nearest(embedding: tuple[float, ...],
            exemplars: list[Exemplar]) -> tuple[float, Exemplar | None]:
    best, hit = -1.0, None
    for ex in exemplars:
        sim = cosine(embedding, ex.embedding)
        if sim > best:
            best, hit = sim, ex
    return best, hit
