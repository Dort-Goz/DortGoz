from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from dortgoz import session
from dortgoz.config import settings
from dortgoz.events import AgentStep, IncidentUpdate, RunStatus, WindowReport
from dortgoz.pipeline import ingest, runner, windowing
from dortgoz.pipeline.interpret import (
    REVIEW_SYSTEM_STRICT_TR,
    REVIEW_SYSTEM_TR,
    SYSTEM_TR,
    TASK_TR,
)

ROOT = Path(__file__).resolve().parents[1]

ARMS = {
    "vlm": {
        "primary": "vlm",
        "second": "",
        "incident_review": False,
        "adjudicate": "",
        "escalate": 0.0,
    },
    "fast": {
        "primary": "llm-fast",
        "second": "",
        "incident_review": False,
        "adjudicate": "",
        "escalate": 0.0,
    },
    "large": {
        "primary": "llm-large",
        "second": "",
        "incident_review": False,
        "adjudicate": "",
        "escalate": 0.0,
    },
    "production": {
        "primary": "vlm",
        "second": "llm-large",
        "incident_review": True,
        "adjudicate": "hirsizlik,kavga,saldiri,bilinmeyen,arac_kazasi",
        "escalate": 0.10,
    },
}
EXPECTED = {
    "Abuse": "kavga",
    "Arrest": "bilinmeyen",
    "Arson": "yangin",
    "Assault": "saldiri",
    "Burglary": "hirsizlik",
    "Explosion": "patlama",
    "Fighting": "kavga",
    "RoadAccidents": "arac_kazasi",
    "Robbery": "hirsizlik",
    "Shooting": "silahli_olay",
    "Shoplifting": "hirsizlik",
    "Stealing": "hirsizlik",
    "Vandalism": "vandalizm",
}


class Collector:
    def __init__(self) -> None:
        self.reports: list[WindowReport] = []
        self.incidents: dict[str, IncidentUpdate] = {}
        self.errors: list[str] = []
        self.terminal: RunStatus | None = None

    async def broadcast(self, event) -> None:
        payload = event.payload
        if isinstance(payload, WindowReport):
            self.reports.append(payload)
        elif isinstance(payload, IncidentUpdate):
            self.incidents[payload.incident_id] = payload
        elif isinstance(payload, AgentStep) and payload.status == "error":
            self.errors.append(payload.detail)
        elif isinstance(payload, RunStatus) and payload.state in {"done", "error"}:
            self.terminal = payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def family(path: Path) -> str:
    stem = path.stem.replace("_x264", "")
    if stem.startswith("Normal"):
        return "Normal"
    return re.sub(r"_?\d+$", "", stem)


def ucf_root(value: Path | None) -> Path:
    root = (value or Path("~/datasets/Dort_Goz/UCF_Crimes")).expanduser().resolve()
    if (root / "Videos").is_dir():
        return root
    if root.name == "Videos" and root.is_dir():
        return root.parent
    raise SystemExit(f"UCF-Crime kökü geçersiz: {root}")


def clips_for(args: argparse.Namespace) -> tuple[list[Path], str]:
    if args.pilot:
        clips = sorted((ROOT / "media").glob("*_x264.mp4"))
        return clips[: args.limit], "repo-pilot"
    root = ucf_root(args.ucf)
    if args.clips:
        names = [
            line.strip() for line in args.clips.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        index = {path.name: path for path in (root / "Videos").rglob("*.mp4")}
        missing = [name for name in names if name not in index]
        if missing:
            raise SystemExit(f"{len(missing)} klip yok; ilk: {missing[0]}")
        return [index[name] for name in names[: args.limit]], f"clips:{sha256(args.clips)}"
    videos = root / "Videos"
    split = root / "Anomaly_Detection_splits" / f"Anomaly_{args.split.capitalize()}.txt"
    lines = [line.strip() for line in split.read_text(encoding="utf-8").splitlines() if line.strip()]
    clips = [videos / line for line in lines]
    missing = [path for path in clips if not path.is_file()]
    if missing:
        raise SystemExit(f"{len(missing)} bölme klibi yok; ilk: {missing[0]}")
    return clips[: args.limit], f"{args.split}:{sha256(split)}"


def configure(args: argparse.Namespace, workspace: Path) -> dict[str, Any]:
    arm = ARMS[args.arm]
    settings.runs_dir = workspace / "runs"
    settings.media_dir = workspace / "media"
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.second_opinion_model = arm["second"]
    settings.incident_review = arm["incident_review"]
    settings.incident_review_strict = args.strict_review
    settings.adjudicate_confusable = arm["adjudicate"]
    settings.escalate_p = arm["escalate"] if args.escalate is None else args.escalate
    settings.escalate_target_p = 0.0
    settings.escalation_zoom_seconds = args.zoom_seconds
    settings.escalate_low_severity = args.escalate_low
    settings.dual_read = False
    settings.final_sweep = False
    settings.category_rules_enabled = False
    settings.exemplar_suppress = False
    settings.exemplar_shadow = True
    settings.candidate_screening = not args.no_screening
    settings.candidate_start_threshold = args.start_threshold
    settings.candidate_continue_threshold = args.continue_threshold
    settings.candidate_adaptive_threshold = False
    settings.second_opinion_motion = args.second_motion
    return arm


def config_record(args: argparse.Namespace, dataset: str, arm: dict[str, Any]) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    candidate_manifest = Path(settings.candidate_model_manifest)
    return {
        "type": "config",
        "arm": args.arm,
        "arm_config": arm,
        "dataset": dataset,
        "repeat_count": args.repeat,
        "parallel": args.parallel,
        "window_seconds": settings.window_seconds,
        "video_input_width": settings.video_input_width,
        "keyframes_per_window": settings.keyframes_per_window,
        "models": {
            "main": settings.main_model,
            "video": settings.video_model,
            "second_opinion": settings.second_opinion_model,
        },
        "detector_conf": settings.detector_conf,
        "detector_rescue_conf": settings.detector_rescue_conf,
        "adjudicate_min_conf": settings.adjudicate_min_conf,
        "candidate_screening": settings.candidate_screening,
        "candidate_start_threshold": settings.candidate_start_threshold,
        "candidate_continue_threshold": settings.candidate_continue_threshold,
        "candidate_manifest_sha256": (
            sha256(candidate_manifest) if candidate_manifest.is_file() else None
        ),
        "detector_enabled": settings.detector_enabled,
        "incident_review_strict": settings.incident_review_strict,
        "escalate_p": settings.escalate_p,
        "escalation_zoom_seconds": settings.escalation_zoom_seconds,
        "escalate_low_severity": settings.escalate_low_severity,
        "second_opinion_motion": settings.second_opinion_motion,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_TR.encode()).hexdigest(),
        "task_prompt_sha256": hashlib.sha256(TASK_TR.encode()).hexdigest(),
        "review_prompt_sha256": hashlib.sha256(
            (REVIEW_SYSTEM_STRICT_TR if settings.incident_review_strict else REVIEW_SYSTEM_TR).encode()
        ).hexdigest(),
        "code_revision": revision,
    }


def metrics_for(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        payload = json.loads(line).get("payload", {})
        if payload.get("type") == "run_metrics":
            return payload
    return {}


def window_rows(duration: float, reports: list[WindowReport]) -> list[dict[str, Any]]:
    indexed = {(round(report.window_start, 3), round(report.window_end, 3)): report
               for report in reports}
    rows = []
    for start, end in windowing.windows(duration, settings.window_seconds):
        report = indexed.get((round(start, 3), round(end, 3)))
        rows.append({
            "start": start,
            "end": end,
            "gated": report is None,
            "summary": report.summary if report else "",
            "events": [event.model_dump(mode="json") for event in report.events]
            if report else [],
            "anomaly_type": report.anomaly_type if report else "normal",
        })
    return rows


async def measure(path: Path, repeat: int, args: argparse.Namespace, sem: asyncio.Semaphore) -> dict:
    async with sem:
        started = time.monotonic()
        run_id = hashlib.sha256(
            f"{args.arm}:{path}:{repeat}:{time.time_ns()}".encode()
        ).hexdigest()[:32]
        feed = f"bench-{run_id[:12]}"
        manager = Collector()
        duration = await ingest.probe_duration(path)
        await runner.run_video(
            manager,
            path.name,
            run_id,
            model=ARMS[args.arm]["primary"],
            feed=feed,
            mode="dengeli",
            live=True,
            source_path=path,
        )
        incidents = [
            item.model_dump(mode="json")
            for item in manager.incidents.values()
            if item.phase == "sonuclandi"
        ]
        return {
            "type": "clip",
            "clip": path.name,
            "source": str(path),
            "class": family(path),
            "anomaly": family(path) != "Normal",
            "repeat": repeat,
            "duration": duration,
            "wall_seconds": round(time.monotonic() - started, 3),
            "terminal": manager.terminal.state if manager.terminal else "missing",
            "terminal_detail": manager.terminal.detail if manager.terminal else "",
            "windows": window_rows(duration, manager.reports),
            "incidents": incidents,
            "errors": manager.errors,
            "metrics": metrics_for(settings.runs_dir / f"{run_id}.jsonl"),
        }


def actionable(row: dict) -> list[dict]:
    return [
        incident for incident in row.get("incidents", [])
        if incident.get("anomaly_type") not in {None, "normal"}
        and incident.get("risk") != "dusuk"
    ]


def summarize(rows: list[dict]) -> dict[str, Any]:
    clips = [row for row in rows if row.get("type") == "clip"]
    anomaly = [row for row in clips if row["anomaly"]]
    normal = [row for row in clips if not row["anomaly"]]
    detected = [row for row in anomaly if actionable(row)]
    false_alarm = [row for row in normal if actionable(row)]
    category = [
        row for row in detected
        if EXPECTED.get(row["class"]) in {
            item["anomaly_type"] for item in actionable(row)
        }
    ]
    metrics: dict[str, float] = {}
    model_calls: dict[str, int] = {}
    for row in clips:
        for key, value in row.get("metrics", {}).items():
            if isinstance(value, int | float):
                metrics[key] = metrics.get(key, 0.0) + value
        for key, value in row.get("metrics", {}).get("model_calls", {}).items():
            model_calls[key] = model_calls.get(key, 0) + value
    evidence_total = metrics.get("evidence_validation_count", 0)
    evidence_auto = metrics.get("evidence_valid_count", 0)
    evidence_technical = evidence_auto + metrics.get("evidence_human_review_count", 0)
    return {
        "records": len(clips),
        "anomaly_records": len(anomaly),
        "normal_records": len(normal),
        "detected": len(detected),
        "false_alarm": len(false_alarm),
        "category_correct": len(category),
        "review_normalized": sum(
            any(item.get("anomaly_type") == "normal" for item in row.get("incidents", []))
            for row in clips
        ),
        "terminal_failures": sum(row["terminal"] != "done" for row in clips),
        "records_with_errors": sum(bool(row["errors"]) for row in clips),
        "evidence_technical_valid_rate": (
            evidence_technical / evidence_total if evidence_total else None
        ),
        "evidence_automatic_valid_rate": (
            evidence_auto / evidence_total if evidence_total else None
        ),
        "wall_seconds": round(sum(row["wall_seconds"] for row in clips), 3),
        "model_calls": dict(sorted(model_calls.items())),
        "metrics": metrics,
        "missed": sorted(row["clip"] for row in anomaly if not actionable(row)),
        "false_alarm_clips": sorted(row["clip"] for row in normal if actionable(row)),
    }


async def collect(args: argparse.Namespace) -> None:
    clips, dataset = clips_for(args)
    workspace = args.out.with_suffix("")
    arm = configure(args, workspace)
    config = config_record(args, dataset, arm)
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    done: set[tuple[str, int]] = set()
    rows = []
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            rows.append(item)
            if item.get("type") == "config" and item.get("config_hash") != config_hash:
                raise SystemExit("çıktı farklı benchmark yapılandırması içeriyor")
            if item.get("type") == "clip" and item.get("terminal") == "done":
                done.add((item["clip"], item["repeat"]))
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        config["config_hash"] = config_hash
        args.out.write_text(json.dumps(config, ensure_ascii=False) + "\n", encoding="utf-8")
        rows.append(config)
    jobs = [(path, repeat) for repeat in range(args.repeat) for path in clips
            if (path.name, repeat) not in done]
    sem = asyncio.Semaphore(args.parallel)
    lock = asyncio.Lock()
    completed = 0

    async def one(path: Path, repeat: int) -> None:
        nonlocal completed
        try:
            row = await measure(path, repeat, args, sem)
        except Exception as exc:
            row = {
                "type": "clip",
                "clip": path.name,
                "source": str(path),
                "class": family(path),
                "anomaly": family(path) != "Normal",
                "repeat": repeat,
                "terminal": "error",
                "errors": [f"{type(exc).__name__}: {exc}"],
                "incidents": [],
                "windows": [],
                "metrics": {},
                "wall_seconds": 0.0,
            }
        async with lock:
            completed += 1
            print(
                f"[{completed}/{len(jobs)}] {path.name} r{repeat + 1}: "
                f"{row['terminal']} · olay {len(row.get('incidents', []))} · "
                f"{row.get('wall_seconds', 0):.1f} sn",
                flush=True,
            )
            with args.out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)

    await asyncio.gather(*(one(path, repeat) for path, repeat in jobs))
    session.clear()
    summary = summarize(rows)
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=sorted(ARMS), required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--split", choices=("test", "train"), default="test")
    parser.add_argument("--ucf", type=Path)
    parser.add_argument("--clips", type=Path)
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--start-threshold", type=float, default=0.80)
    parser.add_argument("--continue-threshold", type=float, default=0.48)
    parser.add_argument("--second-motion", type=float, default=0.30)
    parser.add_argument("--escalate", type=float)
    parser.add_argument("--zoom-seconds", type=float, default=0.0)
    parser.add_argument("--escalate-low", action="store_true")
    parser.add_argument("--strict-review", action="store_true")
    parser.add_argument("--no-screening", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.parallel < 1:
        raise SystemExit("repeat ve parallel pozitif olmalı")
    asyncio.run(collect(args))


if __name__ == "__main__":
    main()
