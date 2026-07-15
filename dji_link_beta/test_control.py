#!/usr/bin/env python3
"""
SAFE control BENCH (bench test).

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!  PROPS REMOVED. Drone on the table. Keep the physical remote   !!
!!  controller nearby — its sticks take over control at any time. !!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Logic:
  - all axes default to CENTER (1024);
  - a background thread sends the current sticks to the FC (~20 Hz);
  - keys smoothly shift the axes; SPACE — instantly everything to center;
  - exit (q/Esc/Ctrl-C) — ramp to center + stop.

The virtual-stick command (cmd_set/cmd_id/receiver/layout) is currently a PLACEHOLDER in
control.FlightProfile. Until it's confirmed by reversing, the FC ignores such frames
(CRC-correct, but the "wrong" command) — that's safe. Once the exact
command arrives, we change ONLY FlightProfile and the bench is immediately live.

Run (Windows):  py -3 test_control.py
"""

from __future__ import annotations
import sys
import threading
import time

from duml import DumlPacket, DumlStream
from probe_serial import find_dji_port
from transport import SerialTransport
from drone import Drone, DEV_FC
from control import build_flight_frame, Sticks

STEP = 0.15          # axis shift step per key press
SEND_HZ = 20


def getch_factory():
    """Non-blocking key reading. Windows -> msvcrt, otherwise -> termios."""
    try:
        import msvcrt
        def getch():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                try:
                    return ch.decode("ascii", "ignore").lower()
                except Exception:
                    return ""
            return ""
        return getch
    except ImportError:
        import termios, tty, select
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        def getch():
            if select.select([sys.stdin], [], [], 0)[0]:
                return sys.stdin.read(1).lower()
            return ""
        getch._restore = lambda: termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return getch


def ping_fc(ser, stream) -> bool:
    """GetVersion to FC (0x03). True if the drone/FC is reachable (drone on and bound)."""
    f = DumlPacket(sender=0x0a, receiver=DEV_FC, cmd_set=0x00, cmd_id=0x01,
                   seq=1, cmd_type=0x40).encode()
    ser.reset_input_buffer()
    ser.write(f)
    t = time.time()
    while time.time() - t < 1.0:
        data = ser.read(256)
        if data:
            for p in stream.feed(data):
                if p.sender == DEV_FC:
                    return True
    return False


def main():
    print(__doc__)
    argv = [a for a in sys.argv[1:] if a != "--live"]
    live = "--live" in sys.argv     # whether to actually send flight frames to the FC
    port = argv[0] if argv else find_dji_port()
    if not port:
        print("DJI port (VID 2CA3) not found. Is the remote controller on/plugged in?")
        return 1
    print(f"[mode] {'LIVE — frames really go to the FC!' if live else 'DRY-RUN — flight frames are NOT sent (only shown). For live: --live'}")

    t = SerialTransport(port)
    drone = Drone(t)

    # check whether the FC is reachable (needs a powered, bound drone)
    print("[*] pinging the flight controller (needs a powered drone)...")
    fc_up = ping_fc(t.ser, DumlStream())
    print("[+] FC responds — drone is reachable." if fc_up else
          "[!] FC silent — drone off/not bound. The bench will come up, but there's nothing to control.")

    drone.start_rx()

    # current axis state
    axes = {"throttle": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0}
    lock = threading.Lock()
    running = threading.Event(); running.set()

    def sender():
        period = 1.0 / SEND_HZ
        while running.is_set():
            with lock:
                if live:
                    drone.set_sticks(axes["roll"], axes["pitch"], axes["yaw"], axes["throttle"])
                # in dry-run we send nothing — the frame is shown in the main loop
            time.sleep(period)

    threading.Thread(target=sender, daemon=True).start()

    getch = getch_factory()
    clamp = lambda v: max(-1.0, min(1.0, v))
    print("\nControl: W/S throttle  A/D yaw  I/K pitch  J/L roll")
    print("SPACE — all to center   Q/Esc — exit (with centering)\n")

    try:
        while running.is_set():
            c = getch()
            if c:
                with lock:
                    if c == "w": axes["throttle"] = clamp(axes["throttle"] + STEP)
                    elif c == "s": axes["throttle"] = clamp(axes["throttle"] - STEP)
                    elif c == "d": axes["yaw"] = clamp(axes["yaw"] + STEP)
                    elif c == "a": axes["yaw"] = clamp(axes["yaw"] - STEP)
                    elif c == "i": axes["pitch"] = clamp(axes["pitch"] + STEP)
                    elif c == "k": axes["pitch"] = clamp(axes["pitch"] - STEP)
                    elif c == "l": axes["roll"] = clamp(axes["roll"] + STEP)
                    elif c == "j": axes["roll"] = clamp(axes["roll"] - STEP)
                    elif c == " ": axes.update(throttle=0, yaw=0, pitch=0, roll=0)
                    elif c in ("q", "\x1b"): running.clear()
                with lock:
                    print(f"\r  thr={axes['throttle']:+.2f} yaw={axes['yaw']:+.2f} "
                          f"pitch={axes['pitch']:+.2f} roll={axes['roll']:+.2f}   ", end="", flush=True)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        # ramp to center + a few center frames
        print("\n[*] centering and exiting...")
        with lock:
            axes.update(throttle=0, yaw=0, pitch=0, roll=0)
        if live:
            for _ in range(10):
                drone.set_sticks(0, 0, 0, 0)
                time.sleep(0.02)
        running.clear()
        drone.stop()
        t.close()
        if hasattr(getch, "_restore"):
            getch._restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
