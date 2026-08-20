from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "semantic"
LOCAL = OUT / "local"

MODEL_ID = "google/siglip2-base-patch16-224"
EVENT_ANCHORS = [
    "a fire, smoke or an explosion",
    "a person snatching a bag and running away",
    "a person smashing or damaging property",
    "people running away in panic",
]
NORMAL_ANCHORS = [
    "people walking normally on a street",
    "an empty street or parking lot",
    "normal traffic on a road",
    "people shopping in a store",
]

SCORE_PARAMS = {
    "ev_weight": 0.5,
    "z_scale": 0.6,
    "sample_step": 2.0,
    "warmup": 3,
    "sd_floor_ev": 0.01,
    "sd_floor_act": 0.02,
    "ev_prior": None,
    "act_prior": {"mu": 0.1505, "sd": 0.1966, "n": 8.0},
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export() -> None:
    import numpy as np
    import torch
    from transformers import AutoModel, AutoProcessor

    LOCAL.mkdir(parents=True, exist_ok=True)
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModel.from_pretrained(MODEL_ID)
    model.eval()

    with torch.no_grad():
        ti = processor(text=EVENT_ANCHORS + NORMAL_ANCHORS, padding="max_length",
                       max_length=64, return_tensors="pt")
        temb = model.get_text_features(**ti)
        if hasattr(temb, "pooler_output"):
            temb = temb.pooler_output
        temb = (temb / temb.norm(dim=-1, keepdim=True)).numpy().astype(np.float32)
    anchors_file = LOCAL / "siglip2_anchors.npz"
    np.savez(anchors_file, anchors=temb, n_event=np.array(len(EVENT_ANCHORS)),
             labels=np.array(EVENT_ANCHORS + NORMAL_ANCHORS))
    print(f"çapalar {temb.shape} -> {anchors_file}")

    class VisionTower(torch.nn.Module):
        def __init__(self, m: torch.nn.Module) -> None:
            super().__init__()
            self.m = m

        def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
            e = self.m.get_image_features(pixel_values=pixel_values)
            if hasattr(e, "pooler_output"):
                e = e.pooler_output
            return e / e.norm(dim=-1, keepdim=True)

    tower = VisionTower(model)
    onnx_file = LOCAL / "siglip2_vision.onnx"
    torch.onnx.export(
        tower, (torch.zeros(1, 3, 224, 224),), str(onnx_file),
        input_names=["pixel_values"], output_names=["img_emb"],
        dynamic_axes={"pixel_values": {0: "batch"}, "img_emb": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    print(f"onnx -> {onnx_file}")

    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
    x = np.random.default_rng(7).uniform(-1, 1, (2, 3, 224, 224)).astype(np.float32)
    with torch.no_grad():
        ref = tower(torch.from_numpy(x)).numpy()
    got = sess.run(None, {"pixel_values": x})[0]
    cos = float((ref * got).sum(-1).mean())
    print(f"torch/onnx eşliği (kosinüs): {cos:.6f}")
    assert cos > 0.999, "ONNX aktarımı torch ile eşleşmedi"


def write_metadata() -> None:
    onnx_file = LOCAL / "siglip2_vision.onnx"
    anchors_file = LOCAL / "siglip2_anchors.npz"
    artifact = {
        "model_id": "siglip2-semantic-v1",
        "version": "1.1.0",
        "license": "Apache-2.0",
        "onnx_path": "models/semantic/local/siglip2_vision.onnx",
        "onnx_sha256": sha256(onnx_file),
        "anchors_path": "models/semantic/local/siglip2_anchors.npz",
        "anchors_sha256": sha256(anchors_file),
        **SCORE_PARAMS,
        "notes": "Nedensel kamera-tabanlı SigLIP-z + activity-z. Üretim yolu ölçümü "
                 "(2026-08-08, çalışma noktası st=0,60/ct=0,36): val 13/13 @ %65,6 "
                 "(taban %81,0); feed doğrulaması project-state günlüğünde.",
    }
    artifact_file = OUT / "semantic-v1.json"
    artifact_file.write_text(json.dumps(artifact, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
    manifest = {
        "model_id": "siglip2-semantic-v1",
        "version": "1.1.0",
        "model_type": "siglip_semantic",
        "artifact_path": "models/semantic/semantic-v1.json",
        "artifact_sha256": sha256(artifact_file),
        "license": "Apache-2.0",
        "input_fps": 0.5,
        "feature_schema": ["siglip2_event_sim", "activity"],
        "notes": "Etkinleştirme: DORTGOZ_CANDIDATE_MODEL_MANIFEST=models/semantic/manifest.json "
                 "+ DORTGOZ_CANDIDATE_START_THRESHOLD=0.80 "
                 "DORTGOZ_CANDIDATE_CONTINUE_THRESHOLD=0.48",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"artifact + manifest -> {OUT}")


if __name__ == "__main__":
    if "--manifest-only" not in sys.argv:
        export()
    write_metadata()
