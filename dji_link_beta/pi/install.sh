#!/usr/bin/env bash
# DJI-Link Pi installer — one-shot bring-up of a clean Raspberry Pi as the AOA
# jump-host, meant to be run straight off the network:
#
#     curl -fsSL https://github.com/OWNER/REPO/releases/latest/download/install-pi.sh | sudo bash
#
# It downloads the matching pi/ bundle from the GitHub release, then hands off to
# setup_pi.sh (dwc2 + raw_gadget + a systemd service that auto-starts the bridge on
# every boot/power-up). After it finishes and you unplug/replug power, the service
# comes back by itself — nothing to launch by hand.
#
# The @@REPO@@ / @@TAG@@ / @@ASSET@@ markers are filled in by release.yml when the
# asset is published. Running the in-repo copy directly falls back to sane defaults
# and can be overridden with env vars:
#     DJI_REPO=owner/repo  DJI_TAG=v1.2.3  sudo -E bash install.sh
set -euo pipefail

REPO="${DJI_REPO:-@@REPO@@}"
TAG="${DJI_TAG:-@@TAG@@}"
ASSET="${DJI_ASSET:-@@ASSET@@}"
PREFIX="${DJI_PREFIX:-/opt/dji-link}"

# Unsubstituted template markers -> fall back to repo defaults.
case "$REPO"  in *@@*) REPO="Kolya080808/DJI-Link";; esac
case "$TAG"   in *@@*) TAG="latest";; esac
case "$ASSET" in *@@*) ASSET="dji-link-pi.tar.gz";; esac

# Re-exec under root if needed (so `curl | bash` works without a manual sudo).
if [ "$(id -u)" -ne 0 ]; then
    echo "[install] elevating with sudo…"
    exec sudo -E bash "$0" "$@"
fi

if [ "$TAG" = "latest" ]; then
    URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
else
    URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
fi

echo "=== DJI-Link Pi installer ==="
echo "    repo   : $REPO"
echo "    tag    : $TAG"
echo "    bundle : $URL"
echo "    prefix : $PREFIX"
echo

command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y curl; }
command -v tar  >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y tar; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[install] downloading bundle"
curl -fSL "$URL" -o "$TMP/bundle.tar.gz" || {
    echo "!! could not download $URL"
    echo "   check the release exists and the asset name is '$ASSET'"
    exit 1
}

echo "[install] unpacking to $PREFIX"
mkdir -p "$PREFIX"
tar -xzf "$TMP/bundle.tar.gz" -C "$PREFIX"

# The bundle contains a top-level pi/ directory with setup_pi.sh + the bridge scripts.
PI_DIR="$PREFIX/pi"
[ -d "$PI_DIR" ] || PI_DIR="$(dirname "$(find "$PREFIX" -name setup_pi.sh -print -quit)")"
[ -n "$PI_DIR" ] && [ -f "$PI_DIR/setup_pi.sh" ] || {
    echo "!! setup_pi.sh not found in the downloaded bundle"
    exit 1
}

echo "[install] running setup (raw_gadget + dwc2 + boot service)"
bash "$PI_DIR/setup_pi.sh" --dir "$PI_DIR" --service

echo
echo "=== installer finished ==="
echo ">>> If a reboot was requested above, run: sudo reboot"
echo ">>> After that the dji-bridge service starts automatically on every power-up."
echo ">>> Status:  systemctl status dji-bridge   |   Logs:  journalctl -u dji-bridge -f"
