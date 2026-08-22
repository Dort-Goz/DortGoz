from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..config import settings

SIZE = 640

INTEREST = {
    "person", "bicycle", "car", "motorcycle", "bus", "truck",
    "backpack", "handbag", "suitcase", "knife",
}
TR = {
    "person": "kişi", "bicycle": "bisiklet", "car": "otomobil",
    "motorcycle": "motosiklet", "bus": "otobüs", "truck": "kamyon/kamyonet",
    "backpack": "sırt çantası", "handbag": "el çantası",
    "suitcase": "valiz", "knife": "bıçak",
}


@dataclass
class Detection:
    label: str
    conf: float
    cx: float
    cy: float
    w: float
    h: float

    def iou(self, other: Detection) -> float:
        ax0, ay0 = self.cx - self.w / 2, self.cy - self.h / 2
        ax1, ay1 = self.cx + self.w / 2, self.cy + self.h / 2
        bx0, by0 = other.cx - other.w / 2, other.cy - other.h / 2
        bx1, by1 = other.cx + other.w / 2, other.cy + other.h / 2
        ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        iy = max(0.0, min(ay1, by1) - max(ay0, by0))
        inter = ix * iy
        union = self.w * self.h + other.w * other.h - inter
        return inter / union if union > 0 else 0.0


class _Detector:

    def __init__(self, model_path: Path | None = None, *, session_factory=None) -> None:
        model = (model_path or Path(settings.dfine_onnx)).resolve()
        if not model.is_file():
            raise FileNotFoundError(
                f"D-FINE ONNX bulunamadı: {model} — DORTGOZ_DFINE_ONNX ayarla "
                "(indirme: scripts/fetch_models.sh)")
        if session_factory is None:
            import onnxruntime as ort

            from .onnx_ep import providers, session_options

            self.session = ort.InferenceSession(
                str(model),
                sess_options=session_options(),
                providers=providers(),
            )
        else:
            self.session = session_factory(str(model), providers=["CPUExecutionProvider"])
        input_names = [item.name for item in self.session.get_inputs()]
        output_names = [item.name for item in self.session.get_outputs()]
        if input_names == ["pixel_values"]:
            self.contract = "raw"
        elif input_names == ["images", "orig_target_sizes"] and output_names == [
            "labels",
            "boxes",
            "scores",
        ]:
            self.contract = "deployed"
        else:
            raise ValueError(
                f"desteklenmeyen D-FINE ONNX sözleşmesi: {input_names} → {output_names}"
            )
        cfg = model.parent / "config.json"
        config_payload = json.loads(cfg.read_text(encoding="utf-8")) if cfg.is_file() else {}
        labels = config_payload.get("id2label", {})
        self.id2label = {int(k): v for k, v in labels.items()}
        configured_interest = config_payload.get("interest_labels")
        self.interest = (
            set(configured_interest)
            if isinstance(configured_interest, list) and configured_interest
            else INTEREST
        )

    def detect(self, rgb: object, conf: float) -> list[Detection]:
        import numpy as np
        x = (np.asarray(rgb, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        if self.contract == "raw":
            logits, boxes = self.session.run(None, {"pixel_values": x})
            probs = 1.0 / (1.0 + np.exp(-logits[0]))
            best = probs.max(axis=1)
            labels = probs.argmax(axis=1)
            raw_boxes = boxes[0]
        else:
            labels_result, boxes_result, scores_result = self.session.run(
                ["labels", "boxes", "scores"],
                {
                    "images": x,
                    "orig_target_sizes": np.asarray([[SIZE, SIZE]], dtype=np.int64),
                },
            )
            best = scores_result[0]
            labels = labels_result[0]
            raw_boxes = boxes_result[0]
        out: list[Detection] = []
        for i in np.nonzero(best >= conf)[0]:
            label = self.id2label.get(int(labels[i]), str(int(labels[i])))
            if label not in self.interest:
                continue
            if self.contract == "raw":
                cx, cy, w, h = (float(v) for v in raw_boxes[i])
            else:
                x1, y1, x2, y2 = (float(v) for v in raw_boxes[i])
                cx = (x1 + x2) / (2 * SIZE)
                cy = (y1 + y2) / (2 * SIZE)
                w = (x2 - x1) / SIZE
                h = (y2 - y1) / SIZE
            out.append(Detection(label, float(best[i]), cx, cy, w, h))
        return out


_detectors: dict[str, _Detector] = {}
_detector_override: Path | None = None
_verified_onnx_hashes: set[tuple[Path, str, int, int]] = set()


def resolve_production_model_path(
    *,
    active_manifest: Path | None = None,
    fallback_onnx: str | Path | None = None,
    workspace_root: Path | None = None,
) -> Path:
    """Terfi edilmiş ONNX dosyasını manifest ve SHA-256 ile doğrula."""

    if _detector_override is not None:
        return _detector_override
    manifest = Path(active_manifest or settings.dfine_active_manifest).resolve()
    if not manifest.exists():
        return Path(fallback_onnx or settings.dfine_onnx).resolve()
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError(f"D-FINE active manifest geçersiz: {manifest}")
    workspace = Path(workspace_root or settings.dfine_workspace_root).resolve()
    if not manifest.is_relative_to(workspace):
        raise ValueError("D-FINE active manifest workspace dışında")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"D-FINE active manifest okunamadı: {exc}") from exc
    reference = payload.get("onnx_ref")
    expected_sha = payload.get("onnx_sha256")
    if (
        payload.get("manifest_version") != "1.0.0"
        or not isinstance(reference, str)
        or not _safe_model_reference(reference)
        or not isinstance(expected_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
    ):
        raise ValueError("D-FINE active manifest sözleşmesi geçersiz")
    model = workspace.joinpath(*reference.replace("\\", "/").split("/")).resolve()
    if not model.is_relative_to(workspace) or model.is_symlink() or not model.is_file():
        raise ValueError("aktif D-FINE ONNX bulunamadı veya güvenli değil")
    model_stat = model.stat()
    verification_key = (model, expected_sha, model_stat.st_size, model_stat.st_mtime_ns)
    if verification_key not in _verified_onnx_hashes:
        if _sha256_file(model) != expected_sha:
            raise ValueError("aktif D-FINE ONNX SHA-256 değeri değişti")
        if len(_verified_onnx_hashes) >= 8:
            _verified_onnx_hashes.clear()
        _verified_onnx_hashes.add(verification_key)
    config = model.parent / "config.json"
    if config.is_symlink() or not config.is_file():
        raise ValueError("aktif D-FINE config.json bulunamadı")
    try:
        runtime_config = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"aktif D-FINE config.json okunamadı: {exc}") from exc
    id2label = runtime_config.get("id2label")
    if (
        not isinstance(id2label, dict)
        or runtime_config.get("onnx_sha256") != expected_sha
        or runtime_config.get("deployment_fingerprint") != payload.get("deployment_fingerprint")
        or list(id2label.values()) != payload.get("category_names")
    ):
        raise ValueError("aktif D-FINE config.json manifest ile eşleşmiyor")
    return model


def detector(model_path: Path | None = None) -> _Detector:
    path = model_path.resolve() if model_path is not None else resolve_production_model_path()
    key = str(path)
    if key not in _detectors:
        _detectors[key] = _Detector(path)
    return _detectors[key]


def reset_detector_cache() -> None:
    _detectors.clear()
    _verified_onnx_hashes.clear()


def set_detector_override(model_path: Path | None) -> None:
    global _detector_override
    _detector_override = model_path.resolve() if model_path is not None else None
    reset_detector_cache()


def _safe_model_reference(value: str) -> bool:
    posix = PurePosixPath(value.replace("\\", "/"))
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def frame_rgb(video: Path, t: float) -> bytes:
    from .ingest import FFmpegError, _run
    for attempt_t in (t, max(0.0, t - 1.0), max(0.0, t - 2.5)):
        try:
            out = await _run(
                "ffmpeg", "-v", "error", "-ss", f"{attempt_t:.3f}", "-i", str(video),
                "-frames:v", "1", "-vf", f"scale={SIZE}:{SIZE}",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            )
            if len(out) == SIZE * SIZE * 3:
                return out
        except FFmpegError:
            continue
    raise FFmpegError(f"kare alınamadı: t={t:.3f} {video.name}")


@dataclass
class WindowPerception:

    counts: dict[str, int]
    stationary_persons: int
    samples: int
    rescue_persons: int = 0

    @property
    def hit(self) -> bool:
        return bool(self.counts)

    def meta_text(self) -> str:
        if not self.counts:
            return "Dedektör bu pencerede insan/araç görmedi."
        parts = [f"{n} {TR.get(c, c)}" for c, n in sorted(self.counts.items())]
        text = "Dedektör (örneklenmiş karelerde): " + ", ".join(parts) + "."
        if self.stationary_persons:
            text += (f" {self.stationary_persons} kişi pencere boyunca aynı "
                     "konumda (hareketsiz).")
        return text


async def scan_window(video: Path, start: float, end: float,
                      samples: int = 4) -> WindowPerception:
    det = detector()
    n = max(2, samples)
    ts = [start + (end - start) * (i + 0.5) / n for i in range(n)]
    frames = await asyncio.gather(*(frame_rgb(video, t) for t in ts))

    low_conf = min(settings.detector_conf, settings.detector_rescue_conf)

    def run_all() -> list[list[Detection]]:
        import numpy as np
        return [det.detect(np.frombuffer(f, dtype=np.uint8).reshape(SIZE, SIZE, 3),
                           low_conf) for f in frames]

    per_frame = await asyncio.to_thread(run_all)

    counts: dict[str, int] = {}
    rescue_persons = 0
    for dets in per_frame:
        frame_counts: dict[str, int] = {}
        n_low_persons = 0
        for d in dets:
            if d.label == "person":
                n_low_persons += 1
            if d.conf >= settings.detector_conf:
                frame_counts[d.label] = frame_counts.get(d.label, 0) + 1
        rescue_persons = max(rescue_persons, n_low_persons)
        for c, k in frame_counts.items():
            counts[c] = max(counts.get(c, 0), k)

    stationary = 0
    first = [d for d in per_frame[0] if d.label == "person" and d.conf >= settings.detector_conf]
    last = [d for d in per_frame[-1] if d.label == "person" and d.conf >= settings.detector_conf]
    used: set[int] = set()
    for a in first:
        for j, b in enumerate(last):
            if j not in used and a.iou(b) >= 0.5:
                used.add(j)
                stationary += 1
                break

    return WindowPerception(counts=counts, stationary_persons=stationary,
                            samples=n, rescue_persons=rescue_persons)
