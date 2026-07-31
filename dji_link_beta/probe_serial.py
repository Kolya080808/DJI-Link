#!/usr/bin/env python3
"""
First contact with the remote controller/drone over DUML via USB Virtual COM.

We send GetVersion (cmd_set=0, cmd_id=1) to various recipients and print ANY replies.
If we see even one parsed DUML reply, there is a bidirectional channel with the hardware,
and from there we can capture/send real commands.

Run:
  Windows:   py -3 probe_serial.py            (auto-finds the DJI port by VID 2CA3)
             py -3 probe_serial.py COM5        (or explicitly)
  WSL/Linux: python3 probe_serial.py /dev/ttyACM0

Needs pyserial:  pip install pyserial
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream

DJI_VID = 0x2CA3


def list_ports():
    from serial.tools import list_ports as lp
    return list(lp.comports())


def find_dji_port():
    """Look for a port with the DJI VID; return its name or None."""
    for p in list_ports():
        if (p.vid or 0) == DJI_VID:
            return p.device
    return None

# DUML device types (address = type | index<<5). index=0.
PC = 0x02            # app sender; 0x0A makes the FC treat us like DJI Assistant
TARGETS = {
    "any": 0x1F,     # broadcast by type — let anyone answer
    "RC": 0x06,      # remote controller
    "FC": 0x03,      # flight controller
    "camera": 0x01,
    "gimbal": 0x04,
    "battery": 0x0B,
}


def main() -> int:
    try:
        import serial  # noqa: F401
    except ImportError:
        print("need pyserial:  pip install pyserial")
        return 2
    import serial

    # which ports exist right now
    ports = list_ports()
    print("[*] available COM ports right now:")
    if not ports:
        print("    (none at all!)")
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        pid = f"{p.pid:04X}" if p.pid else "----"
        mark = "  <-- DJI" if (p.vid or 0) == DJI_VID else ""
        print(f"    {p.device:8}  VID:PID={vid}:{pid}  {p.description}{mark}")

    # port selection: argument or DJI auto-search
    if len(sys.argv) >= 2:
        port = sys.argv[1]
    else:
        port = find_dji_port()
        if not port:
            print("\n[!] DJI port (VID 2CA3) not found among those present.")
            print("    Is the remote controller on and plugged in? Appears/disappears? Replug it and run again.")
            return 1
        print(f"\n[+] auto-selected DJI port: {port}")

    try:
        ser = serial.Serial(port, 115200, timeout=0.3, dsrdtr=False, rtscts=False)
    except Exception as e:
        print(f"\n[!] failed to open {port}: {e}")
        print("    the port vanished (device dropped off) or is busy with DJI Assistant/Fly.")
        return 1
    print(f"[+] opened {port}\n")
    stream = DumlStream()
    seq = 1

    def drain(secs: float):
        t = time.time()
        got = False
        while time.time() - t < secs:
            data = ser.read(512)
            if not data:
                continue
            got = True
            print(f"   [raw {len(data)}b] {data.hex()}")
            for p in stream.feed(data):
                print(f"   [RX] {p}")
        return got

    # 1) just listen — maybe the remote controller sends something on its own
    print("[*] listening 1.5s without a request...")
    drain(1.5)

    # 2) GetVersion to the recipients
    any_reply = False
    for name, rcv in TARGETS.items():
        pkt = DumlPacket(sender=PC, receiver=rcv, cmd_set=0x00, cmd_id=0x01,
                         seq=seq, cmd_type=0x40)
        seq += 1
        frame = pkt.encode()
        print(f"\n[TX -> {name} ({rcv:#04x})] {frame.hex()}")
        ser.reset_input_buffer()
        ser.write(frame)
        if drain(1.2):
            any_reply = True

    ser.close()
    print("\n" + ("=" * 50))
    if any_reply:
        print("RESULT: the hardware answers over DUML — the channel is bidirectional! 🎉")
    else:
        print("RESULT: no replies. Possible causes:")
        print("  - the remote controller is not bound / the drone is off (for FC/camera replies)")
        print("  - a different address/version format — we'll capture it via dji-firmware-tools")
        print("  - COM is busy with another program (DJI Assistant/Fly)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
