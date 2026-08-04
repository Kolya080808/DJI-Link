#!/usr/bin/env bash
# Prepare a Raspberry Pi Zero 2 W to act as a USB device (gadget) via raw-gadget.
# Run: sudo bash setup_gadget.sh
set -e

BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt

echo "[1/4] enabling dwc2 in peripheral mode (a reboot is needed the first time)"
# Match only our own uncommented line. A loose "dtoverlay=dwc2" match would hit the
# stock "[cm5] dtoverlay=dwc2,dr_mode=host" line and wrongly skip this step.
# The "[all]" header makes the setting apply to every model regardless of which
# conditional section happens to be last in the file.
if ! grep -qE '^[[:space:]]*dtoverlay=dwc2,dr_mode=peripheral' "$BOOT_CFG"; then
    printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' | sudo tee -a "$BOOT_CFG" >/dev/null
    echo "   added 'dtoverlay=dwc2,dr_mode=peripheral' under [all] in $BOOT_CFG"
    NEED_REBOOT=1
else
    echo "   already configured in $BOOT_CFG"
fi

# Make sure dwc2 is loaded at every boot (the overlay only changes the DT node).
if [ ! -f /etc/modules-load.d/dwc2.conf ]; then
    echo dwc2 | sudo tee /etc/modules-load.d/dwc2.conf >/dev/null
fi

echo "[2/4] loading the dwc2 + raw_gadget modules"
sudo modprobe dwc2 || true
sudo modprobe raw_gadget || {
    echo "!! the raw_gadget module was not found."
    echo "   The Raspberry Pi kernel ships with CONFIG_USB_RAW_GADGET disabled, so this is expected"
    echo "   on a fresh install. Build the module first:"
    echo "       sudo bash build_raw_gadget.sh"
    echo "   then re-run this script."
    exit 1
}

echo "[3/4] checking the UDC"
if ls /sys/class/udc/ 2>/dev/null | grep -q .; then
    echo "   UDC found: $(ls /sys/class/udc/)"
    echo "   -> pass this name to dji-bridge --udc"
else
    echo "!! no UDC visible. Most likely a reboot is needed (dwc2 was just enabled),"
    echo "   and the Pi must be plugged by its data port into the host (on the Zero, the 'USB' port, not 'PWR')."
    NEED_REBOOT=1
fi

echo "[4/4] permissions on /dev/raw-gadget"
sudo chmod 666 /dev/raw-gadget 2>/dev/null || echo "   (appears after modprobe raw_gadget)"

if [ "${NEED_REBOOT:-0}" = "1" ]; then
    echo
    echo ">>> Reboot the Pi (sudo reboot), then run this script again."
else
    echo
    echo ">>> Done. Start the bridge with:  sudo bin/dji-bridge --udc <name_from_/sys/class/udc>"
fi
