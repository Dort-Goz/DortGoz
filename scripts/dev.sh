#!/usr/bin/env bash
# Backend + vite dev sunucusunu birlikte başlatır.
#
#   ./scripts/dev.sh          gerçek mod — modelleri .env'deki uca bağlanır
#   ./scripts/dev.sh mock     mock mod   — GPU/model gerekmez, örnek akış oynatılır
#
# Konsol: http://localhost:5173   (API/WS 8000'e proxy'lenir)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-real}"

for tool in uv bun ffmpeg; do
  command -v "$tool" >/dev/null || { echo "eksik: $tool"; exit 1; }
done

if [ "$MODE" = "real" ] && [ ! -f "$ROOT/.env" ]; then
  echo "HATA: .env yok. 'cp .env.example .env' ve uç adresini doldur"
  echo "      (adres ekip içi elden paylaşılır)"
  echo "      Modelsiz denemek için: ./scripts/dev.sh mock"
  exit 1
fi

echo "→ bağımlılıklar"
(cd "$ROOT/backend" && uv sync -q)
[ -d "$ROOT/frontend/node_modules" ] || (cd "$ROOT/frontend" && bun install)

if [ "$MODE" = "real" ]; then
  count=$(find "$ROOT/media" -maxdepth 1 -name '*.mp4' 2>/dev/null | wc -l)
  [ "$count" -gt 0 ] || echo "UYARI: media/ boş — konsolda seçilecek klip olmayacak"
  echo "→ gerçek mod ($count klip)"
else
  echo "→ mock mod"
fi

trap 'kill 0' EXIT

if [ "$MODE" = "mock" ]; then
  (cd "$ROOT/backend" && DORTGOZ_MOCK=1 uv run uvicorn dortgoz.main:app --reload --port 8000) &
else
  (cd "$ROOT/backend" && uv run uvicorn dortgoz.main:app --reload --port 8000) &
fi
(cd "$ROOT/frontend" && bun run dev) &
wait
