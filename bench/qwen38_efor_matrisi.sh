#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../backend"
R=../bench/results
DG=qwen3.8-27b-vision-dg
K35=qwen3.6-35b-a3b-vision

kol() {
  local ad=$1 model=$2 kademe=$3 so_model=${4:-} so_efor=${5:-}
  echo "=== [$(date +%H:%M:%S)] KOL $ad · model=$model · kademe=${kademe:-aile} ${so_model:+· ikinci görüş=$so_model/$so_efor} ==="
  DORTGOZ_MAIN_MODEL="$model" \
  DORTGOZ_INTERPRET_EFFORT="$kademe" \
  DORTGOZ_SECOND_OPINION_MODEL="$so_model" \
  DORTGOZ_SECOND_OPINION_EFFORT="$so_efor" \
  DORTGOZ_INTERPRET_THINK_TEMP="${THINK_TEMP:-0}" \
    timeout 14400 uv run python ../bench/ab_pipeline.py --out "$R/efor_${ad}.jsonl" 2>&1 \
    | tail -25
  echo "=== [$(date +%H:%M:%S)] KOL $ad bitti (exit=$?) ==="
}

kol low    "$DG"  low
kol medium "$DG"  medium
kol xhigh  "$DG"  xhigh
