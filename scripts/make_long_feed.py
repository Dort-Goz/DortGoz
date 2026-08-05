#!/usr/bin/env python3
"""Uzun, SÜREKLİ "canlı kamera" kaydı üretir — yerel UCF-Crime kopyasından.

NEDEN: değerlendirme klipleri kısa (8-64 sn) ve %100 olaylı; oysa canlı akış
hattının (pencere kuyruğu, kamera başına arka plan modeli, defter sürekliliği,
geri basınç) testi için SAATLERCE süren, çoğu ölü, olayları BİLİNEN zamanlarda
gömülü kayıt gerekir.

⭐ TEMEL BULGU (2026-08-05): UCF-Crime'ın `Training_Normal_Videos_Anomaly`
dizininde SABİT KAMERADAN ÇEKİLMİŞ ÇOK UZUN sürekli kayıtlar var —
Normal_Videos308 **9 saat**, 307 5,8 sa, 633 4 sa, ayrıca 1-2 saatlik onlarca
kayıt. Görsel doğrulama (9 saate yayılmış 5 kare): tek sahne, tek açı, kesim
YOK, doğal etkinlik dalgalanması (boş → kalabalık → boş). Yani "canlı akış
gibi kesintisiz görüntü" için sentez GEREKMİYOR; taban kayıt zaten elimizde.
Bu betik o kayıttan sürekli bir dilim alır ve isteğe bağlı olarak BİLİNEN
zamanlara anomali klipleri yerleştirir (tek kesim noktası olay sınırlarıdır).

Üçüncü taraf "açık kamera" akışlarına (insecam vb.) göre: gerçek referansı var
(yakalama ölçülebilir), tekrarlanabilir (aynı tohum = aynı kayıt), çevrim dışı
çalışır (final fiziki/hava boşluklu), rıza-lisans sorunu yaratmaz — klipler
makineden çıkmaz (bkz. datasets_S3.md veri politikası).

    python scripts/make_long_feed.py --minutes 30 --events 3
    python scripts/make_long_feed.py --minutes 60 --pure          # saf ölü hava
    python scripts/make_long_feed.py --minutes 20 --cameras 4     # kamera duvarı
    python scripts/make_long_feed.py --list                       # uzun kayıt envanteri

UCF-Crime kopyasının YERİ AYARLANABİLİR (herkesin diski farklı) — öncelik sırası:
    1. `--ucf /yol/UCF_Crimes`
    2. `DORTGOZ_UCF_DIR` ortam değişkeni
    3. kök dizindeki `.env` içindeki `DORTGOZ_UCF_DIR` (backend ile aynı dosya)
    4. bilinen varsayılan yollar
`UCF_Crimes` de `UCF_Crimes/Videos` de verilebilir; ikisi de kabul edilir.
Klipler kopyalanmaz, yalnız okunur — veri seti diskinde kalır (repoya girmez).
Atıf: Sultani et al., CVPR 2018.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "media"
ENV_KEY = "DORTGOZ_UCF_DIR"
# Son çare varsayılanlar — hiçbiri yoksa betik ne ayarlanacağını söyleyip çıkar
DEFAULT_UCF = [
    Path("~/datasets/Dort_Goz/UCF_Crimes"),
    Path.home() / "Datasets/UCF_Crimes",
]
UCF = Path()      # resolve_ucf() ile doldurulur (main/--ucf/env/.env)
NORMAL_DIRS = ["Testing_Normal_Videos_Anomaly", "Training_Normal_Videos_Anomaly"]
# Kapsam: A1 sınıf listesine karşılık gelen UCF dizinleri (Shoplifting/Stealing
# dahil değil — bench'te 0/2 yakalama, gömülü olayın görünürlüğü tartışmalı)
ANOMALY_DIRS = ["Fighting", "Assault", "Abuse", "Explosion", "Arson",
                "RoadAccidents", "Vandalism", "Burglary", "Robbery"]

WIDTH, HEIGHT, FPS = 320, 240, 15   # UCF anaakımı; ~80 prompt token/kare


def _env_file_value(key: str) -> str | None:
    """Kökteki `.env`'den tek anahtar okur (betik stdlib-only; pydantic yok)."""
    env = ROOT / ".env"
    if not env.is_file():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_ucf(cli: Path | None) -> Path:
    """Veri seti kökünü çözer ve `Videos/` dizinini döndürür.

    `UCF_Crimes` ya da `UCF_Crimes/Videos` verilebilir — ikisi de kabul edilir,
    böylece kimse hangi seviyenin isteneceğini hatırlamak zorunda kalmaz.
    """
    explicit = [(Path(src).expanduser(), label) for src, label in (
        (cli, "--ucf"), (os.environ.get(ENV_KEY), ENV_KEY),
        (_env_file_value(ENV_KEY), f".env:{ENV_KEY}")) if src]

    def try_base(base: Path) -> Path | None:
        for path in (base / "Videos", base):
            if (path / "Training_Normal_Videos_Anomaly").is_dir():
                return path
        return None

    # Açıkça verilen yol YANLIŞSA sessizce varsayılana düşme — yazım hatası
    # başka bir makinede fark edilmeden yanlış veri setiyle çalışmaya yol açar
    if explicit:
        base, label = explicit[0]
        found = try_base(base)
        if found:
            return found
        raise SystemExit(
            f"{label} ile verilen yol UCF-Crime kopyası değil: {base}\n"
            "Beklenen içerik: <yol>/Videos/Training_Normal_Videos_Anomaly/")

    for base in DEFAULT_UCF:
        if (found := try_base(base)):
            return found

    tried = "\n  ".join(str(c) for c in DEFAULT_UCF)
    raise SystemExit(
        "UCF-Crime kopyası bulunamadı. Denenen varsayılan yollar:\n  " + tried +
        f"\n\nVeri setinin yerini bildir (biri yeterli):\n"
        f"  scripts/make_long_feed.py --ucf /disk/yolu/UCF_Crimes\n"
        f"  {ENV_KEY}=/disk/yolu/UCF_Crimes  (ortam değişkeni)\n"
        f"  .env dosyasına: {ENV_KEY}=/disk/yolu/UCF_Crimes\n"
        "Beklenen içerik: <yol>/Videos/Training_Normal_Videos_Anomaly/"
    )


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out) if out else 0.0


def normalize(src: Path, dst: Path, ss: float | None = None,
              t: float | None = None) -> float:
    """Tek tip çözünürlük/fps/kodek — concat'in `-c copy` ile güvenli olması için.

    `-ss` girişten ÖNCE (hızlı arama), `-t` sonra: uzun kaynaktan dilim alırken
    9 saatlik dosyayı baştan çözmemek için.
    """
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", str(src)]
    if t is not None:
        cmd += ["-t", f"{t:.3f}"]
    cmd += ["-vf", f"scale={WIDTH}:{HEIGHT},fps={FPS},setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-an", "-g", str(FPS * 2), str(dst)]
    subprocess.run(cmd, check=True)
    return duration(dst)


def long_normals(minimum: float = 1800.0) -> list[tuple[Path, float]]:
    """Sabit kameradan çekilmiş UZUN sürekli kayıtlar (varsayılan ≥30 dk).

    Dosya boyutu süreyle orantılı olduğu için önce boyuta göre elenir —
    950 normal videonun tamamını ffprobe'lamak gereksiz.
    """
    cands = sorted((p for d in NORMAL_DIRS for p in (UCF / d).glob("*.mp4")),
                   key=lambda p: p.stat().st_size, reverse=True)[:20]
    out = [(p, duration(p)) for p in cands]
    return [(p, d) for p, d in out if d >= minimum]


def pick_anomalies(rng: random.Random, n: int) -> list[tuple[Path, str]]:
    """Sınıf çeşitliliğiyle anomali klibi seç."""
    picks, dirs = [], rng.sample(ANOMALY_DIRS, min(max(n, 1), len(ANOMALY_DIRS)))
    while len(picks) < n:
        pool = list((UCF / dirs[len(picks) % len(dirs)]).glob("*.mp4"))
        if pool:
            picks.append((rng.choice(pool), dirs[len(picks) % len(dirs)]))
    return picks


def build(out: Path, minutes: float, n_events: int, seed: int) -> dict:
    """Tek sahneli sürekli taban + bilinen zamanlara gömülü anomaliler."""
    rng = random.Random(seed)
    bases = long_normals()
    if not bases:
        raise SystemExit("yerel kopyada ≥30 dk'lık sürekli normal kayıt yok")
    base, base_dur = bases[seed % len(bases)]
    need = minutes * 60
    # Kaydın başı/sonu yerine rastgele bir noktadan başla (her kamera farklı saat)
    start = rng.uniform(0, max(0.0, base_dur - need - 60)) if base_dur > need + 60 else 0.0
    anomalies = pick_anomalies(rng, n_events)

    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        # Taban, olay sayısı kadar sürekli parçaya bölünür; kesim YALNIZ olay sınırında
        chunk = need / (len(anomalies) + 1)
        listing, truth, offset, cursor = [], [], 0.0, start
        for i in range(len(anomalies) + 1):
            seg = tmpd / f"base{i:03d}.mp4"
            dur = normalize(base, seg, ss=cursor, t=chunk)
            if dur <= 0:
                continue
            listing.append(f"file '{seg}'")
            offset += dur
            cursor += dur
            if i < len(anomalies):
                apath, acls = anomalies[i]
                aseg = tmpd / f"anom{i:03d}.mp4"
                adur = normalize(apath, aseg)
                if adur > 0:
                    listing.append(f"file '{aseg}'")
                    truth.append({"start": round(offset, 2),
                                  "end": round(offset + adur, 2),
                                  "class": acls, "source": apath.name})
                    offset += adur

        concat = tmpd / "list.txt"
        concat.write_text("\n".join(listing), encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(concat), "-c", "copy", str(out)], check=True)

    meta = {
        "video": out.name,
        "duration": round(duration(out), 2),
        "seed": seed,
        "base": {"source": base.name, "start": round(start, 2),
                 "note": "sabit kamera, tek sahne, kesintisiz"},
        "events": truth,
        "note": "scripts/make_long_feed.py. Taban tek sahnedir; TEK kesim noktası "
                "olay sınırlarıdır (arka plan modeli orada ~50 sn toparlanır).",
    }
    out.with_suffix(".truth.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--events", type=int, default=4, help="gömülecek anomali sayısı")
    ap.add_argument("--cameras", type=int, default=1, help="N ayrı kayıt üret (kamera duvarı)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--pure", action="store_true",
                    help="olay gömme — saf sürekli ölü hava (akış/kuyruk testi)")
    ap.add_argument("--list", action="store_true",
                    help="yerel uzun sürekli kayıt envanterini göster ve çık")
    ap.add_argument("--ucf", type=Path,
                    help=f"UCF-Crime kopyasının yolu (yoksa {ENV_KEY} / .env / varsayılan)")
    ap.add_argument("--out", type=Path, help="tek kamera için çıktı yolu")
    args = ap.parse_args()

    global UCF
    UCF = resolve_ucf(args.ucf)
    print(f"veri seti: {UCF}")

    if args.list:
        for path, dur in long_normals(600.0):
            print(f"{dur/60:8.1f} dk  {path.name}")
        return

    for cam in range(1, args.cameras + 1):
        out = args.out if (args.out and args.cameras == 1) \
            else MEDIA / f"kamera{cam:02d}_{int(args.minutes)}dk.mp4"
        meta = build(out, args.minutes, 0 if args.pure else args.events, args.seed + cam)
        ev = ", ".join(f"{e['class']}@{e['start']:.0f}s" for e in meta["events"]) or "yok"
        print(f"{out.name}: {meta['duration']/60:.1f} dk, taban {meta['base']['source']}"
              f" @{meta['base']['start']:.0f}s, {len(meta['events'])} olay [{ev}]")
        print(f"  gerçek referans: {out.with_suffix('.truth.json').name}")


if __name__ == "__main__":
    main()
