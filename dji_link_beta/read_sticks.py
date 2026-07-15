#!/usr/bin/env python3
"""
Reading the Mavic Mini (WM160) remote controller sticks by polling over DUML.

The commands are taken from the RomanLut/MavicMiniControllerAsGamepad project (verified on
this same hardware). The drone is NOT needed — it works with just the remote controller. This proves
request/response with the remote controller and gives the real stick scale (center/min/max),
which will be needed for sending control commands.

Run:
  Windows:   py -3 read_sticks.py
  WSL/Linux: python3 read_sticks.py /dev/ttyACM0
"""

from __future__ import annotations
import sys
import time

from duml import DumlStream
from probe_serial import find_dji_port

# Ready-made valid DUML frames (sender=0x0a -> receiver=0x0e, cmd_set=0x06):
PING_CALIB = bytes.fromhex("550d04330a0e0300400601f44a")   # cmd_id=0x01 calibrated sticks
PING_RAW   = bytes.fromhex("550d04330a0e02004006278405")   # cmd_id=0x27 raw+buttons


def parse_sticks(payload: bytes):
    """Axes as little-endian shorts at RomanLut's offsets (within the payload)."""
    def s16(i):
        if i + 1 >= len(payload):
            return None
        v = payload[i] | (payload[i + 1] << 8)
        return v - 0x10000 if v & 0x8000 else v
    return {
        "roll":     s16(2),    # right stick horizontal
        "pitch":    s16(5),    # right stick vertical
        "throttle": s16(8),    # left stick vertical
        "yaw":      s16(11),   # left stick horizontal
        "camera":   s16(14),
    }


def main() -> int:
    try:
        import serial
    except ImportError:
        print("need pyserial:  pip install pyserial")
        return 2

    port = sys.argv[1] if len(sys.argv) >= 2 else find_dji_port()
    if not port:
        print("DJI port (VID 2CA3) not found. Is the remote controller on/plugged in?")
        return 1
    ser = serial.Serial(port, 115200, timeout=0.05, dsrdtr=False, rtscts=False)
    print(f"[+] {port}: polling the sticks. Move them — the numbers will change. Ctrl-C to exit.\n")

    stream = DumlStream()
    lo = {}
    hi = {}
    try:
        while True:
            ser.write(PING_CALIB)
            time.sleep(0.03)
            data = ser.read(512)
            if not data:
                continue
            for p in stream.feed(data):
                if p.cmd_set == 0x06 and p.cmd_id == 0x01 and p.sender != 0x0a:
                    st = parse_sticks(p.payload)
                    # accumulate observed min/max to estimate the range
                    for k, v in st.items():
                        if v is None:
                            continue
                        lo[k] = min(lo.get(k, v), v)
                        hi[k] = max(hi.get(k, v), v)
                    line = "  ".join(
                        f"{k}={st[k]:>6}" for k in ("throttle", "yaw", "pitch", "roll", "camera"))
                    rng = "  ".join(f"{k}[{lo[k]}..{hi[k]}]" for k in lo)
                    print(f"{line}   | ranges: {rng}")
    except KeyboardInterrupt:
        print("\n[*] exit")
        ser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
