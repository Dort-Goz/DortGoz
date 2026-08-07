"""SigLIP-2 anlamsal screening scorer'ı — nedensel kamera-tabanı ile.

Kampanya bulgusu (2026-08-08): hareket ailesi
0.95 kapısını hiçbir eğitimle geçemedi; SigLIP-2 olay-çapa benzerliği + nedensel
(yalnız-geçmiş) kamera-içi normalizasyon her iki alanda tam recall'u korurken
kapsamayı düşüren tek varyanttır. Çalışma zamanı yalnız onnxruntime + numpy
ister; görüntü kulesi ONNX'e bir kez aktarılır (scripts/export_siglip.py),
metin kulesi hiç koşmaz — çapa embeddingleri artifact yanında hazır durur.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.candidate import ScreeningSample
from .ingest import MotionSample

_SIDE = 224
_BATCH = 16


class SemanticPrior(BaseModel):
    """Global öncül: yeni kamera koşan-tabanının sözde-gözlem başlangıcı."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mu: float
    sd: float = Field(gt=0)
    n: float = Field(gt=0)


class SemanticArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    license: Literal["Apache-2.0", "MIT"]
    onnx_path: str = Field(min_length=1)
    onnx_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    anchors_path: str = Field(min_length=1)
    anchors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ev_weight: float = Field(ge=0, le=1, default=0.5)
    z_scale: float = Field(gt=0, default=0.6)
    sample_step: float = Field(gt=0, default=2.0)
    warmup: int = Field(ge=0, default=3)
    sd_floor_ev: float = Field(gt=0, default=0.01)
    sd_floor_act: float = Field(gt=0, default=0.02)
    ev_prior: SemanticPrior | None = None
    act_prior: SemanticPrior | None = None
    notes: str = ""


class CausalWelford:
    """Yalnız-geçmiş koşan z-skoru; öncül = sözde-gözlem istatistiği.

    Oracle tüm-klip normalizasyonundan bilerek farklı: olay kendi
    normalizasyon istatistiğine katılmadan puanlanır (ölçüm: nedensel
    varyant oracle'dan İYİ — %70,4 → %62,8 kapsama, val klipleri).
    """

    def __init__(self, prior: SemanticPrior | None, sd_floor: float, warmup: int) -> None:
        self.sd_floor = sd_floor
        self.warmup = warmup
        self.seen = 0
        if prior is not None:
            self.n = prior.n
            self.mean = prior.mu
            self.m2 = prior.sd * prior.sd * prior.n
            self.has_prior = True
        else:
            self.n = 0.0
            self.mean = 0.0
            self.m2 = 0.0
            self.has_prior = False

    def push(self, value: float) -> float:
        past_n = self.n
        if (not self.has_prior and self.seen < self.warmup) or past_n < 2:
            z = 0.0
        else:
            sd = max((self.m2 / past_n) ** 0.5, self.sd_floor)
            z = (value - self.mean) / sd
        self.seen += 1
        self.n += 1
        delta = value - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (value - self.mean)
        return z


# Oturum süreç başına bir kez kurulur; ort.InferenceSession.run thread-safe'tir.
_RUNTIME_CACHE: dict[str, tuple[object, object, int]] = {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SemanticCandidateModel:
    """Kare akışı ister: runner ``score_video`` yolunu kullanır."""

    def __init__(self, artifact: SemanticArtifact, *, onnx_file: Path, anchors_file: Path) -> None:
        self.artifact = artifact
        self.model_id = artifact.model_id
        self._onnx_file = onnx_file
        self._anchors_file = anchors_file

    def score(self, profile: list[MotionSample]) -> list[ScreeningSample]:
        raise RuntimeError("anlamsal scorer kare erişimi ister; score_video kullanılmalı")

    def _runtime(self) -> tuple[object, object, int]:
        key = str(self._onnx_file)
        cached = _RUNTIME_CACHE.get(key)
        if cached is not None:
            return cached
        import numpy as np
        import onnxruntime as ort

        if _sha256(self._onnx_file) != self.artifact.onnx_sha256:
            raise ValueError("semantic onnx SHA-256 artifact ile eşleşmiyor")
        if _sha256(self._anchors_file) != self.artifact.anchors_sha256:
            raise ValueError("semantic çapa SHA-256 artifact ile eşleşmiyor")
        data = np.load(self._anchors_file)
        anchors = data["anchors"].astype(np.float32)
        n_event = int(data["n_event"])
        session = ort.InferenceSession(str(self._onnx_file),
                                       providers=["CPUExecutionProvider"])
        runtime = (session, anchors[:n_event], n_event)
        _RUNTIME_CACHE[key] = runtime
        return runtime

    def _event_sims(self, video: Path) -> list[tuple[float, float]]:
        """0,5 fps akış çözümü → parti ONNX çıkarımı → (t, olay-benzerliği)."""
        import numpy as np

        session, event_anchors, _ = self._runtime()
        step = self.artifact.sample_step
        proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video),
             "-vf", f"fps=1/{step},scale={_SIDE}:{_SIDE}:flags=bicubic",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE)
        assert proc.stdout is not None
        nbytes = _SIDE * _SIDE * 3
        out: list[tuple[float, float]] = []
        batch: list[bytes] = []
        base = 0

        def flush() -> None:
            nonlocal base
            if not batch:
                return
            x = np.frombuffer(b"".join(batch), dtype=np.uint8).reshape(
                len(batch), _SIDE, _SIDE, 3)
            x = (x.astype(np.float32) / 255.0 - 0.5) / 0.5
            x = np.ascontiguousarray(x.transpose(0, 3, 1, 2))
            emb = session.run(None, {"pixel_values": x})[0]
            sims = emb @ event_anchors.T
            for j, s in enumerate(sims):
                out.append(((base + j) * step, float(s.max())))
            base += len(batch)
            batch.clear()

        try:
            while True:
                buf = proc.stdout.read(nbytes)
                if len(buf) < nbytes:
                    break
                batch.append(buf)
                if len(batch) >= _BATCH:
                    flush()
            flush()
        finally:
            proc.stdout.close()
            proc.wait()
        return out

    def score_video(self, profile: list[MotionSample],
                    video: Path) -> list[ScreeningSample]:
        art = self.artifact
        activity = {round(s.t): s.activity for s in profile}
        sims = self._event_sims(video)
        if not sims:
            raise ValueError(f"anlamsal scorer kare çözemedi: {video}")
        ev_base = CausalWelford(art.ev_prior, art.sd_floor_ev, art.warmup)
        act_base = CausalWelford(art.act_prior, art.sd_floor_act, art.warmup)
        samples: list[ScreeningSample] = []
        for i, (t, ev) in enumerate(sims):
            act = activity.get(round(t), activity.get(round(t) - 1, 0.0))
            z = (art.ev_weight * ev_base.push(ev)
                 + (1 - art.ev_weight) * act_base.push(act)) / art.z_scale
            z = max(min(z, 30.0), -30.0)
            score = 1.0 / (1.0 + 2.718281828459045 ** (-z))
            samples.append(ScreeningSample(
                timestamp=t,
                anomaly_score=max(0.0, min(1.0, score)),
                image_quality=1.0,
                source_model=self.model_id,
                feature_ref=f"sem:{i}",
            ))
        return samples


__all__ = [
    "CausalWelford",
    "SemanticArtifact",
    "SemanticCandidateModel",
    "SemanticPrior",
]
