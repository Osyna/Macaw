#!/usr/bin/env bash
# Local preview for the Macaw site: builds Tailwind CSS and serves the folder.
# Usage: ./start.sh [port]   (default 8000)
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8000}"

# Prefer a real tailwindcss binary; fall back to npx (downloads on first run).
if command -v tailwindcss >/dev/null 2>&1; then
    TW=(tailwindcss)
else
    TW=(npx -y tailwindcss@3.4.17)
fi

echo "Building styles/output.css (watching for changes)…"
"${TW[@]}" -c tailwind.config.cjs -i styles/input.css -o styles/output.css --watch &
TW_PID=$!
trap 'kill "$TW_PID" 2>/dev/null || true' EXIT

# Wait for the first build so the page isn't unstyled on first load.
for _ in $(seq 1 30); do [ -s styles/output.css ] && break; sleep 0.2; done

echo "Serving http://localhost:$PORT  (Ctrl+C to stop)"
python3 -m http.server "$PORT"
