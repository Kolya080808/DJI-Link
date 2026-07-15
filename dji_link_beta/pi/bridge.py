#!/usr/bin/env python3
"""
Bridge on the Pi Zero: AOA channel to the remote controller  <->  TCP to the laptop.

    [Laptop: keyboard/Drone API] --TCP--> [bridge.py on the Pi] --USB(AOA)--> [Remote controller] ))) [Drone]

The laptop sends ready-made DUML frames over TCP; we hand them to the remote controller via bulk IN;
everything the remote controller sends (bulk OUT) we push back into TCP. We do not parse the bytes — a transparent
transport (DUML parsing lives on the laptop, in drone.py).

Run on the Pi (after setup_gadget.sh):
    sudo python3 bridge.py --udc 20980000.usb
Default port is 9910.
"""

from __future__ import annotations
import argparse
import os
import socket
import threading

from aoa_device import AoaDevice

UDC_SYSFS = "/sys/class/udc"


def detect_udc() -> str:
    """Return the single available UDC name.

    The name is board-specific (Pi Zero 1: 20980000.usb, Pi Zero 2 W: 3f980000.usb),
    so autodetect instead of hardcoding a default.
    """
    try:
        udcs = sorted(os.listdir(UDC_SYSFS))
    except OSError:
        udcs = []
    if not udcs:
        raise SystemExit(
            f"no UDC found in {UDC_SYSFS} — run setup_gadget.sh and reboot "
            "(the dwc2 overlay must be active in peripheral mode)"
        )
    if len(udcs) > 1:
        print(f"[bridge] several UDCs {udcs}, using {udcs[0]} (override with --udc)")
    return udcs[0]


def serve(dev: AoaDevice, host: str, port: int):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    print(f"[bridge] waiting for laptop on {host}:{port}")

    while True:
        conn, addr = srv.accept()
        print(f"[bridge] connected {addr}")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        stop = threading.Event()

        # remote controller -> TCP
        def usb_to_tcp():
            while not stop.is_set():
                try:
                    data = dev.rx_queue.get(timeout=0.5)
                except Exception:
                    continue
                try:
                    conn.sendall(data)
                except OSError:
                    stop.set()
                    break

        t = threading.Thread(target=usb_to_tcp, daemon=True)
        t.start()

        # TCP -> remote controller
        try:
            while not stop.is_set():
                data = conn.recv(4096)
                if not data:
                    break
                dev.send(data)
        except OSError:
            pass
        finally:
            stop.set()
            conn.close()
            print("[bridge] laptop disconnected, waiting again")


def main():
    ap = argparse.ArgumentParser(description="Pi AOA<->TCP bridge")
    ap.add_argument("--udc", default=None,
                    help="UDC name (see /sys/class/udc/); autodetected if omitted")
    ap.add_argument("--udc-driver", default=None,
                    help="UDC driver name (defaults to the same as --udc)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9910)
    ap.add_argument("--model", default="com.dji.logiclink",
                    help="expected model from the remote controller (for logs)")
    args = ap.parse_args()

    udc = args.udc or detect_udc()
    udc_driver = args.udc_driver or udc
    print(f"[bridge] using UDC {udc}")

    dev = AoaDevice(udc_driver, udc)

    # AOA loop in a separate thread (re-enumeration phase1->phase2)
    threading.Thread(target=dev.run_forever, daemon=True).start()
    try:
        serve(dev, args.host, args.port)
    except KeyboardInterrupt:
        print("\n[bridge] exit")
    finally:
        dev.stop()


if __name__ == "__main__":
    main()
