#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = (
    "https://raw.githubusercontent.com/Xuange923/"
    "Surveillance-Video-Understanding/main/UCF%20Annotation/json/"
)
UA = {"User-Agent": "curl/8"}

DEST = Path(__file__).resolve().parents[1] / "data" / "uca"

FILES = {
    "UCFCrime_Train.json": "0df6a6e5e1e1b17b87e229edb865c9dea3e23505fd29256a894660687ff4eba2",
    "UCFCrime_Val.json": "f93720c911d0bfb3e8da168c7da43538227f0edcf90258a407856913fcacf559",
    "UCFCrime_Test.json": "8912bc02c1871e177ffc7f45ed54395c0e34e1a8007a474b91309ce8a816f981",
}


def fetch(name: str, expected: str) -> bool:
    target = DEST / name
    if target.exists():
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest == expected:
            print(f"{name}: mevcut, hash dogru")
            return True
        print(f"{name}: mevcut ama hash farkli, yeniden indiriliyor")
    req = urllib.request.Request(BASE + name, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        print(f"{name}: HASH UYUSMAZLIGI beklenen={expected} gelen={digest}")
        return False
    target.write_bytes(data)
    print(f"{name}: indirildi ve dogrulandi ({len(data)} bayt)")
    return True


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    ok = all([fetch(name, expected) for name, expected in FILES.items()])
    if ok:
        print("UCA anotasyonlari hazir. Atif: data/uca/CITATION.bib")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
