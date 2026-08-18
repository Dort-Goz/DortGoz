#!/usr/bin/env bash
# DONMUŞ profil üzerinde 3 replika (Madde 14). Profil 2026-08-18 optimizasyonundan
# sonra DEĞİŞMEYECEK: VULKAN_XCHAIN_DENSE (GFXQ yok) · -c 24576 · -np 4 --kv-unified · q8 KV.
# ⚠ SIRALI koşulur (--esz 1): ilk replika seti de sıralıydı; yığınlama varyansı
# replika varyansına KARIŞMAMALI. np4 slotları sıralı istekte sonucu değiştirmiyor.
set -u
cd ~/DortGoz/backend
for R in "" _r2 _r3; do
  AD="${R:-_r1}"
  echo "=== [$(date +%H:%M:%S)] donmuş profil replika ${AD} ==="
  DORTGOZ_MAIN_MODEL=qwen3.8-27b-vision-dg timeout 14400 uv run python \
    ../bench/ikinci_gorus_dogrula.py \
    --birincil "../bench/results/ab_testsplit_96k${R}.jsonl" \
    --model qwen3.8-27b-vision-dg --hareket 0.30 --esz 1 \
    --out "../bench/results/ikinci_gorus_donmus${AD}.jsonl" 2>&1 | tail -12
  echo "=== [$(date +%H:%M:%S)] replika ${AD} bitti (exit=$?) ==="
done
echo "=== DONMUŞ REPLİKA SETİ BİTTİ ==="
