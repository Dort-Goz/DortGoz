#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dortgoz.services import calibration

BUCKETS = [(0.0, 0.01), (0.01, 0.1), (0.1, 0.5), (0.5, 0.9), (0.9, 1.01)]


def _table(pairs: list[tuple[float, int]], cal: calibration.Calibration) -> str:
    lines = [f"{'ham aralık':>14} {'adet':>5} {'gerçek oran':>12} {'kalibre':>9}"]
    for lo, hi in BUCKETS:
        grup = [(p, y) for p, y in pairs if lo <= p < hi]
        if not grup:
            continue
        gercek = sum(y for _, y in grup) / len(grup)
        kalibre = sum(cal.apply(p) for p, _ in grup) / len(grup)
        lines.append(f"{lo:6.2f}-{hi:<6.2f} {len(grup):>5} "
                     f"{gercek:>12.2f} {kalibre:>9.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Nöbet defterinden Platt kalibrasyonu uydurur.")
    ap.add_argument("--ledger", type=Path,
                    default=ROOT / "runs" / "nobet_defteri.jsonl")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "runs" / "kalibrasyon.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="uydur ve raporla ama dosyaya yazma")
    args = ap.parse_args()

    if not args.ledger.is_file():
        print(f"defter yok: {args.ledger}", file=sys.stderr)
        raise SystemExit(2)

    pairs = calibration.pairs_from_ledger(args.ledger)
    if not pairs:
        print("defterde durum_p taşıyan karar yok", file=sys.stderr)
        raise SystemExit(2)

    try:
        cal = calibration.calibrate(pairs, now=time.time())
    except calibration.NotEnoughLabels as exc:
        print(f"kalibrasyon YAPILMADI: {exc}", file=sys.stderr)
        print("daha çok etiket gerekiyor — özellikle az olan sınıftan.",
              file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"etiket: {len(pairs)}  (pozitif {cal.n_pos} / negatif {cal.n_neg})")
    print(f"Platt : A={cal.a:.4f}  B={cal.b:.4f}")
    print(f"Brier : {cal.brier_before:.4f} -> {cal.brier_after:.4f}")
    print(f"LogLos: {cal.logloss_before:.4f} -> {cal.logloss_after:.4f}")
    print()
    print(_table(pairs, cal))

    if cal.brier_after >= cal.brier_before:
        print("\nUYARI: kalibrasyon Brier'i İYİLEŞTİRMEDİ — uygulamayın.")
    if cal.n_pos + cal.n_neg < 50:
        print(f"\nUYARI: {cal.n_pos + cal.n_neg} etiket az (tasarım ~50 diyor); "
              "katsayılar oynak, tekrar ölçmeden üretime almayın.")

    if args.dry_run:
        print("\n--dry-run: dosyaya yazılmadı")
        return
    calibration.save(cal, args.out)
    print(f"\nyazıldı: {args.out}")


if __name__ == "__main__":
    main()
