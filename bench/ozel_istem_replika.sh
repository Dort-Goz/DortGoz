#!/usr/bin/env bash
# ÖZEL ikinci-görüş istemi (v2) r2/r3 doğrulaması — Madde 14.
set -u
cd ~/DortGoz/backend
for R in _r2 _r3; do
  echo "=== [$(date +%H:%M:%S)] özel istem ${R} ==="
  DORTGOZ_MAIN_MODEL=qwen3.8-27b-vision-dg timeout 14400 uv run python \
    ../bench/ikinci_gorus_dogrula.py \
    --birincil "../bench/results/ab_testsplit_96k${R}.jsonl" \
    --model qwen3.8-27b-vision-dg --hareket 0.30 --esz 1 --istem ikinci \
    --out "../bench/results/ikinci_gorus_ozel2${R}.jsonl" 2>&1 | tail -8
  echo "=== [$(date +%H:%M:%S)] ${R} bitti ==="
done
echo "=== ÖZEL İSTEM REPLİKA BİTTİ ==="
