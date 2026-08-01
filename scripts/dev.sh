#!/usr/bin/env bash
# Geliştirme: mock backend + vite dev sunucusunu birlikte başlatır.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

trap 'kill 0' EXIT

(cd "$ROOT/backend" && DORTGOZ_MOCK=1 uv run uvicorn dortgoz.main:app --reload --port 8000) &
(cd "$ROOT/frontend" && npm run dev) &
wait
