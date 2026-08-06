"""[1] ALGI — D-FINE nesne tespiti (CPU, ONNX Runtime).

Lisans politikası: Ultralytics/boxmot (AGPL) KULLANILMAZ.
  - Dedektör: **D-FINE-S** (Apache-2.0) — ağırlık zinciri doğrulanmış:
    `ustc-community/dfine_s_coco` (Apache-2.0) → `onnx-community/dfine_s_coco-ONNX`
    (beyan edilen türev). Ağırlık REPOYA GİRMEZ; `DORTGOZ_DFINE_ONNX` ile yerel
    yol verilir (indirme: scripts/fetch_models.sh).
  - Takip: BYTE (supervision, MIT) — sonraki adım; v1 pencere-içi IoU sezgisi
  - Poz: RTMPose-m — sonraki adım

NEDEN (2026-08-06 ölçümleri): kaçırılan GT pencerelerinin 27/32'si "kare açlığı"
ve hiçbir hareket ölçütü duran kişiyi boş odadan ayıramıyor (2026-08-03 mimari
sonucu). Dedektör iki iş görür: (1) hareket kapısının ELEDİĞİ pencerede insan/
araç bularak pencereyi KURTARIR (yalnız-geri-çağırma OR kuralı); (2) VLM istemine
sayısal bağlam verir ("3 kişi, 1 araç; 1 kişi pencere boyunca hareketsiz") —
niyet gerektiren sınıflarda (hırsızlık) modelin yoksun olduğu kanıt budur.

Maliyet: D-FINE-S CPU'da ~160 ms/kare (ölçüldü); pencere başına 4 örnek kare
≈ 0,64 CPU-sn — VLM maliyetinin yanında ihmal edilebilir.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ..config import settings

# Ağır importlar (numpy/onnxruntime) fonksiyon içinde — mock modda ve
# dedektörsüz kurulumlarda modül yüklenebilir kalsın.

SIZE = 640                       # D-FINE girdisi (preprocessor: 640×640, 1/255)

# Pencereyi kurtaran / meta'ya giren sınıflar. COCO'nun tamamı gürültü olur;
# gözetim alanında karar taşıyan nesneler bunlar (id2label config.json'dan
# doğrulanır, sabit indeks varsayılmaz).
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
    # normalize cxcywh (D-FINE ham çıktısı) — köşe kutusuna çevirim yardımcıda
    cx: float
    cy: float
    w: float
    h: float

    def iou(self, other: "Detection") -> float:
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
    """Tembel tekil ONNX oturumu + config.json'dan id2label."""

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

    def detect(self, rgb: "object", conf: float) -> list[Detection]:
        """640×640 RGB uint8 kare → eşik üstü, ilgi listesi içi tespitler."""
        import numpy as np
        x = (np.asarray(rgb, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        logits, boxes = self.session.run(None, {"pixel_values": x})
        probs = 1.0 / (1.0 + np.exp(-logits[0]))          # sigmoid, (300, 80)
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
    """`t` anındaki kareyi 640×640 ham RGB olarak döndürür (ffmpeg ölçekler —
    yeni görüntü kütüphanesi GEREKMEZ). grab_frame'deki akış-sonu payı burada da
    geçerli: başarısız olursa geriye adımlanır."""
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
    """Bir pencerenin algı özeti — kabul kararı + VLM istem metaverisi."""

    counts: dict[str, int]           # sınıf → örnekler arasında görülen EN ÇOK sayı
    stationary_persons: int          # ilk↔son örnekte aynı yerde duran kişi
    samples: int

    @property
    def hit(self) -> bool:
        return bool(self.counts)

    def meta_text(self) -> str:
        """VLM istemine giden Türkçe tek satır — SAYISAL bağlam, yorum değil.

        ⚠ Sınıf sözlüğü dersi (2026-08-05): buraya şüphe/yorum EKLENMEZ; yalnız
        dedektörün saydığı nesneler yazılır. Yorum modelin işi.
        """
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
    """Pencereden eş aralıklı `samples` karede tespit; sayım + durağanlık özeti.

    Durağanlık sezgisi (v1, BYTE gelene dek): ilk ve son örnekte IoU ≥ 0,5 ile
    eşleşen kişi "pencere boyunca aynı konumda" sayılır — 'yerde hareketsiz
    kişi' hedef sınıfının kare-farkında GÖRÜNMEZ olduğu ölçülmüştü; bu sezgi
    tam da o boşluğu dolduruyor.
    """
    det = detector()
    n = max(2, samples)
    ts = [start + (end - start) * (i + 0.5) / n for i in range(n)]
    frames = await asyncio.gather(*(frame_rgb(video, t) for t in ts))

    def run_all() -> list[list[Detection]]:
        import numpy as np
        return [det.detect(np.frombuffer(f, dtype=np.uint8).reshape(SIZE, SIZE, 3),
                           settings.detector_conf) for f in frames]

    per_frame = await asyncio.to_thread(run_all)

    counts: dict[str, int] = {}
    for dets in per_frame:
        frame_counts: dict[str, int] = {}
        for d in dets:
            frame_counts[d.label] = frame_counts.get(d.label, 0) + 1
        for c, k in frame_counts.items():
            counts[c] = max(counts.get(c, 0), k)

    stationary = 0
    first = [d for d in per_frame[0] if d.label == "person"]
    last = [d for d in per_frame[-1] if d.label == "person"]
    used: set[int] = set()
    for a in first:
        for j, b in enumerate(last):
            if j not in used and a.iou(b) >= 0.5:
                used.add(j)
                stationary += 1
                break

    return WindowPerception(counts=counts, stationary_persons=stationary,
                            samples=n)
