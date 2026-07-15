#!/usr/bin/env python3
"""
test_all.py — RUN of ALL functions one by one with pauses, to see what works.

Parts:
  SAFE (by default): identification, telemetry, photo, video, gimbal, zoom.
  FLIGHT (only with --flight + confirmation): take control, TAKEOFF (motors!),
         sticks, landing, return control.

Between steps a pause (--delay, default 3 s), with a "▶ WHAT'S HAPPENING NOW" caption.
For each step it prints whether an ACK came from the drone + telemetry.

Connection: better DIRECTLY INTO THE DRONE (USB, COM5) — replies definitely flow there.
Via the remote controller commands will arrive, but ACK/telemetry may not come back.

  py -3 test_all.py                 # safe part, auto-port
  py -3 test_all.py COM5 --delay 4  # explicit port, 4 s pause
  py -3 test_all.py --flight        # + the part with motors (will ask for confirmation)

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!  FOR --flight: PROPS REMOVED, drone on the table, physical    !!
!!  remote controller nearby (will take over), open space.       !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""

from __future__ import annotations
import sys
import threading
import time

from duml import DumlPacket
from transport import SerialTransport
from drone import Drone
from telemetry import Telemetry
from diag_codes import motor_fail_text

DJI_VID = 0x2CA3
PID_DRONE = 0x001E
PID_RC = 0x0008

# Motor failure cause (code +0x33) -> remediation advice
REMEDIATION = {
    1:  "Compass: calibrate the compass away from metal/magnets.",
    2:  "USB protection/Assistant: DISCONNECT the drone from the PC USB and fly VIA THE REMOTE CONTROLLER — over direct drone USB the motors are locked.",
    3:  "Device locked: activation needed (run DJI Fly once with an account).",
    4:  "Distance limit exceeded: get closer to the takeoff point.",
    5:  "IMU needs calibration: place the drone LEVEL and calibrate the IMU.",
    6:  "IMU serial number error: hardware — send to service.",
    7:  "IMU warming up: wait 30-60 s.",
    8:  "Compass calibration in progress: wait for it to finish.",
    9:  "IMU without attitude: place it level, restart the drone.",
    10: "No GPS in novice mode: go out to open sky OR disable novice.",
    11: "Battery cell error: install a different battery.",
    12: "No link with the battery: reseat the battery.",
    13: "Critically low voltage: charge it.",
    14: "Critically low charge: charge it.",
    15: "Low voltage: charge it.",
}
WAITABLE = {7, 8, 10}   # can wait and retry automatically


def find_port(explicit):
    if explicit:
        return explicit, "?"
    from serial.tools import list_ports
    drone = rc = None
    for p in list_ports.comports():
        if (p.vid or 0) != DJI_VID:
            continue
        if p.pid == PID_DRONE and not drone:
            drone = p.device
        elif p.pid == PID_RC and not rc:
            rc = p.device
    return (drone or rc), ("DRONE" if drone else ("RC" if rc else None))


class Tester:
    def __init__(self, port, delay):
        self.t = SerialTransport(port)
        self.d = Drone(self.t)
        self.delay = delay
        self.tele = Telemetry()
        self.recent = {}          # (set,id) -> last-seen time
        self.responders = {}      # sender_addr -> last packet from it
        self.lock = threading.Lock()
        self.d.on_packet = self._on_pkt
        self.d.start_rx()
        self.seq = 700
        self._poll_on = False

    def _on_pkt(self, p):
        with self.lock:
            self.recent[(p.cmd_set, p.cmd_id)] = time.time()
            self.responders[p.sender] = p
        self.tele.feed_packet(p)

    # background polling: keep serial alive and actively pull telemetry
    def start_polling(self):
        self._poll_on = True
        def loop():
            while self._poll_on:
                for rcv, cs, cid in ((0x1F, 0, 1), (0x03, 0, 1), (0x01, 0, 1),
                                     (0x0D, 0x0D, 0x02)):
                    self.seq += 1
                    try:
                        self.t.send(DumlPacket(sender=0x0A, receiver=rcv, cmd_set=cs,
                                    cmd_id=cid, seq=self.seq, cmd_type=0x40).encode())
                    except Exception:
                        pass
                time.sleep(0.4)
        threading.Thread(target=loop, daemon=True).start()

    def stop_polling(self):
        self._poll_on = False

    def data_report(self):
        """What the drone replied: devices + decoded version strings + OSD."""
        with self.lock:
            resp = dict(self.responders)
        names = {0x03: "FC", 0x01: "camera", 0x04: "gimbal", 0x06: "remote controller",
                 0x0D: "battery", 0x1b: "RC-sub"}
        if not resp:
            print("   (nobody replied — is this the remote controller port? data comes only from direct drone USB)")
            return
        print("   replied:")
        for addr, p in sorted(resp.items()):
            who = names.get(addr, f"{addr:#04x}")
            txt = "".join(chr(b) for b in p.payload if 32 <= b < 127)
            extra = f"  «{txt.strip()}»" if txt.strip() else f"  {p.payload.hex()[:32]}"
            print(f"     {who:8} set={p.cmd_set:#04x} id={p.cmd_id:#04x}{extra}")

    def wake(self):
        print("[*] waking the drone's serial...")
        for i in range(40):
            self.seq += 1
            self.t.send(DumlPacket(sender=0x0A, receiver=0x1F, cmd_set=0, cmd_id=1,
                                   seq=self.seq, cmd_type=0x40).encode())
            time.sleep(0.1)
            with self.lock:
                if self.recent:
                    print("[+] drone responds.\n")
                    return True
        print("[!] drone didn't respond to GetVersion (via the remote controller this is normal — no ACK comes back).\n")
        return False

    def step(self, label, action, expect=None, watch=None, delay=None):
        print(f"\n▶ {label}")
        if watch:
            print(f"   👀 {watch}")
        t0 = time.time()
        try:
            action()
        except Exception as e:
            print(f"   ✗ send error: {e}")
            return
        time.sleep(delay if delay is not None else self.delay)
        if expect is not None:
            with self.lock:
                acked = self.recent.get(expect, 0) > t0
            print(f"   {'✅ ACK from drone' if acked else '⚠️ no reply (the action may have worked without an ACK — check with your eyes)'}")

    def gimbal_sweep(self, dps, secs):
        end = time.time() + secs
        while time.time() < end:
            self.d.gimbal_speed(dps)
            time.sleep(0.1)
        for _ in range(3):
            self.d.gimbal_speed(0); time.sleep(0.05)

    def print_telemetry(self):
        s = self.tele.state
        print("   " + s.summary())
        if s.motor_fail_code:
            print(f"   ⚠️ reason motors won't start: {motor_fail_text(s.motor_fail_code)}")

    def motors_running(self) -> bool:
        return self.tele.state.motors_on is True

    def wait_motors(self, secs=3.0) -> bool:
        t = time.time()
        while time.time() - t < secs:
            if self.motors_running():
                return True
            time.sleep(0.2)
        return self.motors_running()

    def diagnose_and_fix(self, max_tries=3) -> bool:
        """Motors didn't start: name the cause and try to fix (wait/retry)."""
        for attempt in range(1, max_tries + 1):
            code = self.tele.state.motor_fail_code
            if code is None:
                print("   ⚠️ can't see the cause — no telemetry on this channel.")
                print("      Via the REMOTE CONTROLLER the drone's replies don't come back. For diagnostics")
                print("      connect the drone DIRECTLY over USB and run again.")
                return False
            if code == 0 and self.motors_running():
                return True
            print(f"   ❌ motors won't start. CAUSE: {motor_fail_text(code)}")
            advice = REMEDIATION.get(code, "unknown cause — see the code above.")
            print(f"   🔧 what to do: {advice}")
            if code in WAITABLE and attempt < max_tries:
                print(f"   ⏳ waitable cause — waiting 20 s and trying takeoff again (attempt {attempt}/{max_tries})...")
                time.sleep(20)
            elif attempt < max_tries:
                try:
                    input("   ▶ fix per the advice above and press Enter to retry takeoff (or Ctrl+C to exit)... ")
                except EOFError:
                    return False
            else:
                print("   ✗ out of attempts. Sort out the cause and run again.")
                return False
            # retry takeoff
            self.d.takeoff()
            if self.wait_motors(3.0):
                print("   ✅ motors started!")
                return True
        return self.motors_running()

    def close(self):
        self.stop_polling()
        self.d.stop()
        self.t.close()


def main() -> int:
    try:
        import serial  # noqa
    except ImportError:
        print("pyserial required:  pip install pyserial"); return 2

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flight = "--flight" in sys.argv
    delay = 3.0
    for a in sys.argv:
        if a.startswith("--delay"):
            try: delay = float(a.split("=", 1)[1]) if "=" in a else float(sys.argv[sys.argv.index(a)+1])
            except Exception: pass

    port, role = find_port(args[0] if args else None)
    if not port:
        print("DJI port not found. Plug in the drone (or remote controller) via USB and turn it on."); return 1

    tt = Tester(port, delay)
    print(f"[+] port {port} ({role}), {delay} s pause between steps\n" + "=" * 55)
    try:
        tt.wake()
        tt.start_polling()      # actively pull data from the drone in the background

        # ---------- SAFE PART ----------
        print("### SAFE PART (we don't touch the motors) ###")
        tt.step("Identification (GetVersion)",
                lambda: tt.t.send(DumlPacket(sender=0x0A, receiver=0x1F, cmd_set=0,
                                  cmd_id=1, seq=1, cmd_type=0x40).encode()),
                expect=(0x00, 0x01))

        tt.step("Data from the drone (device poll + OSD)", lambda: None, delay=max(delay, 3))
        tt.data_report()
        tt.print_telemetry()

        tt.step("Camera: PHOTO", tt.d.take_photo, expect=(0x02, 0x01),
                watch="photo indicator / shutter sound")
        tt.step("Camera: START recording", tt.d.start_record, expect=(0x02, 0x02),
                watch="REC indicator lit up?")
        tt.step("Camera: STOP recording", tt.d.stop_record, expect=(0x02, 0x02),
                watch="REC indicator went off?")

        tt.step("Gimbal: DOWN", lambda: tt.gimbal_sweep(-30, 1.5), expect=(0x04, 0x0C),
                watch="camera tilts DOWN")
        tt.step("Gimbal: UP", lambda: tt.gimbal_sweep(30, 1.5), expect=(0x04, 0x0C),
                watch="camera tilts UP")

        tt.step("Camera: ZOOM 2x", lambda: tt.d.set_zoom(2.0), expect=(0x02, 0x34),
                watch="image zoomed in?")
        tt.step("Camera: ZOOM 1x", lambda: tt.d.set_zoom(1.0), expect=(0x02, 0x34))

        # ---------- FLIGHT PART ----------
        if not flight:
            print("\n" + "=" * 55)
            print("The MOTORS/TAKEOFF part is skipped. For it: --flight (remove the props!).")
            return 0

        print("\n" + "!" * 55)
        print("!! NEXT — MOTORS AND TAKEOFF. PROPS REMOVED? Remote controller nearby? !!")
        print("!" * 55)
        ans = input("Type 'PROPS REMOVED' to continue: ").strip().lower()
        if ans not in ("props removed", "yes"):
            print("Motors part cancelled."); return 0

        print("\n### FLIGHT PART ###")
        tt.step("Take control (request_control)", tt.d.request_control,
                expect=(0x49, 0x80))
        tt.step("Hand control RC→PC", tt.d.rc_to_pc_control, expect=(0x06, 0xF1))

        # 1) TAKEOFF (starts motors) — BEFORE checking motor control
        tt.step("TAKEOFF (motors should spin up!)", tt.d.takeoff, expect=(0x03, 0x2A),
                watch="MOTORS SPINNING? (without props the drone won't take off)")
        tt.print_telemetry()

        # 2) check whether the motors started; if not — diagnostics and fix
        if not tt.wait_motors(3.0):
            print("\n[!] motors didn't start — diagnostics:")
            if not tt.diagnose_and_fix():
                print("\n[!] motors never started. Landing/returning control and exiting.")
                tt.step("LANDING (just in case)", tt.d.land, expect=(0x03, 0x2A))
                return 0

        # 3) motors running -> check control over them
        def stick_test():
            end = time.time() + 3
            while time.time() < end:
                tt.d.set_sticks(0, 0, 0, 0.25)   # light throttle
                time.sleep(0.05)
            tt.d.set_sticks(0, 0, 0, 0)
        tt.step("Sticks: light throttle (motor modulation)", stick_test,
                watch="motor RPM changes with throttle?", delay=0.5)

        # 4) checks done -> LANDING / GO-HOME
        tt.step("LANDING (land)", tt.d.land, expect=(0x03, 0x2A),
                watch="motors reduce RPM / landing")
        tt.wait_motors(0.1)
        if tt.motors_running():
            print("   motors still spinning — duplicating RTH (go home)")
            tt.step("RTH / GO HOME", tt.d.return_to_home, expect=(0x03, 0x2A))
        tt.step("Return control (release)", tt.d.release_control, expect=(0x49, 0x80))
        return 0

    except KeyboardInterrupt:
        print("\n[!] interrupt — emergency landing + return control")
        try:
            tt.d.set_sticks(0, 0, 0, 0)
            tt.d.land(); time.sleep(0.2); tt.d.release_control()
        except Exception:
            pass
        return 0
    finally:
        # always return control and recenter
        try:
            tt.d.release_control()
        except Exception:
            pass
        tt.close()
        print("\n[*] done. Control returned, state is safe.")


if __name__ == "__main__":
    raise SystemExit(main())
