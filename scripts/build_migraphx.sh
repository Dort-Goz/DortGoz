#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${DORTGOZ_MIGRAPHX_DIR:-$HOME/.cache/dortgoz/migraphx}"
DRIVER="${MIGRAPHX_DRIVER:-/opt/rocm/bin/migraphx-driver}"
DFINE_BATCH="${DORTGOZ_DFINE_BATCH:-4}"
SIGLIP_BATCH="${DORTGOZ_SIGLIP_BATCH:-16}"

command -v "$DRIVER" >/dev/null 2>&1 || { echo "migraphx-driver yok: $DRIVER"; exit 1; }

cd "$ROOT/backend"
DFINE_ONNX="$(uv run python -c 'from dortgoz.pipeline.perception import resolve_production_model_path as r; print(r())')"
SIGLIP_ONNX="$ROOT/models/semantic/local/siglip2_vision.onnx"

[ -f "$DFINE_ONNX" ] || { echo "D-FINE ONNX yok: $DFINE_ONNX"; exit 1; }
[ -f "$SIGLIP_ONNX" ] || { echo "SigLIP ONNX yok: $SIGLIP_ONNX"; exit 1; }

mkdir -p "$OUT"

echo "→ sabit şekilli kopyalar"
uv run --with onnx python -m onnxruntime.tools.make_dynamic_shape_fixed \
  --input_name pixel_values --input_shape "$DFINE_BATCH,3,640,640" \
  "$DFINE_ONNX" "$OUT/dfine-fixed.onnx"
uv run --with onnx python -m onnxruntime.tools.make_dynamic_shape_fixed \
  --input_name pixel_values --input_shape "$SIGLIP_BATCH,3,224,224" \
  "$SIGLIP_ONNX" "$OUT/siglip-fixed.onnx"

echo "→ D-FINE derlemesi (fp32; fp16 tespit sayısını değiştirdiği için kullanılmaz)"
nice -n 15 "$DRIVER" compile --onnx --gpu --enable-offload-copy \
  -o "$OUT/dfine.mxr" "$OUT/dfine-fixed.onnx"

echo "→ SigLIP derlemesi (fp16; kosinüs eşliği 0,999988 ölçüldü)"
nice -n 15 "$DRIVER" compile --onnx --gpu --enable-offload-copy --fp16 \
  -o "$OUT/siglip.mxr" "$OUT/siglip-fixed.onnx"

uv run python - "$OUT" "$DFINE_ONNX" "$SIGLIP_ONNX" <<'PY'
import json, sys
from pathlib import Path
from dortgoz.utils import file_sha256
out, dfine, siglip = (Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
manifest = {
    "dfine": {"source": str(dfine), "source_sha256": file_sha256(dfine), "precision": "fp32"},
    "siglip": {"source": str(siglip), "source_sha256": file_sha256(siglip), "precision": "fp16"},
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps(manifest, indent=2))
PY

rm -f "$OUT/dfine-fixed.onnx" "$OUT/siglip-fixed.onnx"
echo "hazır: $OUT"
echo "etkinleştirmek için .env içine DORTGOZ_MIGRAPHX_DIR=$OUT ekleyin"
