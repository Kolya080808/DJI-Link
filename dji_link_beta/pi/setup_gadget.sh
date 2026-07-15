#!/usr/bin/env bash
# Подготовка Raspberry Pi Zero 2 W к роли USB-устройства (gadget) через raw-gadget.
# Запуск: sudo bash setup_gadget.sh
set -e

BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt

echo "[1/4] включаю dwc2 в peripheral-режиме (нужна перезагрузка после первого раза)"
if ! grep -q "dtoverlay=dwc2" "$BOOT_CFG"; then
    echo "dtoverlay=dwc2,dr_mode=peripheral" | sudo tee -a "$BOOT_CFG"
    NEED_REBOOT=1
fi

echo "[2/4] гружу модули dwc2 + raw_gadget"
sudo modprobe dwc2 || true
sudo modprobe raw_gadget || {
    echo "!! модуль raw_gadget не найден. На свежих Raspberry Pi OS он есть;"
    echo "   если нет — нужно ядро с CONFIG_USB_RAW_GADGET=m."
    exit 1
}

echo "[3/4] проверяю UDC"
if ls /sys/class/udc/ 2>/dev/null | grep -q .; then
    echo "   UDC найден: $(ls /sys/class/udc/)"
    echo "   -> это имя передавай в bridge.py --udc"
else
    echo "!! UDC не виден. Скорее всего нужна перезагрузка (dwc2 только что включён),"
    echo "   и Pi должна быть воткнута данными в порт-хост (у Zero — порт 'USB', не 'PWR')."
    NEED_REBOOT=1
fi

echo "[4/4] права на /dev/raw-gadget"
sudo chmod 666 /dev/raw-gadget 2>/dev/null || echo "   (появится после modprobe raw_gadget)"

if [ "${NEED_REBOOT:-0}" = "1" ]; then
    echo
    echo ">>> Перезагрузи Pi (sudo reboot), затем снова запусти этот скрипт."
else
    echo
    echo ">>> Готово. Запуск моста:  sudo python3 bridge.py --udc <имя_из_/sys/class/udc>"
fi
