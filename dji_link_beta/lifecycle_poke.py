#!/usr/bin/env python3
"""
FULL-CYCLE MONITOR: run it BEFORE powering on the devices.

Order:
  1) start this script;
  2) turn on the drone;
  3) turn on the remote controller;
  4) plug both in over USB;
  5) let them synchronize.
The script itself catches DJI ports appearing, WAKES the drone's serial mode
(a repeated GetVersion — on the Mini it doesn't activate on the first try), logs
all DUML during boot/sync, and continuously polls both devices,
showing WHO answers and WHEN.

DJI ports (VID 2CA3):  PID 0008 = remote controller (RC),  PID 001E = drone.

Run (Windows):  py -3 lifecycle_poke.py
"""

from __future__ import annotations
import struct
import sys
import time

from duml import DumlPacket, DumlStream


def gimbal_speed_payload(pitch_dps: float) -> bytes:
    """Gimbal SPEED command (cmd_set 0x04, id 0x0C) — the one that works over the drone's USB."""
    return struct.pack("<hhh", 0, 0, int(round(pitch_dps * 10))) + bytes([0x81, 0x00])

DJI_VID = 0x2CA3
PID_RC = 0x0008
PID_DRONE = 0x001E
PC = 0x0A
# addresses we poll: any + aircraft (FC/camera/gimbal/battery) + remote controller
POLL_RECEIVERS = (0x1F, 0x03, 0x01, 0x04, 0x0B, 0x06)


def dji_ports():
    from serial.tools import list_ports
    out = []
    for p in list_ports.comports():
        if (p.vid or 0) == DJI_VID:
            role = "RC" if p.pid == PID_RC else ("DRONE" if p.pid == PID_DRONE
                                                 else f"?{(p.pid or 0):04x}")
            out.append((p.device, role))
    return out


def main() -> int:
    try:
        import serial
    except ImportError:
        print("need pyserial:  pip install pyserial")
        return 2

    t0 = time.time()
    def ts():
        return f"{time.time() - t0:7.1f}s"

    print(__doc__)
    print(f"[{ts()}] waiting for DJI ports... turn on the drone, then the remote controller, plug both in. Ctrl-C to exit.\n")

    ports = {}          # name -> dict(ser, role, stream, seq, senders)
    last_poke = 0.0
    last_gimbal = 0.0
    gimbal_started = None   # when we started sending the gimbal command through the remote controller

    try:
        while True:
            present = dict(dji_ports())

            # new ports
            for name, role in present.items():
                if name not in ports:
                    try:
                        ser = serial.Serial(name, 115200, timeout=0.02,
                                            dsrdtr=False, rtscts=False)
                    except Exception:
                        continue    # not ready yet — we'll catch it next round
                    ports[name] = dict(ser=ser, role=role, stream=DumlStream(),
                                       seq=300, senders=set())
                    print(f"[{ts()}] +++ port appeared {name}  role={role}")

            # disappeared
            for name in list(ports):
                if name not in present:
                    print(f"[{ts()}] --- port gone {name} ({ports[name]['role']})")
                    try:
                        ports[name]["ser"].close()
                    except Exception:
                        pass
                    del ports[name]

            # read from all
            for name, st in ports.items():
                try:
                    data = st["ser"].read(512)
                except Exception:
                    data = b""
                if not data:
                    continue
                for p in st["stream"].feed(data):
                    if p.sender == PC:
                        continue
                    # noisy remote controller heartbeat 0x06/0x1e — note it once
                    if p.sender == 0x06 and p.cmd_set == 0x06 and p.cmd_id == 0x1e:
                        if "hb" not in st["senders"]:
                            st["senders"].add("hb"); st["senders"].add(0x06)
                            print(f"[{ts()}] {st['role']:5} {name}: remote controller heartbeat 0x06 (I'll stay quiet about it from now on)")
                        continue
                    new = p.sender not in st["senders"]
                    st["senders"].add(p.sender)
                    tag = "  <== NEW address answering!" if new else ""
                    if new and st["role"] == "DRONE" and p.sender in (0x03, 0x01, 0x04, 0x0b):
                        tag = "  <== 🎉 AIRCRAFT answers directly from the drone!"
                    print(f"[{ts()}] {st['role']:5} {name} [RX] {p}{tag}")

            now = time.time()

            # (a) GetVersion poll about every ~0.4s — wakes the drone's serial + pokes everyone
            if now - last_poke > 0.4:
                last_poke = now
                for name, st in ports.items():
                    for rcv in POLL_RECEIVERS:
                        st["seq"] += 1
                        f = DumlPacket(sender=PC, receiver=rcv, cmd_set=0x00,
                                       cmd_id=0x01, seq=st["seq"], cmd_type=0x40).encode()
                        try:
                            st["ser"].write(f)
                        except Exception:
                            pass

            # (b) MAIN TEST: drive the gimbal THROUGH THE REMOTE CONTROLLER (~10 Hz), down/up sweep.
            #     If the remote controller bridges — the drone's camera moves live.
            rc_ports = [st for st in ports.values() if st["role"] == "RC"]
            if rc_ports and now - last_gimbal > 0.1:
                last_gimbal = now
                if gimbal_started is None:
                    gimbal_started = now
                    print(f"[{ts()}] >>> sending the GIMBAL command THROUGH THE REMOTE CONTROLLER — WATCH THE DRONE'S CAMERA <<<")
                # sweep: 1.5s down, 1.5s up, repeat
                phase = (now - gimbal_started) % 3.0
                pitch = -30 if phase < 1.5 else 30
                payload = gimbal_speed_payload(pitch)
                for st in rc_ports:
                    st["seq"] += 1
                    f = DumlPacket(sender=PC, receiver=0x04, cmd_set=0x04, cmd_id=0x0C,
                                   seq=st["seq"], cmd_type=0x40, payload=payload).encode()
                    try:
                        st["ser"].write(f)
                    except Exception:
                        pass

            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[{ts()}] ==== SUMMARY ====")
        if not ports:
            print("   no ports remained open.")
        for name, st in ports.items():
            addrs = sorted(hex(s) for s in st["senders"] if isinstance(s, int))
            print(f"   {name} ({st['role']}): addresses that answered {addrs}")
            try:
                st["ser"].close()
            except Exception:
                pass
        print("\n   THE KEY QUESTION: did the CAMERA move while the gimbal command was being sent through the remote controller?")
        print("   - the camera twitched OR the RC port got an ACK from 0x04 → the remote controller BRIDGES commands into the drone 🎉")
        print("   - the camera stood still and the aircraft was silent → the remote controller does NOT bridge over serial (the AOA/Pi path is needed).")
        print("   (If the DRONE port is open and shows 0x03/0x04/0x01 — the drone is reachable directly over its own USB.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
