#!/usr/bin/env python3
"""
video_liveview.py — attempt to get video from the drone over DIRECT USB (serial).

Hypothesis: liveview is a multiplexed DUML channel; the drone's direct USB is also DUML.
We send video-start commands and DUMP everything incoming, looking for H.264 (start codes) and
DUML frames. H.264 shows up → video comes over this port (no Pi). No → AOA/Pi is needed.

Start commands (from reverse-engineering, payloads partly hypothetical):
  0x02/0x09 set_liveview_source_camera
  0x08/0x41 dm368_send_decode_capability
  0x08/0x42 dm368 framerate ability
  0x08/0x69 liveview_priority_bandwidth

  py -3 video_liveview.py            # auto drone port
  py -3 video_liveview.py COM5 20    # port + capture seconds
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream

DJI_VID = 0x2CA3
PID_DRONE = 0x001E
PID_RC = 0x0008
PC = 0x0A
CAM, DM368 = 0x01, 0x08


def find_drone_port():
    from serial.tools import list_ports
    cand = None
    for p in list_ports.comports():
        if (p.vid or 0) == DJI_VID:
            if p.pid == PID_DRONE:
                return p.device
            if p.pid != PID_RC and cand is None:
                cand = p.device
    return cand


def h264_scan(buf: bytes):
    """Count H.264 NAL start codes and their types."""
    types = {}
    i = 0
    n = len(buf)
    while True:
        j = buf.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        if j + 3 < n:
            nal = buf[j + 3] & 0x1F
            types[nal] = types.get(nal, 0) + 1
        i = j + 3
    return types


NAL_NAMES = {1: "P-slice", 5: "IDR(keyframe)", 6: "SEI", 7: "SPS", 8: "PPS", 9: "AUD"}


def main() -> int:
    try:
        import serial
    except ImportError:
        print("need pyserial:  pip install pyserial"); return 2

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    port = args[0] if args and not args[0].isdigit() else find_drone_port()
    secs = 15
    for a in args:
        if a.isdigit():
            secs = int(a)
    if not port:
        print("drone port (2CA3:001E) not found. Plug the drone in over USB, turn it on."); return 1

    def open_ser(p):
        return serial.Serial(p, 115200, timeout=0.05, dsrdtr=False, rtscts=False)

    ser = open_ser(port)
    print(f"[+] {port}: trying to start liveview and listening {secs} s...\n")
    seq = [800]

    def send(rcv, cs, cid, pl=b"", ack=True):
        seq[0] += 1
        f = DumlPacket(sender=PC, receiver=rcv, cmd_set=cs, cmd_id=cid,
                       seq=seq[0], cmd_type=0x40 if ack else 0x00, payload=pl).encode()
        try:
            ser.write(f)
        except Exception:
            pass

    def start_cmds():
        # gently: source first, listen; then the rest
        send(CAM, 0x02, 0x09, b"\x01")
        time.sleep(0.15)
        send(DM368, 0x08, 0x41, b"\x01\x00\x00\x00")   # decode capability (hypothesis)
        send(DM368, 0x08, 0x42, b"\x1e")               # framerate (hypothesis)
        send(DM368, 0x08, 0x69, b"\x00")               # bandwidth (hypothesis)

    # wake it up (without flooding)
    for _ in range(8):
        send(0x1F, 0x00, 0x01); time.sleep(0.08)
    print("[*] sending video-start commands...")
    start_cmds()

    # capture with port re-opening on drop-off
    dump = bytearray()
    stream = DumlStream()
    duml_frames = 0
    duml_senders = set()
    drops = 0
    t = time.time()
    last_start = t
    while time.time() - t < secs:
        try:
            data = ser.read(4096)
        except Exception as e:
            drops += 1
            print(f"   [!] port dropped off ({e.__class__.__name__}); reopening... (#{drops})")
            try: ser.close()
            except Exception: pass
            time.sleep(1.0)
            newp = find_drone_port() or port
            for _ in range(30):
                try:
                    ser = open_ser(newp); break
                except Exception:
                    time.sleep(0.5)
            else:
                print("   couldn't reopen the port — the drone is rebooting USB. Stopping.")
                break
            print(f"   reopened {newp}")
            for _ in range(5):
                send(0x1F, 0x00, 0x01); time.sleep(0.05)
            start_cmds()
            last_start = time.time()
            continue
        if data:
            dump += data
            for p in stream.feed(data):
                duml_frames += 1
                duml_senders.add(p.sender)
        if time.time() - last_start > 2:   # keepalive
            send(CAM, 0x02, 0x09, b"\x01")
            last_start = time.time()
    try: ser.close()
    except Exception: pass
    if drops:
        print(f"\n[!] the port dropped off {drops} times — the drone's direct USB is unstable for this.")

    # save the dump
    path = "drone_dump.bin"
    with open(path, "wb") as f:
        f.write(dump)

    nal = h264_scan(bytes(dump))
    print("\n" + "=" * 55)
    print(f"bytes received: {len(dump)}   DUML frames: {duml_frames}"
          f"   answered: {sorted(hex(s) for s in duml_senders)}")
    print(f"dump saved: {path}")
    if nal:
        print("\n🎉 FOUND H.264 NAL units — video comes over this port!")
        for t_, c in sorted(nal.items()):
            print(f"   type {t_} {NAL_NAMES.get(t_,'?'):14} x{c}")
        print("\n→ can decode:  ffplay -f h264 -i drone_dump.bin")
    else:
        print("\n❌ no H.264 found — video does not come over direct USB.")
        print("   So liveview is only over AOA (through the remote controller) — the Pi-as-phone path is needed.")
        print("   (The dump is saved anyway — the framing agent will look at what kind of data is in it.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
