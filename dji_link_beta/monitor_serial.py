#!/usr/bin/env python3
"""
Live DUML monitor with change highlighting — to REVERSE-ENGINEER the stick format.

Groups frames by (sender,receiver,cmd_set,cmd_id) and prints the payload only
when it has changed, marking which bytes changed. Move one stick and you see
which bytes change. This is how we empirically find the channel layout.

Run:
  Windows:   py -3 monitor_serial.py
  WSL/Linux: python3 monitor_serial.py /dev/ttyACM0

Tip for capturing sticks (drone NOT needed, props NOT needed):
  1. Don't touch the sticks for ~2s — you'll see the "background".
  2. Move ONLY the left stick up-down (throttle) fully and back.
  3. Then the left one left-right (yaw), then the right one (pitch/roll) one at a time.
  4. Record which bytes change on each movement -> those are the channels.
"""

from __future__ import annotations
import sys
import time

from duml import DumlStream
from probe_serial import find_dji_port, list_ports, DJI_VID


def fmt_payload(prev: bytes | None, cur: bytes) -> str:
    out = []
    for i, b in enumerate(cur):
        changed = prev is None or i >= len(prev) or prev[i] != b
        cell = f"{b:02x}"
        out.append(f"[{cell}]" if changed else f" {cell} ")
    return "".join(out)


def main() -> int:
    try:
        import serial
    except ImportError:
        print("need pyserial:  pip install pyserial")
        return 2

    if len(sys.argv) >= 2:
        port = sys.argv[1]
    else:
        port = find_dji_port()
        if not port:
            print("DJI port (VID 2CA3) not found. Current list:")
            for p in list_ports():
                print(f"   {p.device}  {p.vid and format(p.vid,'04X')}:{p.pid and format(p.pid,'04X')}  {p.description}")
            return 1
    print(f"[+] monitor on {port}. Ctrl-C to exit.\n")

    ser = serial.Serial(port, 115200, timeout=0.2, dsrdtr=False, rtscts=False)
    stream = DumlStream()
    last: dict[tuple, bytes] = {}
    count: dict[tuple, int] = {}

    try:
        while True:
            data = ser.read(512)
            if not data:
                continue
            for p in stream.feed(data):
                key = (p.sender, p.receiver, p.cmd_set, p.cmd_id)
                count[key] = count.get(key, 0) + 1
                prev = last.get(key)
                if prev == p.payload:
                    continue   # no change — don't spam
                line = fmt_payload(prev, p.payload)
                print(f"{p.sender:#04x}->{p.receiver:#04x} "
                      f"set={p.cmd_set:#04x} id={p.cmd_id:#04x} "
                      f"n={count[key]:<5} {line}")
                last[key] = p.payload
    except KeyboardInterrupt:
        print("\n[*] exit")
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
