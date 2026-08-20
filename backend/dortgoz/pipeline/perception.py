from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

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

    def __init__(self) -> None:
        import onnxruntime as ort
        model = Path(settings.dfine_onnx)
        if not model.is_file():
            raise FileNotFoundError(
                f"D-FINE ONNX bulunamadı: {model} — DORTGOZ_DFINE_ONNX ayarla "
                "(indirme: scripts/fetch_models.sh)")
        self.session = ort.InferenceSession(str(model),
                                            providers=["CPUExecutionProvider"])
        cfg = model.parent / "config.json"
        labels = json.loads(cfg.read_text())["id2label"] if cfg.is_file() else {}
        self.id2label = {int(k): v for k, v in labels.items()}

    def detect(self, rgb: object, conf: float) -> list[Detection]:
        import numpy as np
        x = (np.asarray(rgb, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        logits, boxes = self.session.run(None, {"pixel_values": x})
        probs = 1.0 / (1.0 + np.exp(-logits[0]))
        best = probs.max(axis=1)
        cls = probs.argmax(axis=1)
        out: list[Detection] = []
        for i in np.nonzero(best >= conf)[0]:
            label = self.id2label.get(int(cls[i]), str(int(cls[i])))
            if label not in INTEREST:
                continue
            cx, cy, w, h = (float(v) for v in boxes[0][i])
            out.append(Detection(label, float(best[i]), cx, cy, w, h))
        return out


_detector: _Detector | None = None


def detector() -> _Detector:
    global _detector
    if _detector is None:
        _detector = _Detector()
    return _detector


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
