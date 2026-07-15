#!/usr/bin/env bash
# Build the raw_gadget kernel module out-of-tree for Raspberry Pi OS.
#
# Why: the Raspberry Pi kernel ships with CONFIG_USB_RAW_GADGET disabled, so
# `modprobe raw_gadget` fails. raw_gadget.c is self-contained, so we fetch the
# source matching the running kernel and build just that one module.
#
# Run: sudo bash build_raw_gadget.sh
set -e

KREL=$(uname -r)            # e.g. 6.18.34+rpt-rpi-v8
KVER=${KREL%%+*}            # e.g. 6.18.34
BRANCH="rpi-${KVER%.*}.y"   # e.g. rpi-6.18.y
BUILD=/tmp/raw_gadget_build
RAW_URL="https://raw.githubusercontent.com/raspberrypi/linux/${BRANCH}"

echo "[1/6] kernel=${KREL}  source branch=${BRANCH}"

echo "[2/6] installing build tools + kernel headers"
apt-get update -qq
apt-get install -y build-essential curl >/dev/null
if [ ! -d "/lib/modules/${KREL}/build" ]; then
    apt-get install -y "linux-headers-${KREL}" 2>/dev/null \
        || apt-get install -y linux-headers-rpi-v8 2>/dev/null \
        || apt-get install -y raspberrypi-kernel-headers 2>/dev/null \
        || true
fi
if [ ! -d "/lib/modules/${KREL}/build" ]; then
    echo "!! No kernel headers for ${KREL} (/lib/modules/${KREL}/build missing)."
    echo "   Try: sudo apt install linux-headers-\$(uname -r)"
    echo "   or install rpi-source and run it to fetch the matching kernel source."
    exit 1
fi

echo "[3/6] fetching raw_gadget.c"
rm -rf "$BUILD"; mkdir -p "$BUILD"; cd "$BUILD"
curl -fsSLO "${RAW_URL}/drivers/usb/gadget/legacy/raw_gadget.c" || {
    echo "!! Could not fetch raw_gadget.c from branch ${BRANCH}."
    echo "   Check which branches exist: https://github.com/raspberrypi/linux/branches"
    exit 1
}

echo "obj-m += raw_gadget.o" > Makefile

# The uapi header ships with the headers package regardless of the config switch,
# but fetch a local copy if this kernel's headers lack it.
if [ ! -f "/lib/modules/${KREL}/build/include/uapi/linux/usb/raw_gadget.h" ]; then
    echo "     (uapi header missing from headers pkg — fetching a local copy)"
    mkdir -p include/uapi/linux/usb
    curl -fsSLo include/uapi/linux/usb/raw_gadget.h \
        "${RAW_URL}/include/uapi/linux/usb/raw_gadget.h"
    echo 'ccflags-y := -I$(src)/include' >> Makefile
fi

echo "[4/6] compiling"
make -C "/lib/modules/${KREL}/build" M="$PWD" modules

echo "[5/6] installing + loading"
rmmod raw_gadget 2>/dev/null || true
insmod ./raw_gadget.ko
mkdir -p "/lib/modules/${KREL}/extra"
cp raw_gadget.ko "/lib/modules/${KREL}/extra/"
depmod -a
echo raw_gadget > /etc/modules-load.d/raw-gadget.conf   # load on every boot

echo "[6/6] verifying"
lsmod | grep -q raw_gadget && echo "   module loaded OK"
ls -l /dev/raw-gadget && chmod 666 /dev/raw-gadget

echo
echo ">>> Done. Now run: sudo bash setup_gadget.sh"
echo ">>> NOTE: after a kernel upgrade this module must be rebuilt (re-run this script)."
