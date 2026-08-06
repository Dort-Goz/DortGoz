#!/usr/bin/env bash
# Algı katmanı model ağırlıklarını indirir — ağırlıklar REPOYA GİRMEZ.
#
# D-FINE-S (nesne tespiti, CPU/ONNX):
#   Lisans zinciri doğrulanmış (2026-08-06): ustc-community/dfine_s_coco
#   (Apache-2.0) → onnx-community/dfine_s_coco-ONNX (beyan edilen türev).
#   Atıf: Peng ve ark., "D-FINE: Redefine Regression Task in DETRs as
#   Fine-grained Distribution Refinement", ICLR 2025.
#
# Kullanım:  ./scripts/fetch_models.sh [hedef_dizin]
#   Varsayılan hedef: ~/.cache/dortgoz/dfine — farklıysa DORTGOZ_DFINE_ONNX'i
#   .env'de <hedef>/model.onnx olarak ayarla.
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
