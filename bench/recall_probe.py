#!/usr/bin/env python3
"""Sınıf bazlı recall ölçümü: etiketli klipleri doğrudan VLM'e verir.

`kalite_kapisi` on kliple sınırlıdır ve tek klip bir recall ölçümü değildir.
Bu prob bir sınıfın klasöründen örneklem alır, her klibi `interpret_window` ile
okur ve "dikkat" dönme oranını verir. Negatif kontrol için Normal_Videos da
alınabilir; yanlış alarm oranını aynı koşuda görürsünüz.

    python bench/recall_probe.py --dizin /.../RoadAccidents --sayi 20 \
        --kontrol /.../Testing_Normal_Videos --kontrol-sayi 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dortgoz.config import settings
from dortgoz.pipeline import ingest
from dortgoz.pipeline.interpret import interpret_window

PENCERE = 30.0


def anotasyonlari_oku(yol: Path, fps: float = 30.0) -> dict[str, tuple[float, float]]:
    """UCF-Crime zamansal anotasyonu: dosya, sinif, baslangic/bitis KARESI.

    Yalnız test bölümünü kapsar (RoadAccidents için 23 klip). Anotasyon olmadan
    ilk 30 saniyeye bakmak, olay geç oluyorsa OLMAYAN bir kaçırma sayar.
    """
    out: dict[str, tuple[float, float]] = {}
    for line in yol.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2].lstrip("-").isdigit():
            bas, bit = int(parts[2]), int(parts[3])
            if bas >= 0 and bit > bas:
                out[parts[0]] = (bas / fps, bit / fps)
    return out


async def _bir(klip: Path, aralik: tuple[float, float] | None = None) -> dict:
    stats: dict = {}
    t0 = time.perf_counter()
    try:
        sure = await ingest.probe_duration(klip)
    except Exception as exc:
        return {"klip": klip.name, "hata": f"sure: {str(exc)[:60]}"}
    if aralik is not None:
        # Olayi ortalayan pencere; olayin pencerede OLDUGU garanti edilir.
        orta = (aralik[0] + aralik[1]) / 2
        bas = max(0.0, min(orta - PENCERE / 2, sure - PENCERE))
        bas = max(0.0, bas)
        end = round(min(bas + PENCERE, sure - 0.4), 1)
        if end - bas <= 1.0:
            return {"klip": klip.name, "hata": "pencere kurulamadi"}
        keys = [bas + (end - bas) * (j + 0.5) / 6 for j in range(6)]
        try:
            rapor = await interpret_window(klip, (bas, end), keys, stats=stats)
        except Exception as exc:
            return {"klip": klip.name, "hata": f"{type(exc).__name__}: {str(exc)[:60]}"}
        return {"klip": klip.name, "sure": round(sure, 1),
                "pencere": [bas, end], "olay_araligi": list(aralik),
                "durum_p": stats.get("durum_p"),
                "anomaly_type": rapor.anomaly_type, "olay": len(rapor.events),
                "siddet": sorted({e.severity_hint for e in rapor.events}),
                "sn": round(time.perf_counter() - t0, 1)}
    end = round(min(sure - 0.4, PENCERE), 1)
    if end <= 1.0:
        return {"klip": klip.name, "hata": f"klip cok kisa: {sure:.1f} sn"}
    keys = [end * (j + 0.5) / 6 for j in range(6)]
    try:
        rapor = await interpret_window(klip, (0.0, end), keys, stats=stats)
    except Exception as exc:
        return {"klip": klip.name, "hata": f"{type(exc).__name__}: {str(exc)[:60]}"}
    return {
        "klip": klip.name,
        "sure": round(sure, 1),
        "durum_p": stats.get("durum_p"),
        "anomaly_type": rapor.anomaly_type,
        "olay": len(rapor.events),
        "siddet": sorted({e.severity_hint for e in rapor.events}),
        "sn": round(time.perf_counter() - t0, 1),
    }


def _dikkat(satir: dict) -> bool:
    """Operatore ulasir mi: olay yazildi ya da pencere anormal isaretlendi."""
    return bool(satir.get("olay")) or satir.get("anomaly_type") not in (None, "normal")


async def _kume(ad: str, klipler: list[Path], esz: int,
                anot: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    sem = asyncio.Semaphore(esz)

    async def gorev(k: Path) -> dict:
        async with sem:
            return await _bir(k, (anot or {}).get(k.name))

    print(f"\n=== {ad}: {len(klipler)} klip ===")
    satirlar = await asyncio.gather(*(gorev(k) for k in klipler))
    for s in satirlar:
        if "hata" in s:
            print(f"  {s['klip'][:34]:34} HATA {s['hata']}")
            continue
        p = s["durum_p"]
        print(f"  {s['klip'][:34]:34} durum_p={p if p is None else round(p, 5)!s:>9} "
              f"{s['anomaly_type']:12} olay={s['olay']} {s['sn']}s")
    return satirlar


def _ozet(ad: str, satirlar: list[dict], pozitif_beklenir: bool) -> None:
    gecerli = [s for s in satirlar if "hata" not in s]
    if not gecerli:
        return print(f"{ad}: olculebilir klip yok")
    isaretli = [s for s in gecerli if _dikkat(s)]
    oran = len(isaretli) / len(gecerli)
    etiket = "RECALL (yakalanan)" if pozitif_beklenir else "YANLIS ALARM"
    print(f"\n{ad} -> {etiket}: {len(isaretli)}/{len(gecerli)} = %{oran*100:.0f}")
    tipler: dict[str, int] = {}
    for s in isaretli:
        tipler[s["anomaly_type"]] = tipler.get(s["anomaly_type"], 0) + 1
    if tipler:
        print(f"  isaretlenenlerin sinifi: {tipler}")
    if pozitif_beklenir:
        kacan = [s["klip"] for s in gecerli if not _dikkat(s)]
        print(f"  KACAN {len(kacan)}: {', '.join(k[:26] for k in kacan[:8])}")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dizin", type=Path, required=True)
    ap.add_argument("--sayi", type=int, default=20)
    ap.add_argument("--kontrol", type=Path)
    ap.add_argument("--kontrol-sayi", type=int, default=10)
    ap.add_argument("--esz", type=int, default=3)
    ap.add_argument("--anotasyon", type=Path,
                    help="UCF Temporal_Anomaly_Annotation.txt; verilirse YALNIZ "
                         "anotasyonlu klipler ve olayi ortalayan pencere kullanilir")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    anot = anotasyonlari_oku(a.anotasyon) if a.anotasyon else None

    def sec(d: Path, n: int, filtre: bool = False) -> list[Path]:
        hepsi = sorted(d.glob("*.mp4"))
        if filtre and anot:
            hepsi = [p for p in hepsi if p.name in anot]
        if not hepsi:
            return []
        adim = max(1, len(hepsi) // n)          # deterministik, dagilmis ornek
        return hepsi[::adim][:n]

    pozitif = await _kume(a.dizin.name, sec(a.dizin, a.sayi, filtre=True),
                          a.esz, anot)
    _ozet(a.dizin.name, pozitif, True)
    kontrol: list[dict] = []
    if a.kontrol:
        kontrol = await _kume(a.kontrol.name, sec(a.kontrol, a.kontrol_sayi), a.esz)
        _ozet(a.kontrol.name, kontrol, False)

    if a.out:
        a.out.write_text(json.dumps({"pozitif": pozitif, "kontrol": kontrol},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nyazıldı: {a.out}")


asyncio.run(main())
