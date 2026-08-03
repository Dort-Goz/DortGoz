#!/usr/bin/env python3
"""UCF-Crime örnek kliplerini `media/` altına indirir — klipler REPOYA GİRMEZ.

Veri politikası (veri politikası): UCF-Crime lisanssız
ve yeniden dağıtım hakkı yok. Depoda yalnızca bu betik, kendi etiketlerimiz ve
atıflar bulunur; klipleri herkes kendi makinesine indirir.

Kaynak: HF aynası `backseollgi/UCF-Crime` — resmî `UCF_Crimes.zip`'in 5 parçalı
ham bölünmesi (103 GB). Parçalar HTTP Range ile sanal olarak birleştirilip
yalnız seçilen üyeler çekilir; 103 GB indirilmez (toplam ~100 MB).

İnen set aşağıdaki MANIFEST ile SABİTLENMİŞTİR: 13 anomali sınıfının her
birinden 2 + 5 normal = 31 klip. Liste, "her sınıftan 2 MB üstü en küçük 2 klip"
kuralıyla üretildi ama sabitlendi; çünkü `bench/results/` altındaki ölçümler tam
olarak bu dosyalara atıf yapıyor. Böylece ekipteki herkes birebir AYNI
değerlendirme setini alır ve kıyaslama sonuçları karşılaştırılabilir kalır.

    python scripts/fetch_ucf_samples.py            # indir
    python scripts/fetch_ucf_samples.py --list     # ne inecek, indirmeden göster

Atıf: Sultani et al., "Real-world Anomaly Detection in Surveillance Videos",
CVPR 2018 · https://www.crcv.ucf.edu/projects/real-world/
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = ("https://huggingface.co/datasets/backseollgi/UCF-Crime/resolve/main/"
        "UCF_Crimes.zip.part_")
PARTS = [f"{BASE}{i:02d}" for i in range(5)]
UA = {"User-Agent": "curl/8"}

MEDIA = Path(__file__).resolve().parents[1] / "media"

# Değerlendirme seti — SABİT LİSTE. Kural tabanlı seçim (her sınıftan 2 MB üstü
# en küçük 2 klip) bu listeyi ÜRETTİ, ama kıyaslama sonuçları (bench/results/)
# tam olarak bu dosyalara atıf yapıyor; herkesin birebir aynı seti alması için
# liste sabitlendi. Set değişecekse bench sonuçları da yenilenmeli.
MANIFEST = [
    "Abuse005_x264.mp4",
    "Abuse021_x264.mp4",
    "Arrest013_x264.mp4",
    "Arrest015_x264.mp4",
    "Arson012_x264.mp4",
    "Arson049_x264.mp4",
    "Assault020_x264.mp4",
    "Assault039_x264.mp4",
    "Burglary043_x264.mp4",
    "Burglary054_x264.mp4",
    "Explosion019_x264.mp4",
    "Explosion048_x264.mp4",
    "Fighting018_x264.mp4",
    "Fighting023_x264.mp4",
    "Normal_Videos055_x264.mp4",
    "Normal_Videos649_x264.mp4",
    "Normal_Videos_251_x264.mp4",
    "Normal_Videos_878_x264.mp4",
    "Normal_Videos_885_x264.mp4",
    "RoadAccidents041_x264.mp4",
    "RoadAccidents052_x264.mp4",
    "Robbery089_x264.mp4",
    "Robbery147_x264.mp4",
    "Shooting001_x264.mp4",
    "Shooting023_x264.mp4",
    "Shoplifting017_x264.mp4",
    "Shoplifting031_x264.mp4",
    "Stealing091_x264.mp4",
    "Stealing095_x264.mp4",
    "Vandalism006_x264.mp4",
    "Vandalism026_x264.mp4",
]


def _size(url: str) -> int:
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()
        return int(r.headers["Content-Range"].split("/")[-1])


class SplitRemoteFile(io.RawIOBase):
    """Parçalı uzak zip'i tek, seekable dosya gibi gösterir (HTTP Range)."""

    def __init__(self, urls: list[str]) -> None:
        self.urls = urls
        self.sizes = [_size(u) for u in urls]
        self.starts, off = [], 0
        for s in self.sizes:
            self.starts.append(off)
            off += s
        self.size = off
        self.pos = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = max(0, min(self.size, base + offset))
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        out = bytearray()
        while n > 0:
            i = max(j for j in range(len(self.urls)) if self.starts[j] <= self.pos)
            local = self.pos - self.starts[i]
            take = min(n, self.sizes[i] - local)
            req = urllib.request.Request(
                self.urls[i], headers={**UA, "Range": f"bytes={local}-{local + take - 1}"})
            for attempt in range(3):                  # ağ hatasında yeniden dene
                try:
                    with urllib.request.urlopen(req, timeout=300) as r:
                        chunk = r.read()
                    break
                except Exception:
                    if attempt == 2:
                        raise
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return bytes(out)


def choose(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Manifestteki dosyaları zip içinde bulur; eksik kalanı bildirir."""
    want = set(MANIFEST)
    found: dict[str, zipfile.ZipInfo] = {}
    for info in zf.infolist():
        name = info.filename.rsplit("/", 1)[-1]
        if name in want and info.filename.endswith(".mp4"):
            found[name] = info
    missing = want - found.keys()
    if missing:
        print(f"UYARI: aynada bulunamadı: {sorted(missing)}", file=sys.stderr)
    return [found[n] for n in MANIFEST if n in found]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="indirmeden listele")
    ap.add_argument("--force", action="store_true", help="var olanları yeniden indir")
    args = ap.parse_args()

    print("uzak zip dizini okunuyor (yalnız merkezî dizin — birkaç MB)…")
    zf = zipfile.ZipFile(SplitRemoteFile(PARTS))
    picked = choose(zf)
    total = sum(i.file_size for i in picked) / 1e6
    print(f"{len(picked)} klip · ~{total:.0f} MB\n")

    if args.list:
        for i in picked:
            print(f"  {i.file_size/1e6:6.1f} MB  {i.filename}")
        return

    MEDIA.mkdir(parents=True, exist_ok=True)
    for n, info in enumerate(picked, 1):
        out = MEDIA / info.filename.rsplit("/", 1)[-1]
        if out.exists() and not args.force:
            print(f"[{n}/{len(picked)}] {out.name} — zaten var, atlandı")
            continue
        with zf.open(info) as src, out.open("wb") as dst:
            while chunk := src.read(1 << 22):
                dst.write(chunk)
        print(f"[{n}/{len(picked)}] {out.name}  ({info.file_size/1e6:.1f} MB)")

    print(f"\nhazır → {MEDIA}")
    print("Klipler git tarafından yok sayılır (.gitignore: media/*) — depoya girmezler.")


if __name__ == "__main__":
    sys.exit(main())
