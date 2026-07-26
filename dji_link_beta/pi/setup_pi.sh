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

# Run as root, but keep track of the real user so files do not end up root-owned.
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
# dnsmasq-base + iptables are what NetworkManager's ipv4.method=shared uses for the
# AP's DHCP/DNS and its NAT. A Lite image can lack both, and then the AP comes up but
# clients get no address / no route out — the "Pi network has no internet" symptom.
apt-get install -y build-essential curl git python3 iproute2 iw network-manager \
    dnsmasq-base iptables >/dev/null
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
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
ExecStart=/usr/bin/python3 ${PI_DIR}/netctl.py serve
WorkingDirectory=${PI_DIR}
Restart=always
RestartSec=2
User=root

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/dji-bridge.service <<EOF
[Unit]
Description=DJI AOA bridge (Pi jump-host)
After=network.target dji-netctl.service
Wants=dji-netctl.service

[Service]
ExecStart=/usr/bin/python3 ${PI_DIR}/bridge.py
WorkingDirectory=${PI_DIR}
Restart=always
RestartSec=2
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
    systemctl daemon-reload
    systemctl enable NetworkManager.service 2>/dev/null || true
    systemctl enable dji-netctl.service
    systemctl enable dji-bridge.service
    systemctl enable dji-update.timer
    systemctl restart NetworkManager.service 2>/dev/null || true
    systemctl restart dji-netctl.service || true
    systemctl restart dji-update.timer || true
    # Start it now too, unless dwc2 was just enabled (no UDC until the reboot).
    if [ "$NEED_REBOOT" = "0" ] && [ -e /dev/raw-gadget ]; then
        systemctl restart dji-bridge.service || true
        echo "     dji-netctl.service enabled + started"
        echo "     dji-bridge.service enabled + started"
        echo "     dji-update.timer enabled"
    else
        echo "     dji-netctl.service enabled + started"
        echo "     dji-bridge.service enabled (will start after the reboot)"
        echo "     dji-update.timer enabled"
    fi
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
