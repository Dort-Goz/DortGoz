from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.metadata import Distribution, DistributionFinder
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DENYLIST = re.compile(
    r"\b(AGPL|GPL-[23]|GPLv[23]|LGPL|SSPL|CC-BY-NC|Commons Clause|BUSL|Elastic-2)",
    re.IGNORECASE,
)
GPL_EXCEPTION = re.compile(r"GPL[^,;]{0,40}exception", re.IGNORECASE)


def _python_site_packages() -> Path | None:
    for candidate in sorted((ROOT / "backend" / ".venv" / "lib").glob("python*")):
        site = candidate / "site-packages"
        if site.is_dir():
            return site
    return None


def _license_of(distribution: Distribution) -> str:
    metadata = distribution.metadata
    for key in ("License-Expression", "License"):
        value = (metadata.get(key) or "").strip()
        if value and len(value) <= 80 and "\n" not in value:
            return value
    classifiers = [
        item for item in (metadata.get_all("Classifier") or []) if item.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(item.split(":: ")[-1] for item in classifiers)
    return "UNKNOWN"


def python_components() -> list[dict[str, str]]:
    site = _python_site_packages()
    if site is None:
        return []
    context = DistributionFinder.Context(path=[str(site)])
    components = []
    for distribution in Distribution.discover(context=context):
        name = distribution.metadata["Name"]
        if not name:
            continue
        components.append(
            {
                "name": name,
                "version": distribution.version,
                "license": _license_of(distribution),
                "ecosystem": "pypi",
            }
        )
    return sorted(components, key=lambda item: item["name"].lower())


def node_components() -> list[dict[str, str]]:
    modules = ROOT / "frontend" / "node_modules"
    if not modules.is_dir():
        return []
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for manifest in modules.rglob("package.json"):
        relative = manifest.relative_to(modules).parts
        if "node_modules" in relative[:-1]:
            continue
        scoped = len(relative) == 3 and relative[0].startswith("@")
        if not (len(relative) == 2 or scoped):
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name, version = payload.get("name"), payload.get("version")
        if not name or not version:
            continue
        licence = payload.get("license")
        if not licence and isinstance(payload.get("licenses"), list):
            licence = "; ".join(
                str(item.get("type", "")) for item in payload["licenses"] if isinstance(item, dict)
            )
        seen[(name, version)] = {
            "name": name,
            "version": version,
            "license": str(licence or "UNKNOWN"),
            "ecosystem": "npm",
        }
    return sorted(seen.values(), key=lambda item: item["name"].lower())


def denied(components: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        component
        for component in components
        if DENYLIST.search(component["license"]) and not GPL_EXCEPTION.search(component["license"])
    ]


def unknown(components: list[dict[str, str]]) -> list[dict[str, str]]:
    return [component for component in components if component["license"] == "UNKNOWN"]


def cyclonedx(components: list[dict[str, str]]) -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "dortgoz",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            }
        },
        "components": [
            {
                "type": "library",
                "name": component["name"],
                "version": component["version"],
                "purl": f"pkg:{component['ecosystem']}/{component['name']}@{component['version']}",
                "licenses": [{"license": {"name": component["license"]}}],
            }
            for component in components
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    components = python_components() + node_components()
    if not components:
        print("SBOM: bağımlılık ağacı bulunamadı (uv sync / bun install çalıştırın)")
        return 1

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(cyclonedx(components), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"SBOM yazıldı: {args.out} ({len(components)} bileşen)")

    blocked = denied(components)
    missing = unknown(components)
    for component in blocked:
        print(f"YASAK LİSANS: {component['name']} {component['version']} — {component['license']}")
    for component in missing:
        print(f"LİSANS BİLİNMİYOR: {component['name']} {component['version']}")

    if args.check:
        if blocked:
            print(f"LİSANS KAPISI BAŞARISIZ: {len(blocked)} yasak bileşen")
            return 1
        print(f"LİSANS KAPISI TAMAM: {len(components)} bileşen, yasak lisans yok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
