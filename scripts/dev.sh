#!/usr/bin/env bash
# Backend + vite dev sunucusunu birlikte başlatır.
#
#   ./scripts/dev.sh          arayüz test akışı — GPU/model gerekmez, video analizi yapılmaz
#   ./scripts/dev.sh real     gerçek mod — .env'deki yerel model ucuna bağlanır
#
# Konsol: http://localhost:5173   (API/WS 8000'e proxy'lenir)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-mock}"

case "$MODE" in
  mock|real) ;;
  *) echo "Kullanım: ./scripts/dev.sh [mock|real]"; exit 2 ;;
esac

for tool in uv bun; do
  command -v "$tool" >/dev/null || { echo "eksik: $tool"; exit 1; }
done

if [ "$MODE" = "real" ]; then
  for tool in ffmpeg ffprobe; do
    command -v "$tool" >/dev/null || { echo "eksik: $tool"; exit 1; }
  done
fi

if [ "$MODE" = "real" ] && [ ! -f "$ROOT/.env" ]; then
  echo "HATA: .env yok. 'cp .env.example .env' ve yerel model ayarlarını doldur"
  echo "      Modelsiz denemek için: ./scripts/dev.sh"
  exit 1
fi

echo "→ bağımlılıklar"
(cd "$ROOT/backend" && uv sync --locked -q)
[ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && bun install --frozen-lockfile)

if [ "$MODE" = "real" ]; then
  (cd "$ROOT/backend" && uv run python ../scripts/preflight.py --root .. --mode real --check-tools)
fi

if [ "$MODE" = "real" ]; then
  count=$(find "$ROOT/media" -maxdepth 1 -name '*.mp4' 2>/dev/null | wc -l)
  [ "$count" -gt 0 ] || echo "UYARI: media/ boş — konsolda seçilecek klip olmayacak"
  echo "→ gerçek mod ($count klip)"
else
  echo "→ arayüz test akışı (video analizi yapılmaz)"
fi

trap 'kill 0' EXIT

if [ "$MODE" = "mock" ]; then
  (cd "$ROOT/backend" && DORTGOZ_MOCK=1 uv run uvicorn dortgoz.main:app --reload --port 8000) &
else
  (cd "$ROOT/backend" && DORTGOZ_MOCK=0 uv run uvicorn dortgoz.main:app --reload --port 8000) &
fi
(cd "$ROOT/frontend" && bun run dev) &
wait
