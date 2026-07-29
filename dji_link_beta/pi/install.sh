#!/usr/bin/env bash
# DJI-Link Pi installer — one-shot bring-up of a clean Raspberry Pi as the AOA
# jump-host, meant to be run straight off the network:
#
#     curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh | bash
#
# Root is required, but you do not have to remember sudo: without it the installer
# re-runs itself under sudo (re-downloading itself first when it came in on stdin).
#
# It downloads the matching pi/ bundle from the GitHub release, then hands off to
# setup_pi.sh (dwc2 + raw_gadget + dji-netctl/dji-bridge services + dji-update
# timer). After it finishes and you unplug/replug power, the Pi services come back
# by themselves — nothing to launch by hand.
#
# Re-running it is the upgrade path (dji-update.timer does exactly that): the
# services are stopped, the previous pi/ is kept as pi.old for rollback, the new
# bundle is unpacked, and both services are restarted on the new code and checked.
#
# The @@REPO@@ / @@TAG@@ / @@ASSET@@ markers are filled in by release.yml when the
# asset is published. Running the in-repo copy directly falls back to sane defaults
# and can be overridden with env vars:
#     DJI_REPO=owner/repo  DJI_TAG=v1.2.3  sudo -E bash install.sh
set -euo pipefail

REPO="${DJI_REPO:-@@REPO@@}"
TAG="${DJI_TAG:-@@TAG@@}"
ASSET="${DJI_ASSET:-@@ASSET@@}"
INSTALLER_ASSET="${DJI_INSTALLER_ASSET:-install-pi.sh}"
PREFIX="${DJI_PREFIX:-/opt/dji-link}"

# Unsubstituted template markers -> fall back to repo defaults.
case "$REPO"  in *@@*) REPO="Kolya080808/DJI-Link";; esac
case "$TAG"   in *@@*) TAG="latest";; esac
case "$ASSET" in *@@*) ASSET="dji-link-pi.tar.gz";; esac

# Not root? Re-run ourselves under sudo instead of telling the user to do it.
#
# Two cases:
#   * started from a file (bash install.sh)   -> exec sudo on that same file;
#   * arrived on stdin (curl ... | bash)      -> there is no file path to re-exec and
#     stdin is already partly consumed, so re-download the installer to a temp file
#     and exec sudo on that. DJI_REEXEC guards against a loop if sudo somehow keeps
#     us non-root.
if [ "$(id -u)" -ne 0 ]; then
    if [ "${DJI_REEXEC:-0}" = "1" ]; then
        echo "!! still not root after sudo; aborting" >&2
        exit 1
    fi
    command -v sudo >/dev/null 2>&1 || {
        echo "!! this installer needs root and sudo is not available." >&2
        echo "   log in as root and re-run it." >&2
        exit 1
    }
    echo "[install] not root — re-running under sudo"
    if [ -f "${BASH_SOURCE[0]:-}" ]; then
        exec sudo -E DJI_REEXEC=1 bash "${BASH_SOURCE[0]}" "$@"
    fi
    if [ "$TAG" = "latest" ]; then
        SELF_URL="https://github.com/${REPO}/releases/latest/download/${INSTALLER_ASSET}"
    else
        SELF_URL="https://github.com/${REPO}/releases/download/${TAG}/${INSTALLER_ASSET}"
    fi
    SELF_TMP="$(mktemp -t dji-install-XXXXXX.sh)"
    trap 'rm -f "$SELF_TMP"' EXIT
    curl -fsSL "$SELF_URL" -o "$SELF_TMP" || {
        echo "!! could not re-download the installer from $SELF_URL" >&2
        echo "   run it as root instead:" >&2
        echo "   curl -fsSL $SELF_URL | sudo bash" >&2
        exit 1
    }
    sudo -E DJI_REEXEC=1 bash "$SELF_TMP" "$@"
    exit $?
fi

if [ "$TAG" = "latest" ]; then
    URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
else
    URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
fi

OLD_VERSION=""
[ -f "$PREFIX/VERSION" ] && OLD_VERSION="$(cat "$PREFIX/VERSION")"

echo "=== DJI-Link Pi installer ==="
echo "    repo   : $REPO"
echo "    tag    : $TAG"
echo "    bundle : $URL"
echo "    prefix : $PREFIX"
echo "    version: ${OLD_VERSION:-none} -> $TAG"
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

# Verify the archive before touching a working install — a truncated download must
# not take the Pi down.
echo "[install] verifying bundle"

LIST="$TMP/bundle.lst"

if ! tar -tzf "$TMP/bundle.tar.gz" >"$LIST"; then
    echo "!! could not read downloaded archive"
    exit 1
fi

if ! grep -qx 'pi/setup_pi.sh' "$LIST"; then
    echo "!! downloaded bundle does not contain pi/setup_pi.sh,"
    echo "   archive contents:"
    sed 's/^/     /' "$LIST"
    echo "keeping current install"
    exit 1
fi

# Stop first: a running bridge.py holds the old code in memory, so restarting the
# services afterwards is what actually makes an upgrade take effect.
if [ -d "$PREFIX/pi" ]; then
    echo "[install] stopping services for the upgrade"
    systemctl stop dji-bridge.service 2>/dev/null || true
    systemctl stop dji-netctl.service 2>/dev/null || true
fi

echo "[install] unpacking to $PREFIX"
mkdir -p "$PREFIX"
if [ -d "$PREFIX/pi" ]; then
    rm -rf "$PREFIX/pi.old"
    mv "$PREFIX/pi" "$PREFIX/pi.old"
    echo "     previous install kept at $PREFIX/pi.old"
fi
if ! tar -xzf "$TMP/bundle.tar.gz" -C "$PREFIX"; then
    echo "!! unpacking failed; restoring the previous install"
    rm -rf "$PREFIX/pi"
    [ -d "$PREFIX/pi.old" ] && mv "$PREFIX/pi.old" "$PREFIX/pi"
    systemctl start dji-netctl.service 2>/dev/null || true
    systemctl start dji-bridge.service 2>/dev/null || true
    exit 1
fi
printf '%s\n' "$REPO" > "$PREFIX/REPO"

# The bundle contains a top-level pi/ directory with setup_pi.sh + the bridge scripts.
PI_DIR="$PREFIX/pi"
[ -f "$PI_DIR/setup_pi.sh" ] || PI_DIR="$(dirname "$(find "$PREFIX" -path "$PREFIX/pi.old" -prune -o -name setup_pi.sh -print -quit)")"
[ -n "$PI_DIR" ] && [ -f "$PI_DIR/setup_pi.sh" ] || {
    echo "!! setup_pi.sh not found in the downloaded bundle"
    exit 1
}

echo "[install] running setup (raw_gadget + dwc2 + bridge/netctl boot services)"
bash "$PI_DIR/setup_pi.sh" --dir "$PI_DIR" --service
printf '%s\n' "$TAG" > "$PREFIX/VERSION"

# setup_pi.sh only restarts the bridge when it believes no reboot is pending; on an
# upgrade of an already-working Pi do it unconditionally, then report what came up.
echo "[install] restarting services on the new code"
systemctl daemon-reload
systemctl restart dji-netctl.service 2>/dev/null || true
if [ -e /dev/raw-gadget ]; then
    systemctl restart dji-bridge.service 2>/dev/null || true
else
    echo "     /dev/raw-gadget missing — dji-bridge starts after the reboot"
fi
sleep 2
for svc in dji-netctl dji-bridge; do
    state="$(systemctl is-active "$svc" 2>/dev/null || true)"
    echo "     $svc: ${state:-unknown}"
    if [ "$state" != "active" ]; then
        journalctl -u "$svc" -n 5 --no-pager 2>/dev/null | sed 's/^/       /' || true
    fi
done

# ---------------------------------------------------------------- health gate
# An upgrade that leaves the Pi without an access point leaves it with no way in at
# all — and dji-update.timer runs this installer unattended, so nobody is watching when
# it happens. Verify the AP really came up; if it did not and there is a previous
# bundle, put it back and restart on it rather than ending the run with a dead Pi.
ap_ok() {
    [ -f "$PI_DIR/ap.sh" ] || return 0          # older bundle without the health check
    bash "$PI_DIR/ap.sh" health >/dev/null 2>&1
}
if [ -f "$PI_DIR/ap.sh" ]; then
    echo "[install] verifying the access point"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        ap_ok && break
        sleep 2
    done
    if ap_ok; then
        echo "     ap: ok"
        rm -f "$PREFIX/BAD_VERSION"     # this tag works; let the updater try it again
    else
        echo "!! the access point did not come up after the upgrade:"
        bash "$PI_DIR/ap.sh" health 2>&1 | sed 's/^/       /' || true
        journalctl -u dji-ap -n 20 --no-pager 2>/dev/null | sed 's/^/       /' || true
        if [ -d "$PREFIX/pi.old" ]; then
            echo "!! rolling back to the previous bundle so the Pi stays reachable"
            rm -rf "$PREFIX/pi.new-failed"
            mv "$PREFIX/pi" "$PREFIX/pi.new-failed"
            mv "$PREFIX/pi.old" "$PREFIX/pi"
            printf '%s\n' "${OLD_VERSION:-unknown}" > "$PREFIX/VERSION"
            # Remember which tag did this: dji-update.timer fires every 6 hours and
            # would otherwise reinstall and roll back the same broken release forever.
            printf '%s\n' "$TAG" > "$PREFIX/BAD_VERSION"
            systemctl daemon-reload
            systemctl restart dji-ap.service 2>/dev/null || true
            systemctl restart dji-netctl.service 2>/dev/null || true
            systemctl restart dji-bridge.service 2>/dev/null || true
            echo "     rolled back to ${OLD_VERSION:-the previous bundle}"
            echo "     the failed bundle is kept at $PREFIX/pi.new-failed"
            exit 1
        fi
        echo "   no previous bundle to roll back to; diagnose with:"
        echo "   sudo python3 $PI_DIR/netctl.py doctor"
    fi
fi

echo
echo "=== installer finished ==="
echo ">>> installed version: $TAG  (was: ${OLD_VERSION:-none})"
echo ">>> If a reboot was requested above, run: sudo reboot"
echo ">>> After that dji-netctl and dji-bridge start automatically on every power-up."
echo ">>> Status:  systemctl status dji-netctl dji-bridge dji-update.timer"
echo ">>> Logs:    journalctl -u dji-netctl -f   |   journalctl -u dji-bridge -f"
echo ">>> Updates: journalctl -u dji-update -f"
echo ">>> Rollback: rm -rf $PREFIX/pi && mv $PREFIX/pi.old $PREFIX/pi && systemctl restart dji-netctl dji-bridge"
