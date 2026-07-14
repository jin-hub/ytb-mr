#!/usr/bin/env bash
# Restore Codex CLI subscription login from the CODEX_AUTH_JSON env secret.
# CODEX_AUTH_JSON holds the content of ~/.codex/auth.json, either raw JSON
# or base64-encoded (base64 recommended to avoid quoting issues in env vars).
set -euo pipefail

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not installed; run: npm install -g @openai/codex" >&2
  exit 1
fi

if codex login status 2>&1 | grep -q "Logged in"; then
  echo "codex already logged in"
  exit 0
fi

if [ -z "${CODEX_AUTH_JSON:-}" ]; then
  echo "CODEX_AUTH_JSON not set. Fall back to: codex login --device-auth" >&2
  exit 1
fi

mkdir -p "$HOME/.codex"
if printf '%s' "$CODEX_AUTH_JSON" | base64 -d > "$HOME/.codex/auth.json" 2>/dev/null \
   && head -c1 "$HOME/.codex/auth.json" | grep -q '{'; then
  : # was base64-encoded JSON
else
  printf '%s' "$CODEX_AUTH_JSON" > "$HOME/.codex/auth.json"
fi
chmod 600 "$HOME/.codex/auth.json"

codex login status
