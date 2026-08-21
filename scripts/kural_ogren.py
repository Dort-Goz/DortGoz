#!/usr/bin/env python3
"""Operatör kategori düzeltmelerinden AYIRT EDİCİ ölçüt çıkarır.

Kare eşleştirmez; kategorinin TANIMINI öğrenir. Örnek: 'kavga' yerine
'silahli_olay' düzeltmesi tekrar ediyorsa, silahın baskın olduğu ölçütünü yazar.

Çıkan kurallar ONAYSIZ yazılır; operatör onaylamadan isteme girmez.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dortgoz.agent.llm import create_chat, main_client  # noqa: E402
from dortgoz.config import settings  # noqa: E402
from dortgoz.services import category_rules  # noqa: E402

SYSTEM = (
    "Sen bir güvenlik video analiz sisteminin sınıflandırma ölçütlerini yazan "
    "uzmansın. Sana bir modelin YANLIŞ kategori tahminleri ve operatörün DOĞRU "
    "kategorisi veriliyor. Görevin, o doğru kategoriyi diğerlerinden AYIRAN kısa "
    "ve GÖZLEMLENEBİLİR bir ölçüt yazmak.\n"
    "Kurallar: tek cümle; Türkçe; yalnız karede görülebilecek şeye dayan; "
    "hangi kategorinin ne zaman ÖNCELİKLİ olduğunu söyle; klip adı, kamera adı "
    "veya tek bir olaya özgü ayrıntı KULLANMA — kural genel olmalı."
)

TASK = (
    "Aşağıda hep aynı doğru kategoriye ({hedef}) ait düzeltmeler var. "
    "Model bunları yanlışlıkla başka kategori sanmış.\n\n{ornekler}\n\n"
    "Bu kategoriyi ayıran TEK bir ölçüt cümlesi yaz. Yalnız cümleyi döndür."
)


async def induce(hedef: str, rows: list[dict], model: str) -> str:
    ornekler = "\n".join(
        f"- model '{r['model']}' dedi, operatör '{r['operator']}' dedi. "
        f"Başlık: {r['title'][:120]}" for r in rows[:8])
    resp = await create_chat(
        main_client(),
        model=model or settings.main_model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": TASK.format(hedef=hedef,
                                                          ornekler=ornekler)}],
        temperature=0.0,
        max_tokens=400,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    text = (resp.choices[0].message.content or "").strip().strip('"')
    return text.splitlines()[0].strip() if text else ""


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path,
                    default=ROOT / "runs" / "nobet_defteri.jsonl")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs")
    ap.add_argument("--model", default="")
    ap.add_argument("--min", type=int, default=category_rules.MIN_EVIDENCE,
                    help="bir kategori için gereken en az düzeltme sayısı")
    ap.add_argument("--seed", action="store_true",
                    help="taksonomi taban ölçütlerini kur (model çağırmaz)")
    args = ap.parse_args()

    if args.seed:
        seeds = category_rules.seed_rules(ROOT / "defaults")
        if not seeds:
            print("defaults/kategori_olcutleri.json okunamadı", file=sys.stderr)
            return 2
        merged = category_rules.merge(category_rules.load(args.runs), seeds)
        category_rules.save(args.runs, merged)
        for r in seeds:
            print(f"  {r.category:14} {r.criterion[:88]}…")
        print(f"\n{len(seeds)} taban ölçüt ONAYSIZ yazıldı: "
              f"{args.runs / category_rules.RULES_FILE}")
        return 0

    corr = category_rules.corrections(args.ledger)
    if not corr:
        print("defterde kategori düzeltmesi yok", file=sys.stderr)
        return 2

    by_target: dict[str, list[dict]] = {}
    for row in corr:
        by_target.setdefault(row["operator"], []).append(row)

    print(f"{len(corr)} düzeltme, {len(by_target)} hedef kategori")
    proposed: list[category_rules.CategoryRule] = []
    for hedef, rows in sorted(by_target.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < args.min:
            print(f"  {hedef:14} {len(rows)} düzeltme — atlandı (< {args.min})")
            continue
        criterion = await induce(hedef, rows, args.model)
        if not criterion:
            print(f"  {hedef:14} ölçüt üretilemedi")
            continue
        proposed.append(category_rules.CategoryRule(
            category=hedef, criterion=criterion,
            evidence=[r["id"] for r in rows]))
        print(f"\n  {hedef}  ({len(rows)} düzeltme)\n    → {criterion}")

    if not proposed:
        print("\nyeterli kanıtı olan kategori yok")
        return 1

    merged = category_rules.merge(category_rules.load(args.runs), proposed)
    category_rules.save(args.runs, merged)
    print(f"\n{len(proposed)} kural ONAYSIZ yazıldı: {args.runs / category_rules.RULES_FILE}")
    print("Onaylamak için ilgili kaydın \"approved\" alanını true yapın.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
