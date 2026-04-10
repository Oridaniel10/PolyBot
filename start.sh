#!/bin/bash
# Starts: 1) FastAPI (bot + /api + /dashboard/)  2) Cloudflare quick tunnel
# Remote / phone: use the https://*.trycloudflare.com URL printed below (not 127.0.0.1).
#
# Usage: ./start.sh
# Requires: venv, ui/dist (npm run build in ui/), cloudflared in PATH or ~/bin/cloudflared

set -e
cd "$(dirname "$0")"

PORT="${PORT:-8080}"
CF_LOG="${TMPDIR:-/tmp}/poly-cloudflared-$$.log"
BOT_PID=""
TUNNEL_PID=""
cleanup() {
  rm -f "$CF_LOG" 2>/dev/null || true
  [[ -n "$BOT_PID" ]] && kill "$BOT_PID" 2>/dev/null || true
  [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ -x "$HOME/bin/cloudflared" ]]; then
  CF_BIN="$HOME/bin/cloudflared"
elif command -v cloudflared >/dev/null 2>&1; then
  CF_BIN="cloudflared"
else
  echo "[start] ERROR: cloudflared not found. Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
  echo "[start] Or put the binary at ~/bin/cloudflared"
  exit 1
fi

echo "[start] launching bot + HTTP on port $PORT ..."
source venv/bin/activate
python main_bot.py &
BOT_PID=$!

for _ in $(seq 1 15); do
  if curl -sfL "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    echo "[start] HTTP OK (local only)"
    echo "[start] same Wi‑Fi / this PC:  http://127.0.0.1:$PORT/  → redirects to /dashboard/"
    break
  fi
  sleep 1
done

echo "[start] Cloudflare Tunnel → public HTTPS (phone, mobile data, any network) ..."
"$CF_BIN" tunnel --url "http://127.0.0.1:$PORT" >>"$CF_LOG" 2>&1 &
TUNNEL_PID=$!

TUNNEL_URL=""
for _ in $(seq 1 25); do
  TUNNEL_URL=$(grep -oE 'https://[a-zA-Z0-9.-]+\.trycloudflare\.com' "$CF_LOG" 2>/dev/null | head -1 || true)
  if [[ -n "$TUNNEL_URL" ]]; then
    break
  fi
  sleep 1
done

echo ""
echo "========================================================================"
if [[ -n "$TUNNEL_URL" ]]; then
  echo "  PHONE / REMOTE — open in browser (any network):"
  echo ""
  echo "    $TUNNEL_URL"
  echo ""
  echo "  (root redirects to dashboard; API stays same origin)"
else
  echo "  Tunnel starting… URL not detected yet. Run:"
  echo "    grep -oE 'https://[^ ]+trycloudflare\\.com' $CF_LOG | head -1"
  echo "  Or:  tail -f $CF_LOG"
fi
echo "========================================================================"
echo ""

echo "[start] bot PID=$BOT_PID  tunnel PID=$TUNNEL_PID"
echo "[start] tunnel log: $CF_LOG  (tail -f for debug)"
echo "[start] Ctrl+C stops bot + tunnel"

wait
