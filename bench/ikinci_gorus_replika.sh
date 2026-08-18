#!/usr/bin/env bash
# Madde 14: rapora giren sayı tek koşudan alınmaz. Tırmandırma kolunu birincil
# tabanın r2 ve r3 replikaları üzerinde de ölçer (r1 2026-08-18'de koşuldu).
set -u
cd ~/DortGoz/backend
for R in r2 r3; do
  echo "=== [$(date +%H:%M:%S)] replika $R ==="
  DORTGOZ_MAIN_MODEL=qwen3.8-27b-vision-dg timeout 14400 uv run python \
    ../bench/ikinci_gorus_dogrula.py \
    --birincil ../bench/results/ab_testsplit_96k_${R}.jsonl \
    --model qwen3.8-27b-vision-dg --hareket 0.30 \
    --out ../bench/results/ikinci_gorus_testsplit_${R}.jsonl 2>&1 | tail -25
  echo "=== [$(date +%H:%M:%S)] replika $R bitti (exit=$?) ==="
done
