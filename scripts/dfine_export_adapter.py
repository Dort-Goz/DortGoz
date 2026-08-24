#!/usr/bin/env python3


from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _load_official_exporter(path: Path) -> ModuleType:
    exporter = path.resolve()
    if exporter.is_symlink() or not exporter.is_file():
        raise RuntimeError("official D-FINE exporter bulunamadı veya güvensiz")
    spec = importlib.util.spec_from_file_location(
        "dortgoz_official_dfine_export", exporter
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("official D-FINE exporter yüklenemedi")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "YAMLConfig", None)) or not callable(
        getattr(module, "main", None)
    ):
        raise TypeError("official D-FINE exporter sözleşmesi değişti")
    return module


def export(
    *,
    official_exporter: Path,
    config: Path,
    resume: Path,
    num_classes: int,
) -> None:
    if num_classes <= 0:
        raise ValueError("num_classes pozitif olmalıdır")
    module = _load_official_exporter(official_exporter)
    original_yaml_config = module.YAMLConfig

    def configured_yaml(path: str, **kwargs):
        kwargs["num_classes"] = num_classes
        kwargs["remap_mscoco_category"] = False
        return original_yaml_config(path, **kwargs)

    module.YAMLConfig = configured_yaml
    module.main(
        SimpleNamespace(
            config=str(config.resolve()),
            resume=str(resume.resolve()),
            check=True,
            simplify=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-exporter", type=Path, required=True)
    parser.add_argument("-c", "--config", type=Path, required=True)
    parser.add_argument("-r", "--resume", type=Path, required=True)
    parser.add_argument("--num-classes", type=int, required=True)
    args = parser.parse_args()
    export(
        official_exporter=args.official_exporter,
        config=args.config,
        resume=args.resume,
        num_classes=args.num_classes,
    )


if __name__ == "__main__":
    main()
