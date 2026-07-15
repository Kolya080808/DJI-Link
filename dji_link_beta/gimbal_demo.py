#!/usr/bin/env python3
"""
DEMO: move the drone's CAMERA (gimbal) with our DUML command — watch it live.

Goes through the drone's OWN USB port (VID 2CA3, PID 001E, e.g. COM5), because the remote
controller does not bridge our commands into the aircraft. The drone is on and plugged in over USB. Props don't matter
(the gimbal is not the motors). The remote controller can be left disconnected.

  py -3 gimbal_demo.py            # auto-find the drone port, SPEED mode (down-up sweep)
  py -3 gimbal_demo.py COM5       # explicit port
  py -3 gimbal_demo.py --angle    # ANGLE mode (down -90 -> exactly 0)
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream
from drone import Drone
from transport import SerialTransport

DJI_VID = 0x2CA3
PID_DRONE = 0x001E
PID_RC = 0x0008


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


def main() -> int:
    args = sys.argv[1:]
    use_angle = "--angle" in args
    portargs = [a for a in args if not a.startswith("--")]
    port = portargs[0] if portargs else find_drone_port()
    if not port:
        print("Drone port (2CA3:001E) not found. Plug the drone in over USB and turn it on.")
        return 1

    try:
        t = SerialTransport(port)
    except Exception as e:
        print(f"cannot open {port}: {e}"); return 1
    d = Drone(t)

    seen_any = []
    def on_pkt(p):
        seen_any.append(p)
        if p.cmd_set == 0x04 or p.sender == 0x04:
            print(f"   [RX gimbal] {p}")
    d.on_packet = on_pkt
    d.start_rx()

    # 1) wake the drone's serial mode (the Mini doesn't activate it on the first GetVersion)
    print(f"[+] {port}: waking the drone's serial with a repeated GetVersion...")
    for i in range(50):
        t.send(DumlPacket(sender=0x0a, receiver=0x1f, cmd_set=0, cmd_id=1,
                          seq=1 + i, cmd_type=0x40).encode())
        t.send(DumlPacket(sender=0x0a, receiver=0x03, cmd_set=0, cmd_id=1,
                          seq=100 + i, cmd_type=0x40).encode())
        time.sleep(0.1)
        if seen_any:
            print(f"[+] the drone answers (received {len(seen_any)} frames). serial is active.")
            break
    else:
        print("[!] the drone did not answer GetVersion. Trying the gimbal anyway — maybe it'll ACK.")

    print("\n>>> WATCH THE DRONE'S CAMERA <<<\n")

    if use_angle:
        print("[angle] tilt DOWN -90°...")
        for _ in range(8):
            d.gimbal_angle(-90.0, duration_s=1.0); time.sleep(0.1)
        time.sleep(2.0)
        print("[angle] return to EXACTLY 0°...")
        for _ in range(8):
            d.gimbal_angle(0.0, duration_s=1.0); time.sleep(0.1)
        time.sleep(2.0)
    else:
        def sweep(dps, secs, label):
            print(f"[speed] {label} ({dps:+d}°/s)...")
            end = time.time() + secs
            while time.time() < end:
                d.gimbal_speed(dps); time.sleep(0.1)   # ~10 Hz, like the app
            for _ in range(3):
                d.gimbal_speed(0); time.sleep(0.05)     # stop
        sweep(-30, 1.5, "camera DOWN")
        time.sleep(1.0)
        sweep(+30, 1.5, "camera UP")
        time.sleep(0.5)
        d.gimbal_speed(0)

    time.sleep(0.5)
    d.stop(); t.close()
    gimbal_acks = [p for p in seen_any if p.cmd_set == 0x04 or p.sender == 0x04]
    print("\n" + "=" * 50)
    if gimbal_acks:
        print(f"🎉 the gimbal ANSWERED ({len(gimbal_acks)} frame(s)) — command accepted!")
    elif seen_any:
        print("the drone answered DUML, but the gimbal sent no ACK. Did the camera move? "
              "If yes — the command works; if no — you may need set gimbal work mode (0x04/0x44).")
    else:
        print("the drone was completely silent on this port — wrong port / serial didn't wake up.")
    print("Did the camera move live? That's the answer. 🎯")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
