from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RULES_FILE = "kategori_kurallari.json"
MIN_EVIDENCE = 2


@dataclass
class CategoryRule:
    """Operatör düzeltmelerinden çıkarılmış, bir kategoriyi AYIRT EDEN ölçüt."""

    category: str
    criterion: str
    evidence: list[str] = field(default_factory=list)
    approved: bool = False
    created_at: float = 0.0
    source: str = "induced"

    @property
    def supported(self) -> bool:
        return len(self.evidence) >= MIN_EVIDENCE


def load(runs_dir: Path) -> list[CategoryRule]:
    try:
        raw = json.loads((runs_dir / RULES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[CategoryRule] = []
    for row in raw if isinstance(raw, list) else []:
        try:
            out.append(CategoryRule(**row))
        except TypeError:
            continue
    return out


def save(runs_dir: Path, rules: list[CategoryRule]) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / RULES_FILE).write_text(
        json.dumps([asdict(r) for r in rules], ensure_ascii=False, indent=1),
        encoding="utf-8")


def merge(existing: list[CategoryRule],
          proposed: list[CategoryRule]) -> list[CategoryRule]:
    """Onaylanmış kuralları KORUR; aynı kategori+ölçüt yeniden önerilmez."""
    seen = {(r.category, r.criterion.strip()) for r in existing}
    out = list(existing)
    for r in proposed:
        if (r.category, r.criterion.strip()) in seen:
            continue
        r.created_at = r.created_at or time.time()
        out.append(r)
    return out


def prompt_block(rules: list[CategoryRule]) -> str:
    """Yalnız ONAYLI ve yeterli kanıtı olan kuralları isteme yazar."""
    live = [r for r in rules if r.approved and r.supported and r.criterion.strip()]
    if not live:
        return ""
    by_cat: dict[str, list[str]] = {}
    for r in live:
        by_cat.setdefault(r.category, []).append(r.criterion.strip())
    lines = ["\n\n## Kategori ayrım ölçütleri (operatör düzeltmelerinden öğrenildi)"]
    for cat in sorted(by_cat):
        for crit in by_cat[cat]:
            lines.append(f"- `{cat}`: {crit}")
    lines.append("Bu ölçütler kategori SEÇİMİ içindir; olay olup olmadığını "
                 "değiştirmez.")
    return "\n".join(lines) + "\n"


def corrections(ledger: Path) -> list[dict]:
    """(model_category -> operator_category) düzeltmelerini döndürür."""
    try:
        text = ledger.read_text(encoding="utf-8")
    except OSError:
        return []
    latest: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("verdict") != "anomali":
            continue
        key = row.get("key") or row.get("decision_id", "")
        latest[key] = row
    out = []
    for row in latest.values():
        got, want = row.get("model_category", ""), row.get("operator_category", "")
        if want and got != want:
            out.append({
                "id": row.get("decision_id", "")[:8],
                "model": got,
                "operator": want,
                "title": row.get("title", ""),
                "note": row.get("note", ""),
                "feed": row.get("feed", ""),
            })
    return out
