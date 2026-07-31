#!/usr/bin/env bash
# One-shot bring-up of a clean Raspberry Pi (Zero 2 W) as the AOA jump-host.
#
# Does everything a fresh install needs:
#   1. build tools + kernel headers
#   2. dwc2 in peripheral mode (config.txt + module autoload)
#   3. builds and installs the raw_gadget module (the Pi kernel ships it disabled)
#   4. optionally clones the project and installs systemd services for bridge + netctl + updates
#
# Usage:
#   sudo bash setup_pi.sh                      # set up this machine, use ~/pi if present
#   sudo bash setup_pi.sh <git-url>            # also clone/update the project first
#   sudo bash setup_pi.sh <git-url> --service  # and run bridge.py at boot
#   sudo bash setup_pi.sh --dir /opt/dji-link/pi --service   # use already-present files
#
# A reboot is required the first time (dwc2 is a device-tree change).
set -euo pipefail

REPO_URL=""
WANT_SERVICE=0
FORCE_DIR=""
_prev=""
for a in "$@"; do
    case "$a" in
        --service) WANT_SERVICE=1 ;;
        --dir)     _prev="dir" ;;
        --*)       _prev="" ;;
        *)
            if [ "$_prev" = "dir" ]; then FORCE_DIR="$a"; _prev="";
            elif [ -z "$REPO_URL" ]; then REPO_URL="$a"; fi ;;
    esac
done

# Root is required (apt, /boot/config.txt, systemd units). Started without sudo, we
# re-exec ourselves under it rather than telling the user to try again — the argument
# list is passed through, so `bash setup_pi.sh --dir X --service` keeps working.
# DJI_REEXEC guards against looping if sudo does not actually give us root.
if [ "$(id -u)" -ne 0 ]; then
    if [ "${DJI_REEXEC:-0}" = "1" ]; then
        echo "!! still not root after sudo; aborting" >&2
        exit 1
    fi
    command -v sudo >/dev/null 2>&1 || {
        echo "!! this script needs root and sudo is not available; log in as root." >&2
        exit 1
    }
    echo "=== not root — re-running under sudo ==="
    exec sudo -E DJI_REEXEC=1 bash "${BASH_SOURCE[0]}" "$@"
fi

# Keep track of the real user so files do not end up root-owned.
RUN_USER="${SUDO_USER:-pi}"
RUN_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)
[ -n "$RUN_HOME" ] || RUN_HOME=/home/pi

KREL=$(uname -r)
KVER=${KREL%%+*}
BRANCH="rpi-${KVER%.*}.y"
BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt

echo "=== DJI-Link Pi setup ==="
echo "    kernel : $KREL"
echo "    user   : $RUN_USER ($RUN_HOME)"
echo "    config : $BOOT_CFG"
echo

# ---------------------------------------------------------------- 1. packages
echo "[1/5] installing packages"
apt-get update -qq
# hostapd + dnsmasq-base + iptables run the Wi-Fi AP (see pi/ap.sh): hostapd is the AP
# (WPS off, so Windows asks for the password, not a PIN), dnsmasq gives DHCP/DNS on
# uap0, iptables NATs to the uplink. A Lite image can lack them, and then the AP comes
# up but clients get no address / no route out — the "Pi network has no internet" symptom.
apt-get install -y build-essential curl git python3 iproute2 iw network-manager \
    hostapd dnsmasq-base iptables >/dev/null
# Nice to have, never fatal (some images do not carry them, and `set -e` would turn a
# missing optional package into a failed setup). wireless-regdb is the regulatory
# database: without it the kernel stays in the world domain (00), which marks channels
# 12-13 NO-IR so hostapd cannot beacon there. ap.sh only picks channels the kernel
# reports as usable either way, so this just widens what the AP is allowed to follow.
apt-get install -y wireless-regdb rfkill >/dev/null 2>&1 || \
    echo "     (wireless-regdb/rfkill unavailable — continuing without them)"
# We drive hostapd ourselves through dji-ap.service, so keep Debian's stock hostapd
# service out of the way (it ships masked, but be explicit for re-runs on odd images).
systemctl disable hostapd 2>/dev/null || true
if [ ! -d "/lib/modules/${KREL}/build" ]; then
    apt-get install -y "linux-headers-${KREL}" 2>/dev/null \
        || apt-get install -y linux-headers-rpi-v8 2>/dev/null \
        || apt-get install -y raspberrypi-kernel-headers 2>/dev/null || true
fi
if [ ! -d "/lib/modules/${KREL}/build" ]; then
    echo "!! no kernel headers for ${KREL}; cannot build raw_gadget."
    echo "   try: sudo apt install linux-headers-\$(uname -r)"
    exit 1
fi

# ---------------------------------------------------------------- 2. dwc2
echo "[2/5] configuring dwc2 (peripheral mode)"
NEED_REBOOT=0
# Match only our own uncommented line: a loose "dtoverlay=dwc2" grep would hit the
# stock "[cm5] dtoverlay=dwc2,dr_mode=host" line and skip this. The "[all]" header
# makes it apply on every model no matter which conditional section is last.
if ! grep -qE '^[[:space:]]*dtoverlay=dwc2,dr_mode=peripheral' "$BOOT_CFG"; then
    printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOT_CFG"
    echo "     added dtoverlay under [all]"
    NEED_REBOOT=1
else
    echo "     already configured"
fi
echo dwc2 > /etc/modules-load.d/dwc2.conf

# ---------------------------------------------------------------- 3. raw_gadget
echo "[3/5] building raw_gadget (kernel ships CONFIG_USB_RAW_GADGET disabled)"
if modinfo raw_gadget >/dev/null 2>&1; then
    echo "     already installed"
else
    BUILD=$(mktemp -d)
    trap 'rm -rf "$BUILD"' EXIT
    cd "$BUILD"
    curl -fsSLO "https://raw.githubusercontent.com/raspberrypi/linux/${BRANCH}/drivers/usb/gadget/legacy/raw_gadget.c" || {
        echo "!! could not fetch raw_gadget.c for branch ${BRANCH}"
        echo "   check https://github.com/raspberrypi/linux/branches"
        exit 1
    }
    echo "obj-m += raw_gadget.o" > Makefile
    if [ ! -f "/lib/modules/${KREL}/build/include/uapi/linux/usb/raw_gadget.h" ]; then
        mkdir -p include/uapi/linux/usb
        curl -fsSLo include/uapi/linux/usb/raw_gadget.h \
            "https://raw.githubusercontent.com/raspberrypi/linux/${BRANCH}/include/uapi/linux/usb/raw_gadget.h"
        echo 'ccflags-y := -I$(src)/include' >> Makefile
    fi
    make -C "/lib/modules/${KREL}/build" M="$PWD" modules >/dev/null
    mkdir -p "/lib/modules/${KREL}/extra"
    cp raw_gadget.ko "/lib/modules/${KREL}/extra/"
    depmod -a
    cd /
fi
echo raw_gadget > /etc/modules-load.d/raw-gadget.conf
modprobe dwc2 2>/dev/null || true
modprobe raw_gadget 2>/dev/null || true
# Let the bridge run without root once the device node exists.
cat > /etc/udev/rules.d/99-raw-gadget.rules <<'EOF'
KERNEL=="raw-gadget", MODE="0666"
EOF
udevadm control --reload 2>/dev/null || true
chmod 666 /dev/raw-gadget 2>/dev/null || true

# ---------------------------------------------------------------- 4. project
echo "[4/5] project files"
if [ -n "$FORCE_DIR" ]; then
    PI_DIR="$FORCE_DIR"
    [ -d "$PI_DIR" ] || { echo "!! --dir $PI_DIR does not exist"; exit 1; }
    echo "     using $PI_DIR"
elif [ -n "$REPO_URL" ]; then
    DEST="$RUN_HOME/dji-link"
    if [ -d "$DEST/.git" ]; then
        sudo -u "$RUN_USER" git -C "$DEST" pull --ff-only || echo "     (pull failed, keeping local)"
    else
        sudo -u "$RUN_USER" git clone --depth 1 "$REPO_URL" "$DEST"
    fi
    PI_DIR="$DEST/dji_link_beta/pi"
    [ -d "$PI_DIR" ] || PI_DIR="$DEST/pi"
    echo "     scripts: $PI_DIR"
elif [ -d "$RUN_HOME/pi" ]; then
    PI_DIR="$RUN_HOME/pi"
    echo "     using existing $PI_DIR"
else
    PI_DIR=""
    echo "     no scripts found — copy pi/ to $RUN_HOME/pi or pass a git URL"
fi

# ---------------------------------------------------------------- 5. service
echo "[5/5] boot service"
if [ "$WANT_SERVICE" = "1" ] && [ -n "$PI_DIR" ]; then
    cat > /etc/systemd/system/dji-netctl.service <<EOF
[Unit]
Description=DJI Link Pi Wi-Fi control API
After=NetworkManager.service dji-ap.service
Wants=NetworkManager.service dji-ap.service

[Service]
ExecStart=/usr/bin/python3 ${PI_DIR}/netctl.py serve
WorkingDirectory=${PI_DIR}
Restart=always
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
EOF

    # uap0 is the hostapd AP interface — NetworkManager must not touch it, but must keep
    # managing wlan0 (scanning + uplink). interface-name:uap0 matches ONLY uap0, not the
    # shared phy, so wlan0 stays managed. Coupled with dji-ap.service so a hostapd-less
    # setup never strands uap0 without an AP (netctl's NM fallback still needs it managed).
    install -d /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/99-dji-uap0-unmanaged.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:uap0
EOF

    # Wi-Fi power save is a well-known cause of a Raspberry Pi that answers for a few
    # minutes and then goes quiet, and it makes the brcmfmac AP+STA combination much
    # less stable. NM turns it back on for every new connection, so setting it once on
    # the interface is not enough — it has to be the default here (2 = disable).
    # MAC randomisation is disabled for the same class of reason: uap0's address is
    # derived from wlan0's, and a station MAC that changes per scan/connection breaks
    # DHCP reservations and some routers' client lists.
    cat > /etc/NetworkManager/conf.d/98-dji-wifi.conf <<'EOF'
[connection]
wifi.powersave = 2

[device]
wifi.scan-rand-mac-address = no
EOF

    # Older installers tried to create uap0 from an udev RUN rule. That was racy on
    # reboot: the path to iw differs between images, and udev can race NetworkManager's
    # first wlan0 association. dji-ap.service now owns uap0 creation before
    # NetworkManager starts, so remove the obsolete rule on upgrades.
    rm -f /etc/udev/rules.d/90-dji-uap0.rules
    udevadm control --reload 2>/dev/null || true

    cat > /etc/systemd/system/dji-ap.service <<EOF
[Unit]
Description=DJI Link Wi-Fi access point (hostapd + dnsmasq on uap0)
After=systemd-modules-load.service systemd-udev-trigger.service
Before=NetworkManager.service
Wants=systemd-udev-trigger.service

[Service]
Type=simple
RuntimeDirectory=dji-ap
StateDirectory=dji-ap
# ap.sh pre creates uap0 + IP + NAT + dnsmasq and writes the hostapd config (on a
# channel this radio is actually allowed to beacon on); hostapd is the foreground main
# process; ap.sh post stops dnsmasq and records how long the run lasted.
ExecStartPre=/bin/bash ${PI_DIR}/ap.sh pre
ExecStart=/usr/sbin/hostapd /run/dji-ap/hostapd.conf
ExecStopPost=/bin/bash ${PI_DIR}/ap.sh post
# The AP is the only way into the Pi in the field, so it is restarted for as long as it
# takes (StartLimitIntervalSec=0 disables systemd's give-up-after-N-tries). What makes
# that safe is that ap.sh no longer destroys uap0 between runs and pins a known-good
# channel after two short runs: the old 3-second create/destroy loop on the shared
# brcmfmac radio is what used to take wlan0 down with it.
Restart=always
RestartSec=5
StartLimitIntervalSec=0
User=root

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/dji-bridge.service <<EOF
[Unit]
Description=DJI AOA bridge (Pi jump-host)
After=network.target

[Service]
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 ${PI_DIR}/bridge.py
WorkingDirectory=${PI_DIR}
Restart=always
RestartSec=2
StartLimitIntervalSec=0
User=root

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/dji-update.service <<EOF
[Unit]
Description=DJI Link Pi auto-update
After=network-online.target dji-netctl.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/bash ${PI_DIR}/update_pi.sh
WorkingDirectory=${PI_DIR}
User=root
EOF

    cat > /etc/systemd/system/dji-update.timer <<EOF
[Unit]
Description=Check for DJI Link Pi updates

[Timer]
OnBootSec=10min
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    # rescue.sh installs a minimal stand-in AP so a Pi with a broken bundle is still
    # reachable. The real one is going back in now, and two hostapds on one interface
    # would fight, so retire it.
    if [ -f /etc/systemd/system/dji-rescue-ap.service ]; then
        echo "     removing the rescue access point (dji-ap takes over)"
        systemctl disable --now dji-rescue-ap.service 2>/dev/null || true
        rm -f /etc/systemd/system/dji-rescue-ap.service
        rm -rf /usr/local/lib/dji-rescue
    fi

    systemctl daemon-reload
    systemctl enable NetworkManager.service 2>/dev/null || true
    systemctl enable dji-ap.service
    systemctl enable dji-netctl.service
    systemctl enable dji-bridge.service
    systemctl enable dji-update.timer
    # Do not restart NetworkManager here: on an SSH install that tears down the uplink
    # we are currently using. Reload the config and mark uap0 unmanaged directly; the
    # boot ordering below makes a full NM restart unnecessary on the next reboot too.
    systemctl reload NetworkManager.service 2>/dev/null || nmcli general reload 2>/dev/null || true
    nmcli dev set uap0 managed no 2>/dev/null || true
    systemctl restart dji-ap.service || true
    systemctl restart dji-netctl.service || true
    systemctl restart dji-bridge.service || true
    systemctl restart dji-update.timer || true
    # bridge.py opens :9910 immediately and retries AOA in the background, so it is useful
    # even before /dev/raw-gadget or the RC is ready.
    if [ "$NEED_REBOOT" = "0" ]; then
        echo "     dji-netctl.service enabled + started"
        echo "     dji-bridge.service enabled + started"
        echo "     dji-update.timer enabled"
    else
        echo "     dji-netctl.service enabled + started"
        echo "     dji-bridge.service enabled + started (AOA activates after the reboot)"
        echo "     dji-update.timer enabled"
    fi
    # Do not walk away from a setup that left the Pi with no access point: report the
    # real state of it, not just whether systemd thinks the unit is running.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        bash "${PI_DIR}/ap.sh" health >/dev/null 2>&1 && break
        sleep 1
    done
    AP_STATE="$(systemctl is-active dji-ap.service 2>/dev/null || echo unknown)"
    # Keyed off the exit status, not the text: a healthy AP can still print an
    # informational note (the firmware moved it to the station's channel, say).
    if bash "${PI_DIR}/ap.sh" health >/dev/null 2>&1; then
        echo "     ap: ${AP_STATE}  (hostapd+dnsmasq) — ok"
    else
        echo "     ap: ${AP_STATE}  (hostapd+dnsmasq)"
        bash "${PI_DIR}/ap.sh" health 2>&1 | sed 's/^/       /' || true
        echo "     !! the access point is NOT healthy. Diagnose with:"
        echo "        sudo python3 ${PI_DIR}/netctl.py doctor"
        echo "        journalctl -u dji-ap -n 40 --no-pager"
    fi
    echo "     ap logs: journalctl -u dji-ap -f"
    echo "     logs: journalctl -u dji-bridge -f"
    echo "     wifi API logs: journalctl -u dji-netctl -f"
    echo "     update logs: journalctl -u dji-update -f"
else
    echo "     skipped (pass --service to run bridge/netctl at boot)"
fi

echo
echo "=== done ==="
if [ "$NEED_REBOOT" = "1" ] || [ ! -e /dev/raw-gadget ] || ! ls /sys/class/udc/ 2>/dev/null | grep -q .; then
    echo ">>> REBOOT NEEDED:  sudo reboot"
    echo ">>> after reboot check:  ls /sys/class/udc/   (expect 3f980000.usb on a Zero 2 W)"
else
    echo ">>> UDC: $(ls /sys/class/udc/)"
    echo ">>> services:  systemctl status dji-netctl dji-bridge"
fi
echo ">>> NOTE: raw_gadget must be rebuilt after a kernel upgrade — re-run this script."
