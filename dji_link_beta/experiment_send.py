#!/usr/bin/env python3
"""
EXPERIMENT: try to SEND data to the remote controller and observe the reaction.

!!! THE DRONE IS OFF. We test only the remote controller (RC), without flight. All packets carry a CRC —
if bogus, the remote controller simply ignores them. We write nothing to non-volatile memory.

Three parts:
  1. baseline — read the sticks (cmd_set=0x06 id=0x01), we know: center is 1024.
  2. GET scan cmd_set=0x06 — empty requests by cmd_id, see who answers.
  3. write attempt — send a stick payload with several candidate cmd_ids and
     re-read the sticks: did they change / any ACK.

Run:  py -3 experiment_send.py     (Windows, auto-finds the DJI port)
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream
from probe_serial import find_dji_port

PC = 0x0A
RC = 0x0E                     # receiver, as in RomanLut's pingData
PING_CALIB = bytes.fromhex("550d04330a0e0300400601f44a")   # get calibrated sticks


def open_port():
    try:
        import serial
    except ImportError:
        print("need pyserial:  pip install pyserial"); sys.exit(2)
    port = sys.argv[1] if len(sys.argv) >= 2 else find_dji_port()
    if not port:
        print("DJI port (VID 2CA3) not found. Is the remote controller on/plugged in?"); sys.exit(1)
    return serial.Serial(port, 115200, timeout=0.05, dsrdtr=False, rtscts=False), port


def read_frames(ser, secs: float, stream: DumlStream):
    out = []
    t = time.time()
    while time.time() - t < secs:
        data = ser.read(512)
        if data:
            out.extend(stream.feed(data))
    return out


def poll_sticks(ser, stream, tries=6):
    for _ in range(tries):
        ser.write(PING_CALIB)
        for p in read_frames(ser, 0.08, stream):
            if p.cmd_set == 0x06 and p.cmd_id == 0x01 and p.sender != PC:
                pl = p.payload
                def s16(i): return (pl[i] | (pl[i+1] << 8)) if i+1 < len(pl) else None
                return {"roll": s16(2), "pitch": s16(5), "throttle": s16(8),
                        "yaw": s16(11), "camera": s16(14)}, pl
    return None, None


def main():
    ser, port = open_port()
    stream = DumlStream()
    seq = 100
    print(f"[+] port {port}\n")

    # 1) baseline
    st, raw = poll_sticks(ser, stream)
    print(f"[baseline] sticks = {st}")
    print(f"           payload = {raw.hex() if raw else None}\n")

    # 2) GET scan cmd_set=0x06 (empty requests)
    print("[scan] cmd_set=0x06, empty request over cmd_id 0x00..0x30:")
    responders = {}
    for cid in range(0x00, 0x31):
        seq += 1
        f = DumlPacket(sender=PC, receiver=RC, cmd_set=0x06, cmd_id=cid,
                       seq=seq, cmd_type=0x40).encode()
        ser.reset_input_buffer()
        ser.write(f)
        for p in read_frames(ser, 0.12, stream):
            if p.sender != PC and p.cmd_set == 0x06 and p.cmd_id == cid:
                responders[cid] = p
    if responders:
        for cid, p in sorted(responders.items()):
            print(f"   id={cid:#04x} -> reply type={p.cmd_type:#04x} "
                  f"len={len(p.payload)} data={p.payload.hex()}")
    else:
        print("   (no replies caught)")
    print()

    # 3) write attempt: send sticks (throttle up) with several candidate cmd_ids.
    #    the payload is simple: 4x uint16 LE (roll,pitch,throttle,yaw)=center except throttle,
    #    + a flag byte. This is a HYPOTHETICAL layout; the goal is to see an ACK/reaction.
    import struct
    payload = struct.pack("<HHHH", 1024, 1024, 1684, 1024) + b"\x01"  # throttle max
    print("[write] trying to send sticks (throttle=1684) with candidate cmd_ids:")
    for cid in (0x02, 0x03, 0x08, 0x09, 0x0a, 0x1c, 0x1d, 0x1e):
        seq += 1
        f = DumlPacket(sender=PC, receiver=RC, cmd_set=0x06, cmd_id=cid,
                       seq=seq, cmd_type=0x40, payload=payload).encode()
        ser.reset_input_buffer()
        ser.write(f)
        resp = None
        for p in read_frames(ser, 0.15, stream):
            if p.sender != PC and p.cmd_set == 0x06 and p.cmd_id == cid:
                resp = p
        st2, _ = poll_sticks(ser, stream, tries=3)
        tag = f"ACK type={resp.cmd_type:#04x} data={resp.payload.hex()}" if resp else "no reply"
        print(f"   id={cid:#04x}: {tag} | sticks after = {st2}")

    ser.close()
    print("\n[done] look at: (a) who answered in the scan — neighbors of the sticks command;")
    print("         (b) whether anyone ACKed the write and whether the sticks changed.")


if __name__ == "__main__":
    main()
