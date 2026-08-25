#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../backend"
for R in r2 r3; do
  echo "=== [$(date +%H:%M:%S)] replika $R ==="
  DORTGOZ_MAIN_MODEL=qwen3.8-27b-vision-dg timeout 14400 uv run python \
    ../bench/ikinci_gorus_dogrula.py \
    --birincil ../bench/results/ab_testsplit_96k_${R}.jsonl \
    --model qwen3.8-27b-vision-dg --hareket 0.30 \
    --out ../bench/results/ikinci_gorus_testsplit_${R}.jsonl 2>&1 | tail -25
  echo "=== [$(date +%H:%M:%S)] replika $R bitti (exit=$?) ==="
done
