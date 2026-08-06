"""Önceden üretilmiş Docker imajlarından air-gap bundle oluşturur.

Bu araç image build veya model indirmez. Ağ erişimi olan hazırlık makinesinde
yalnızca zaten oluşturulmuş imajları ve Git'te izlenen kaynak ağacını taşınabilir
bir klasöre koyar; hedef makine bundle'ı yükledikten sonra ``--no-build`` ile
çalıştırır.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

from verify_offline import verify

DEFAULT_IMAGES = ("dortgoz-api:local", "dortgoz-frontend:local")


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _hashes(bundle: Path) -> None:
    rows: list[str] = []
    for path in sorted(item for item in bundle.rglob("*") if item.is_file() and item.name != "SHA256SUMS"):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.relative_to(bundle).as_posix()}")
    (bundle / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _copy_release_metadata(root: Path, bundle: Path) -> None:
    for relative in ("docker-compose.yml", ".env.example", "models/MANIFEST.json", "THIRD_PARTY_NOTICES.md"):
        destination = bundle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
    documentation = bundle / "docs"
    documentation.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "docs" / "OFFLINE_INSTALL.md", documentation / "OFFLINE_INSTALL.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="oluşturulacak boş bundle klasörü")
    parser.add_argument("--image", action="append", dest="images", help="kaydedilecek Docker image etiketi")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = args.output.resolve()
    images = tuple(args.images) if args.images else DEFAULT_IMAGES

    errors = verify(root)
    if errors:
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit("offline girdileri doğrulanmadı")
    if bundle.exists():
        raise SystemExit(f"çıktı zaten var, üzerine yazılmayacak: {bundle}")

    try:
        _run(["docker", "image", "inspect", *images], cwd=root)
    except FileNotFoundError as exc:
        raise SystemExit("Docker CLI bulunamadı; bundle oluşturulamadı") from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit("gerekli Docker imajları önce build edilmelidir") from exc

    bundle.mkdir(parents=True)
    try:
        _copy_release_metadata(root, bundle)
        _run(["git", "archive", "--format=tar", "--output", str(bundle / "source.tar"), "HEAD"], cwd=root)
        images_dir = bundle / "images"
        images_dir.mkdir()
        _run(["docker", "save", "--output", str(images_dir / "dortgoz-images.tar"), *images], cwd=root)
        _hashes(bundle)
    except Exception:
        shutil.rmtree(bundle, ignore_errors=True)
        raise
    print(f"offline bundle hazır: {bundle}")


if __name__ == "__main__":
    main()
