#!/usr/bin/env bash
# Qwen3.8-27B düşünme kademesi matrisi — 31 klip / 40 pencere (2026-08-15 ile
# birebir kıyaslanabilir set: media/*_x264.mp4).
#
# Soru: 2026-08-15 A/B'si 27B'yi DÜŞÜNMESİZ ölçtü (17/26 yakalama, 5,3× maliyet)
# ve "birincil yapma, ikinci görüş yap" dedi. Kademe ekseni hiç denenmedi.
# Düşünme 27B'nin kaçırdığı Robbery'yi kurtarır mı, maliyeti ne yapar?
#
# Her kol AYRI dosyaya yazar; ab_pipeline kol kimliğini (model+kademe+ikinci
# görüş) config bloğunda tutar ve karışmayı reddeder. Kol içinde klip klip
# sürdürülebilir — kesilirse aynı komut kaldığı yerden devam eder.
set -u
cd "$(dirname "$0")/../backend"
R=../bench/results
DG=qwen3.8-27b-vision-dg
K35=qwen3.6-35b-a3b-vision

kol() {   # kol <ad> <model> <kademe> [ikinci-görüş-modeli] [ikinci-görüş-kademesi]
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

# K1 (düşünmesiz 27B) 2026-08-15'te ölçüldü → ab_qwen38dg_20260815.jsonl, tekrar koşulmaz.
kol low    "$DG"  low
kol medium "$DG"  medium
kol xhigh  "$DG"  xhigh
