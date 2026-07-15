#!/usr/bin/env python3
"""
DECISIVE TEST: can we reach the flight controller (FC) via the remote controller's serial?

Conditions: drone POWERED ON and BOUND to the remote controller, PROPS REMOVED, physical remote controller nearby.

Logic: virtual-stick is a PUSH (doesn't wait for a reply), so we check FC reachability
with a command that WAITS FOR A REPLY — request_control (cmd_set 0x49, id 0x80).
  - FC replied  -> the remote controller bridges our commands into the aircraft. The serial path is alive. 🎉
  - FC silent   -> the remote controller's serial = RC-local, doesn't bridge to the aircraft.

Additionally: GetVersion to the aircraft's addresses + a couple of center stick-PUSHes.
We log ALL incoming frames and all sender addresses. At the end, release_control.

Run (Windows):  py -3 test_fc_reach.py
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream
from probe_serial import find_dji_port

PC = 0x0A
FC = 0x03
KNOWN_RC = {0x06, 0x1b}          # already known — this is the remote controller and its subsystem


def main() -> int:
    try:
        import serial
    except ImportError:
        print("pyserial required:  pip install pyserial"); return 2
    port = sys.argv[1] if len(sys.argv) >= 2 else find_dji_port()
    if not port:
        print("DJI port not found. Is the remote controller on/plugged in?"); return 1

    ser = serial.Serial(port, 115200, timeout=0.05, dsrdtr=False, rtscts=False)
    print(f"[+] {port}\n[!] props removed? drone on and bound? remote controller nearby?\n")
    stream = DumlStream()
    seq = 200
    senders_seen: set[int] = set()
    aircraft_hits: list = []

    def send(receiver, cmd_set, cmd_id, payload=b"", ack=True, label=""):
        nonlocal seq
        seq += 1
        f = DumlPacket(sender=PC, receiver=receiver, cmd_set=cmd_set, cmd_id=cmd_id,
                       seq=seq, cmd_type=0x40 if ack else 0x00, payload=payload).encode()
        ser.write(f)
        print(f"[TX {label}] recv={receiver:#04x} set={cmd_set:#04x} id={cmd_id:#04x} : {f.hex()}")

    def listen(secs, note=""):
        t = time.time()
        while time.time() - t < secs:
            data = ser.read(256)
            if not data:
                continue
            for p in stream.feed(data):
                if p.sender == PC:
                    continue
                senders_seen.add(p.sender)
                aircraft = p.sender not in KNOWN_RC
                mark = "  <== AIRCRAFT!" if aircraft else ""
                if aircraft:
                    aircraft_hits.append(p)
                # RC heartbeat (0x06/0x1e) — don't spam it
                if not (p.sender == 0x06 and p.cmd_set == 0x06 and p.cmd_id == 0x1e):
                    print(f"   [RX] {p}{mark}")

    # 1) MAIN: control request (waits for ACK)
    print(">>> 1) request_control (waiting for ACK from FC)")
    ser.reset_input_buffer()
    send(FC, 0x49, 0x80, b"\x01", ack=True, label="request_control")
    listen(1.5)

    # 2) GetVersion to the aircraft's addresses
    print("\n>>> 2) GetVersion -> FC/camera/gimbal")
    for name, rcv in (("FC", 0x03), ("camera", 0x01), ("gimbal", 0x04)):
        send(rcv, 0x00, 0x01, ack=True, label=f"GetVer {name}")
        listen(0.8)

    # 3) a couple of center stick-PUSHes (harmless — everything centered)
    print("\n>>> 3) center virtual-stick PUSH (all 1024, no movement)")
    from control import Sticks, FlightProfile, sticks_to_payload
    prof = FlightProfile()
    center = sticks_to_payload(Sticks(0, 0, 0, 0), prof)
    for _ in range(5):
        send(FC, prof.cmd_set, prof.cmd_id, center, ack=False, label="stick center")
        listen(0.2)

    # 4) return control
    print("\n>>> 4) release_control")
    send(FC, 0x49, 0x80, b"\x00", ack=True, label="release_control")
    listen(0.8)

    ser.close()
    print("\n" + "=" * 55)
    print(f"addresses that replied: {sorted(hex(s) for s in senders_seen)}")
    if aircraft_hits:
        print("🎉 AIRCRAFT REPLIED — the remote controller bridges our commands into the drone! The serial path is alive.")
        for p in aircraft_hits[:8]:
            print(f"    {p}")
    else:
        print("⚠️  Only the remote controller replied (0x06/0x1b). FC/aircraft is NOT reachable over serial.")
        print("    Likely the remote controller's VCOM is RC-local and doesn't bridge to the aircraft.")
        print("    Then flight control -> path via the app's radio session (AOA/Pi)")
        print("    or a direct connection to the drone's own USB (but that's a cable tether).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
