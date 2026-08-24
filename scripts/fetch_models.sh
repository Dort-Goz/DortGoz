#!/usr/bin/env bash
set -euo pipefail

DEST="${1:-~/.cache/dortgoz/dfine}"
BASE="https://huggingface.co/onnx-community/dfine_s_coco-ONNX/resolve/main"

mkdir -p "$DEST"
for f in onnx/model.onnx config.json preprocessor_config.json; do
  out="$DEST/$(basename "$f")"
  if [ -f "$out" ]; then
    echo "var: $out"
  else
    echo "indiriliyor: $f"
    curl -fL --progress-bar "$BASE/$f" -o "$out"
  fi
done

echo
echo "Tamam. .env için:  DORTGOZ_DFINE_ONNX=$DEST/model.onnx"
