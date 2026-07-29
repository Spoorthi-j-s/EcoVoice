#!/usr/bin/env bash
#
# EcoVoice — one-command launcher for non-developers.
# Just run:   ./run.sh
# It installs everything the first time, then starts the app. Press Ctrl+C to stop.
#
set -euo pipefail
cd "$(dirname "$0")"

echo "🎙️ EcoVoice starting up..."
echo

# 1) Make sure 'uv' (the Python runner) is available on PATH.
#    It may already be installed in a non-standard spot (e.g. a VSCode-snap dir),
#    so we actively look for it before — and after — installing.
ensure_uv_on_path() {
  command -v uv >/dev/null 2>&1 && return 0
  # Known install locations, including XDG and VSCode-snap layouts.
  local d
  for d in \
    "${XDG_BIN_HOME:-}" \
    "${XDG_DATA_HOME:+$XDG_DATA_HOME/../bin}" \
    "$HOME/.local/bin" \
    "$HOME/.cargo/bin"; do
    if [ -n "$d" ] && [ -x "$d/uv" ]; then
      export PATH="$d:$PATH"; hash -r 2>/dev/null || true; return 0
    fi
  done
  # Last resort: search under the home directory.
  local found
  found="$(find "$HOME" -maxdepth 6 -type f -name uv -perm -u+x 2>/dev/null | head -n1 || true)"
  if [ -n "$found" ]; then
    export PATH="$(dirname "$found"):$PATH"; hash -r 2>/dev/null || true; return 0
  fi
  return 1
}

if ! ensure_uv_on_path; then
  echo "📦 First-time setup: installing 'uv'..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if ! ensure_uv_on_path; then
  echo "❌ Could not locate 'uv' even after installing."
  echo "   Close and reopen the terminal, then run ./run.sh again."
  exit 1
fi
echo "✓ using uv: $(command -v uv)"

# 2) Install Python + all dependencies (first run downloads them; later runs are instant).
echo "📦 Installing dependencies (first run also downloads the speech model)..."
uv sync

# 3) Create a local HTTPS certificate if it's missing.
#    HTTPS is required so phone browsers allow microphone access.
if [ ! -f certs/cert.pem ]; then
  echo "🔐 Creating a local HTTPS certificate..."
  mkdir -p certs
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  IP="${IP:-127.0.0.1}"
  if command -v openssl >/dev/null 2>&1; then
    openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout certs/key.pem -out certs/cert.pem -days 365 \
      -subj "/CN=ecovoice.local" \
      -addext "subjectAltName=DNS:ecovoice.local,DNS:localhost,IP:${IP},IP:127.0.0.1" \
      >/dev/null 2>&1
  else
    echo "⚠️  'openssl' not found — starting without HTTPS. The phone microphone won't work"
    echo "    until a certificate exists in ./certs. Install openssl and run ./run.sh again."
  fi
fi

# 4) Launch the app.
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
LAN_IP="${LAN_IP:-127.0.0.1}"
echo
echo "============================================================"
echo "✅ EcoVoice is starting. Give it a moment to load the model."
echo "   On THIS computer:            https://localhost:8501"
echo "   On another phone (same Wi-Fi): https://ecovoice.local:8501"
echo "                          or:     https://${LAN_IP}:8501"
echo "   Tip: accept the browser's security warning once"
echo "        (the certificate is self-signed for local use)."
echo "   Press Ctrl+C here to stop."
echo "============================================================"
echo 

exec uv run main.py
