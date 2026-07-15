#!/usr/bin/env python3
"""
full_test.py — FULL run of all functions + DIAGNOSTICS "down to the culprit".

Idea: we run each test in turn. If a test didn't pass (or you're not sure) —
I ask you, and start an analysis: read telemetry, check GPS, battery,
calibration, activation, novice mode, geo — until we find the cause (for example,
why the props won't start) and what to do about it.

Interactive CLI (not a window). Flight — only with --live. Props — REMOVE them.

  py -3 full_test.py            # no hardware (sim) — logic run
  py -3 full_test.py --serial COM5   # direct drone USB (telemetry flows)
  py -3 full_test.py --pi 192.168.x.x --live
"""

from __future__ import annotations
import argparse
import sys
import threading
import time

from duml import DumlPacket, DumlStream
from drone import Drone
from telemetry import Telemetry
from diag_codes import motor_fail_text, MOTOR_FAIL_NAME


def ask(q: str) -> bool:
    try:
        return input(f"   ❓ {q} (y/n): ").strip().lower().startswith("y")
    except EOFError:
        return False


def ask_text(q: str) -> str:
    try:
        return input(f"   ▶ {q}: ").strip()
    except EOFError:
        return ""


class Harness:
    def __init__(self, transport, mode, live):
        self.t = transport
        self.mode = mode
        self.live = live
        self.d = Drone(transport)
        self.tele = Telemetry()
        self.duml = DumlStream()
        self.recent = {}
        self.lock = threading.Lock()
        self.running = True
        if mode == "pi":
            import composite, liveview
            self.demux = composite.CompositeDemux(
                on_duml=self._feed_duml,
                on_video=lambda pl: None)
        else:
            self.demux = None

    def _feed_duml(self, payload):
        for p in self.duml.feed(payload):
            with self.lock:
                if p.sender != 0x0A:
                    self.recent[(p.cmd_set, p.cmd_id)] = time.time()
            self.tele.feed_packet(p)

    def start_rx(self):
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while self.running:
            try:
                data = self.t.recv(timeout_ms=300)
            except Exception:
                break
            if not data:
                continue
            if self.demux:
                self.demux.feed(data)
            else:
                self._feed_duml(data)

    def poll_getversion(self, n=20):
        for i in range(n):
            self.t.send(DumlPacket(sender=0x0A, receiver=0x1F, cmd_set=0, cmd_id=1,
                                   seq=1 + i, cmd_type=0x40).encode())
            time.sleep(0.1)
            with self.lock:
                if self.recent:
                    return True
        return False

    def acked(self, key, since):
        with self.lock:
            return self.recent.get(key, 0) > since

    def close(self):
        self.running = False
        try:
            if self.live:
                self.d.release_control()
        except Exception:
            pass
        self.d.stop()
        self.t.close()


# ---------------------------------------------------------------- diagnostics
def diagnose_no_telemetry(h: Harness):
    print("\n🔎 DIAGNOSIS: telemetry isn't arriving.")
    print("   - Through the remote controller (COM4) replies do NOT come back — that's normal.")
    print("   - You need direct drone USB (COM5) or a path via Pi (AOA).")
    print("   - Check: is the drone on? right port? did serial wake up?")


def diagnose_motors(h: Harness):
    """Main analysis: why the props won't start. We lead down to the culprit."""
    print("\n🔎 DIAGNOSIS: motors/props won't start. Searching for the cause...")
    st = h.tele.state

    # 1) direct cause from FC
    if st.motor_fail_code is not None:
        code = st.motor_fail_code
        print(f"   ⛔ FC states the cause directly: {motor_fail_text(code)}")
        return guide_fix_for_code(h, code)

    print("   (FC didn't send a code directly — going subsystem by subsystem)")

    # 2) link
    if not h.recent:
        diagnose_no_telemetry(h)
        return "no link with the aircraft"

    # 3) GPS
    if st.satellites is not None and st.satellites < 8:
        print(f"   ⚠ GPS weak: satellites={st.satellites} (need ~8+).")
        print("     → go out to open sky, wait for fix; or take off in ATTI.")
        return "weak GPS"

    # 4) battery
    if st.battery_pct is not None and st.battery_pct < 15:
        print(f"   ⚠ Battery low: {st.battery_pct}%. → charge it.")
        return "low charge"

    # 5) mode/calibration (we ask the user — they see the status on the drone/app)
    print("   Let's check subsystems one by one (watch the drone indicators/app):")
    if not ask("Is the compass calibrated? (no compass error)"):
        print("     → calibrate the COMPASS (away from metal/magnets), then retry takeoff.")
        return "compass calibration needed"
    if not ask("Is the IMU OK? (drone was sitting level, not 'IMU warming up')"):
        print("     → place the drone LEVEL, let the IMU warm up 30-60s / calibrate the IMU.")
        return "IMU calibration/warm-up needed"
    if ask("Is novice/beginner mode on?"):
        print("     → novice requires GPS; turn off novice OR wait for GPS.")
        return "novice mode without GPS"
    if not ask("Is the drone ACTIVATED? (went through activation in the app with an account)"):
        print("     → activation needed (one-time, via DJI Fly with an account). We can't fake it.")
        return "not activated"
    if ask("Is there a geo-zone / NFZ warning in the app?"):
        print("     → geo-unlock requires a DJI license (account+server) — can't bypass locally.")
        return "geo restriction (NFZ)"

    print("   ⚠ No obvious cause found. Grab a log/send telemetry — we'll dig deeper.")
    return "cause not determined"


def guide_fix_for_code(h: Harness, code: int) -> str:
    """Hint for the code + offer to retry (the cause text already contains advice)."""
    print(f"   🔧 What to do: see the cause above — {motor_fail_text(code)}")
    if ask("Fixed it? Retry the takeoff attempt?"):
        h.d.takeoff()
        time.sleep(3)
        st = h.tele.state
        if st.motors_on:
            print("   ✅ motors started!")
            return "fixed"
        print(f"   still no. Current cause: {motor_fail_text(st.motor_fail_code) if st.motor_fail_code is not None else '—'}")
    return MOTOR_FAIL_NAME.get(code, f"code {code}")


# ---------------------------------------------------------------- tests
def run_tests(h: Harness):
    d = h.d
    results = []

    def test(name, action, watch=None, expect=None, on_fail=None, flight=False):
        print(f"\n▶ TEST: {name}")
        if flight and not h.live:
            print("   ⏭ skipped (flight test, needs --live)")
            results.append((name, "skip")); return
        if watch:
            print(f"   👀 {watch}")
        t0 = time.time()
        try:
            action()
        except Exception as e:
            print(f"   ✗ send error: {e}")
            results.append((name, "error")); return
        time.sleep(2.5)
        ok = None
        if expect is not None:
            ok = h.acked(expect, t0)
            print(f"   {'✅ ACK' if ok else '⚠ no ACK'}")
        # interactive verification
        if watch:
            ok = ask("Did it work (saw the effect)?")
        if ok is False and on_fail:
            reason = on_fail(h)
            results.append((name, f"FAIL: {reason}"))
        else:
            results.append((name, "ok" if ok else ("sent" if ok is None else "ok")))

    # --- link/telemetry ---
    print("=" * 55 + "\nSAFE TESTS\n" + "=" * 55)
    print("[*] waking and listening to telemetry...")
    conn = h.poll_getversion()
    time.sleep(2)
    st = h.tele.state
    print(f"   link: {'✅' if conn else '❌'}   "
          f"mode={st.flight_mode_name} satellites={st.satellites} battery={st.battery_pct}% motors={st.motors_on}")
    if not conn:
        diagnose_no_telemetry(h)

    # --- camera/gimbal (safe) ---
    test("Camera: photo", d.take_photo, watch="photo indicator/sound", expect=(0x02, 0x01))
    test("Camera: start recording", d.start_record, watch="recording started?", expect=(0x02, 0x02),
         on_fail=lambda h: "camera didn't respond — right channel? (video/status only via AOA/drone USB)")
    test("Camera: stop recording", d.stop_record, expect=(0x02, 0x02))
    test("Gimbal: down", lambda: [d.gimbal_speed(-30) or time.sleep(0.1) for _ in range(15)] and d.gimbal_speed(0),
         watch="camera tilted DOWN?", on_fail=lambda h: "gimbal didn't move — check channel/power")
    test("Gimbal: up", lambda: [d.gimbal_speed(30) or time.sleep(0.1) for _ in range(15)] and d.gimbal_speed(0),
         watch="camera tilted UP?")

    # --- flight (with --live) ---
    print("\n" + "!" * 55 + "\nFLIGHT TESTS (props removed!)\n" + "!" * 55)
    if h.live and ask("Continue with flight tests? PROPS REMOVED?"):
        test("Ground-station mode", lambda: d.set_ground_station_mode(True), expect=(0x03, 0x80),
             flight=True, on_fail=lambda h: "FC didn't confirm ground-station")
        test("Take control", d.request_control, expect=(0x49, 0x80), flight=True,
             on_fail=lambda h: "FC didn't grant control — try rc_to_pc / preempt")
        test("TAKEOFF (starts motors)", d.takeoff, watch="PROPS/motors spun up?",
             flight=True, on_fail=diagnose_motors)
        if h.live:
            test("Sticks: light throttle",
                 lambda: [d.set_sticks(0, 0, 0, 0.2) or time.sleep(0.05) for _ in range(40)] and d.set_sticks(0, 0, 0, 0),
                 watch="motor RPM changed with throttle?", flight=True,
                 on_fail=lambda h: "sticks have no effect — need a different variant (0x01/02) / enable?")
        test("LANDING", d.land, expect=(0x03, 0x2A), flight=True)
        test("Return control", d.release_control, expect=(0x49, 0x80), flight=True)

    # --- summary ---
    print("\n" + "=" * 55 + "\nTEST SUMMARY\n" + "=" * 55)
    for name, res in results:
        mark = "✅" if res in ("ok", "sent") else ("⏭" if res == "skip" else "❌")
        print(f"  {mark} {name}: {res}")
    fails = [r for _, r in results if r.startswith("FAIL")]
    if fails:
        print(f"\n⚠ Failures: {len(fails)}. Causes above — fix by those.")
    else:
        print("\nEverything that was checked passed (or was sent).")


def main() -> int:
    ap = argparse.ArgumentParser(description="full test + diagnostics")
    ap.add_argument("--pi", metavar="HOST[:PORT]")
    ap.add_argument("--serial", metavar="PORT")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    if args.pi:
        from transport import NetTransport, CompositeTransport
        host, _, p = args.pi.partition(":")
        t = CompositeTransport(NetTransport(host, int(p) if p else 9910)); mode = "pi"
    elif args.serial:
        from transport import SerialTransport
        t = SerialTransport(args.serial); mode = "serial"
    else:
        from transport import LogTransport
        t = LogTransport(verbose=True); mode = "sim"
        print("[sim] no hardware — testing the logic of tests/diagnostics")

    h = Harness(t, mode, args.live)
    h.start_rx()
    try:
        run_tests(h)
    finally:
        h.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
