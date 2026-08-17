#!/usr/bin/env python3
"""Kolları yan yana koyar: yakalama, maliyet, şiddet karışımı ve TAMAMLAYICILIK.

`ab_pipeline.py --analyze` her kolu TEK BAŞINA raporlar. Karar için gereken şey
kolların BİRBİRİNE göre durumu: hangi klibi kim yakalıyor, kim kimin kör noktasını
kapatıyor. 2026-08-15 rol analizi bunu elle yaptı; burada tekrarlanabilir hâli.

Puanlama kuralları ab_pipeline ile AYNI kaynaktan gelir (orta+ = gerçek olay).

Kullanım:
  cd backend && uv run python ../bench/efor_karsilastir.py \\
      ../bench/results/ab_ctrl35b_20260815.jsonl:35B-taban \\
      ../bench/results/ab_qwen38dg_20260815.jsonl:27B-düşünmesiz \\
      ../bench/results/efor_low.jsonl:27B-low
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ab_pipeline import (  # noqa: E402
    ALARM_FLOOR,
    clip_class,
    load_results,
    window_severity,
)


def kol_ozeti(yol: Path) -> dict:
    data = load_results(yol)
    cfg = data.get("config", {})
    yakalanan, anomali, yanlis, normal = set(), set(), set(), set()
    gpu, derin, siddetler = 0.0, 0, []
    for c in data["clips"]:
        if "error" in c:
            continue
        ad = c["clip"]
        anomali_mi = c.get("anomaly", not clip_class(Path(ad)).startswith("Normal"))
        olayli = False
        for w in c.get("windows", []):
            if w.get("gated"):
                continue                 # kapıda düşen pencere derin okunmadı
            s = window_severity(w)
            if s >= 0:
                siddetler.append(s)
            if s >= ALARM_FLOOR:
                olayli = True
            gpu += w.get("deep_s", 0.0)
            derin += 1
        if not anomali_mi:
            normal.add(ad)
            if olayli:
                yanlis.add(ad)
        else:
            anomali.add(ad)
            if olayli:
                yakalanan.add(ad)
    return {"ad": yol.stem, "cfg": cfg, "yakalanan": yakalanan, "anomali": anomali,
            "yanlis": yanlis, "normal": normal, "gpu": gpu, "derin": derin,
            "siddet": siddetler}


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    kollar = []
    for arg in sys.argv[1:]:
        yol, _, etiket = arg.partition(":")
        k = kol_ozeti(Path(yol))
        k["etiket"] = etiket or k["ad"]
        kollar.append(k)

    print("| Kol | model | kademe | yakalama | yanlış alarm | GPU-sn | derin pencere |")
    print("|---|---|---|---|---|---|---|")
    for k in kollar:
        cfg = k["cfg"]
        print(f"| {k['etiket']} | {cfg.get('model','?')} | "
              f"{cfg.get('interpret_effort','') or '—'} | "
              f"{len(k['yakalanan'])}/{len(k['anomali'])} | "
              f"{len(k['yanlis'])}/{len(k['normal'])} | {k['gpu']:.0f} | {k['derin']} |")

    print("\n## Tamamlayıcılık — hangi klibi kim yakalıyor")
    print("\n| Kol çifti | yalnız A | yalnız B | ikisi | hiçbiri |")
    print("|---|---|---|---|---|")
    for i, a in enumerate(kollar):
        for b in kollar[i + 1:]:
            ortak = a["anomali"] & b["anomali"]
            ya = (a["yakalanan"] - b["yakalanan"]) & ortak
            yb = (b["yakalanan"] - a["yakalanan"]) & ortak
            iki = a["yakalanan"] & b["yakalanan"] & ortak
            hic = ortak - a["yakalanan"] - b["yakalanan"]
            print(f"| {a['etiket']} ↔ {b['etiket']} | {len(ya)} | {len(yb)} | "
                  f"{len(iki)} | {len(hic)} |")
            if ya:
                print(f"|   ↳ yalnız {a['etiket']} | {', '.join(sorted(ya))} | | | |")
            if yb:
                print(f"|   ↳ yalnız {b['etiket']} | {', '.join(sorted(yb))} | | | |")

    print("\n## Şiddet karışımı (pencere sayısı)")
    print("\n| Kol | dusuk | orta | yuksek | kritik |")
    print("|---|---|---|---|---|")
    for k in kollar:
        sayim = [sum(1 for s in k["siddet"] if s == i) for i in range(4)]
        print(f"| {k['etiket']} | " + " | ".join(str(x) for x in sayim) + " |")

    # Birleşim üst sınırı: kolların TAMAMI kullanılsa yakalama ne olurdu
    birlesim: set[str] = set()
    tum_anomali: set[str] = set()
    for k in kollar:
        birlesim |= k["yakalanan"]
        tum_anomali |= k["anomali"]
    print(f"\n**Birleşim (üst sınır, tüm kollar):** {len(birlesim)}/{len(tum_anomali)}")


if __name__ == "__main__":
    main()
