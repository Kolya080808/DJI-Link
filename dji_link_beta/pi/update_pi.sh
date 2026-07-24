#!/usr/bin/env bash
# Auto-update the Raspberry Pi jump-host from the latest GitHub Release.
#
# Intended to run from a systemd timer installed by setup_pi.sh. It does nothing
# when the Pi has no internet, and only re-runs install-pi.sh when the latest
# release tag differs from /opt/dji-link/VERSION.
set -euo pipefail

PREFIX="${DJI_PREFIX:-/opt/dji-link}"
REPO="${DJI_REPO:-}"
ASSET="${DJI_ASSET:-install-pi.sh}"

if [ -z "$REPO" ] && [ -f "$PREFIX/REPO" ]; then
    REPO="$(cat "$PREFIX/REPO")"
fi
[ -n "$REPO" ] || REPO="Kolya080808/DJI-Link"

if [ "$(id -u)" -ne 0 ]; then
    echo "!! update_pi.sh must run as root"
    exit 1
fi

command -v curl >/dev/null 2>&1 || {
    echo "[update] curl missing; skipping"
    exit 0
}

API="https://api.github.com/repos/${REPO}/releases/latest"
echo "[update] checking ${API}"
JSON="$(curl -fsSL --connect-timeout 5 --max-time 20 "$API" 2>/dev/null || true)"
if [ -z "$JSON" ]; then
    echo "[update] no internet or GitHub unreachable; skipping"
    exit 0
fi

LATEST="$(printf '%s\n' "$JSON" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
if [ -z "$LATEST" ]; then
    echo "[update] could not parse latest release tag; skipping"
    exit 0
fi

CURRENT=""
[ -f "$PREFIX/VERSION" ] && CURRENT="$(cat "$PREFIX/VERSION")"
if [ "$CURRENT" = "$LATEST" ]; then
    echo "[update] already at ${LATEST}"
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/${REPO}/releases/download/${LATEST}/${ASSET}"

echo "[update] upgrading ${CURRENT:-unknown} -> ${LATEST}"
curl -fSL "$URL" -o "$TMP/install-pi.sh"
chmod +x "$TMP/install-pi.sh"

DJI_REPO="$REPO" DJI_TAG="$LATEST" DJI_PREFIX="$PREFIX" bash "$TMP/install-pi.sh"
echo "[update] done: ${LATEST}"
