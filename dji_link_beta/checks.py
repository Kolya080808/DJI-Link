#!/usr/bin/env python3
"""
checks.py — UNIFIED drone readiness checks file (read-only).

Polls the drone/remote controller with safe GET commands (empty payload, changes
nothing and moves nothing) + listens for pushes, and produces a human-readable report:
versions/serial numbers, battery, GPS/satellites, flight mode, calibration, limits and
MOST IMPORTANTLY — the reason why the motors won't start.

All cmd_set/cmd_id — from reversing libsdk_jni.so (see drone.py/telemetry.py/diag_codes.py).
Does NOT include what's already been tested live (DUML channel, sticks, gimbal, camera movement).

Where to connect:
  - DIRECTLY INTO THE DRONE (USB, VID 2CA3 PID 001E, e.g. COM5) — replies definitely come here.
  - via the remote controller (PID 0008) telemetry may NOT come back (the remote controller bridges commands
    into the drone, but doesn't return replies over serial) — then the report will be incomplete.

  py -3 checks.py            # auto-search for the drone port
  py -3 checks.py COM5       # explicit port
  py -3 checks.py --rc       # allow the remote controller port (telemetry may not arrive)
"""

from __future__ import annotations
import sys
import time

from duml import DumlPacket, DumlStream
from telemetry import Telemetry
from diag_codes import motor_fail_text, FLYC_STATE

DJI_VID = 0x2CA3
PID_DRONE = 0x001E
PID_RC = 0x0008
PC = 0x02

# Receiver devices (dev_type)
FC, CAM, GIMBAL, RC, BATT = 0x03, 0x01, 0x04, 0x06, 0x0D

# Safe GET requests (empty payload). (label, receiver, cmd_set, cmd_id)
SAFE_GETS = [
    ("GetVersion(any)",        0x1F, 0x00, 0x01),
    ("GetVersion(FC)",         FC,   0x00, 0x01),
    ("GetVersion(camera)",     CAM,  0x00, 0x01),
    ("GetVersion(gimbal)",     GIMBAL, 0x00, 0x01),
    ("serial_number",          FC,   0x00, 0x51),
    ("query_device_info",      FC,   0x00, 0x88),
    ("device_info",            FC,   0x00, 0xFF),
    ("static_cap",             FC,   0x00, 0xB7),
    ("product_config",         FC,   0x03, 0xAF),
    ("get_voltage_alert",      FC,   0x03, 0x30),
    ("get_fail_safe_action",   FC,   0x03, 0x3C),
    ("nfz_db_status",          FC,   0x03, 0xBB),
    ("nfz_db_result",          FC,   0x03, 0xBC),
    ("battery_static",         BATT, 0x0D, 0x01),
    ("battery_dynamic",        BATT, 0x0D, 0x02),
    ("battery_cells",          BATT, 0x0D, 0x03),
]


def find_port(allow_rc: bool):
    from serial.tools import list_ports
    drone = rc = None
    for p in list_ports.comports():
        if (p.vid or 0) != DJI_VID:
            continue
        if p.pid == PID_DRONE and drone is None:
            drone = p.device
        elif p.pid == PID_RC and rc is None:
            rc = p.device
    if drone:
        return drone, "DRONE"
    if allow_rc and rc:
        return rc, "RC"
    return None, None


def mark(ok):
    return "✅" if ok is True else ("❌" if ok is False else "⚠️")


def main() -> int:
    try:
        import serial
    except ImportError:
        print("pyserial required:  pip install pyserial"); return 2

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_rc = "--rc" in sys.argv
    if args:
        port, role = args[0], "?"
    else:
        port, role = find_port(allow_rc)
        if not port:
            print("Drone port (2CA3:001E) not found.")
            print("Plug in the drone via USB and turn it on. The remote controller (PID 0008) may not return telemetry — then use --rc.")
            return 1
    print(f"[+] port {port} ({role})\n")

    try:
        ser = serial.Serial(port, 115200, timeout=0.05, dsrdtr=False, rtscts=False)
    except Exception as e:
        print(f"can't open {port}: {e}"); return 1

    stream = DumlStream()
    tele = Telemetry()
    responders = {}      # (set,id) -> DumlPacket reply
    seq = 500

    def drain(secs):
        t = time.time()
        while time.time() - t < secs:
            data = ser.read(512)
            if not data:
                continue
            for pkt in stream.feed(data):
                if pkt.sender == PC:
                    continue
                responders[(pkt.cmd_set, pkt.cmd_id)] = pkt
                tele.feed_packet(pkt)
                # heuristic: OSD-general is already in feed_packet; also try special parsers
                if pkt.cmd_set == 0x03 and len(pkt.payload) >= 0x66:
                    tele.parse_osd_lowfreq(pkt.payload)

    # 1) wake the drone's serial (the Mini doesn't activate on the first GetVersion)
    print("[*] waking serial and polling (2-3 sec)...")
    for _ in range(30):
        seq += 1
        ser.write(DumlPacket(sender=PC, receiver=0x1F, cmd_set=0, cmd_id=1,
                             seq=seq, cmd_type=0x40).encode())
        drain(0.08)
        if responders:
            break

    # 2) run through the safe GETs
    for label, rcv, cs, cid in SAFE_GETS:
        seq += 1
        ser.write(DumlPacket(sender=PC, receiver=rcv, cmd_set=cs, cmd_id=cid,
                             seq=seq, cmd_type=0x40).encode())
        drain(0.15)

    # 3) listen to push telemetry for a couple more seconds
    drain(2.0)
    ser.close()

    # ---------- REPORT ----------
    st = tele.state
    print("\n" + "=" * 60)
    print("DRONE READINESS REPORT")
    print("=" * 60)

    if not responders:
        print("❌ The drone didn't respond to any request on this port.")
        print("   - right port? (drone = PID 001E). Did serial wake up?")
        print("   - via the remote controller telemetry usually does NOT come back — plug in the drone directly.")
        return 0

    print(f"Commands that replied: {sorted('%#04x/%#04x'%k for k in responders)}\n")

    # identification
    gv = responders.get((0x00, 0x01))
    if gv:
        txt = bytes(b for b in gv.payload if 32 <= b < 127).decode("ascii", "ignore")
        print(f"  device: {txt.strip() or gv.payload.hex()}")

    # telemetry from OSD (if it arrived)
    print(f"\n{mark(st.flight_mode_name is not None)} flight mode: "
          f"{st.flight_mode_name or '—'}")
    print(f"{mark(st.satellites is not None and st.satellites >= 6)} "
          f"GPS: satellites={st.satellites}  level={st.gps_level}")
    print(f"{mark(st.battery_pct is not None and (st.battery_pct or 0) > 20)} "
          f"battery: {st.battery_pct}%")
    print(f"{mark(st.home_set)} home-point set: {st.home_set}")
    print(f"   altitude={st.altitude_m}m  flying={st.is_flying}  motors={st.motors_on}")
    if st.flight_time_s is not None:
        print(f"   flight time={st.flight_time_s}s  total flights={st.total_flights}"
              f"  max height={st.max_height_m}m")
    if st.drone_lat is not None:
        print(f"   drone coordinates: {st.drone_lat:.6f}, {st.drone_lon:.6f}")

    # MOST IMPORTANT: reason the motors won't start
    print()
    if st.motor_fail_code is not None:
        ok = st.motor_fail_code == 0
        print(f"{mark(ok)} WHY THE MOTORS WON'T START: {motor_fail_text(st.motor_fail_code)}")
    else:
        print("⚠️  motor failure reason: OSD push didn't arrive (no direct channel to the FC)")

    print("\n" + "-" * 60)
    if role == "RC":
        print("Note: polling went via the REMOTE CONTROLLER — the drone's telemetry may not have come back.")
        print("For a full report, plug in the drone directly via USB.")
    print("Check is read-only — the drone's state was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
