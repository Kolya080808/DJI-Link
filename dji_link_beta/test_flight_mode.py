#!/usr/bin/env python3
"""
test_flight_mode.py — hardware-free check of the WHOLE flight-mode path in the beta.

Nothing here touches a serial port, a socket or the drone: the sim transport (LogTransport)
stands in for the link, so this runs on a bare laptop and is the fastest way to see whether a
change to the mode code still holds together.

  py -3 test_flight_mode.py          # all parts, ~2 s
  py -3 test_flight_mode.py -v       # also print every asserted value

Parts, in the order the roadmap built them:
  1. model      — mode <-> gear mapping and the firmware wire values
  2. encoder    — the SoftSwitchMode DUML frame (cmd_set 0x06 -> RC 0x06)
  3. drone      — set_flight_mode() sends that exact frame; set_horizontal_speed() does not
  4. derive     — FLYC_STATE -> user mode, including the sticky keep-last behaviour
  5. sim        — LogTransport answers a gear frame by moving the FLYC_STATE it streams back
  6. detect     — the bounded cmd_id probe locks a winner and restores state on failure
"""

from __future__ import annotations
import struct
import sys

from duml import DumlPacket
from drone import Drone
from telemetry import Telemetry, DerivedFlightMode, derived_user_mode
from transport import LogTransport
from flight_mode import (
    FlightMode, RcSoftSwitchMode, SoftSwitchCmdId, RC_CMD_SET, RC_RECEIVER,
    flight_mode_from_name, flight_mode_name, make_soft_switch_packet, soft_switch_cmd_id_from,
    soft_switch_for, soft_switch_payload, soft_switch_wire_value,
)
from soft_switch_detect import (
    SoftSwitchDetectConfig, SoftSwitchDetectHooks, detect_soft_switch_cmd_id,
    auto_detect_mode_cmd_id, expected_derived_for,
)

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_failed: list[str] = []


def ok(cond: bool, what: str) -> None:
    """Record one assertion. Keeps going after a failure so one run shows every problem."""
    if cond:
        if VERBOSE:
            print(f"    ok   {what}")
    else:
        _failed.append(what)
        print(f"    FAIL {what}")


class Capture(LogTransport):
    """Sim transport that also keeps every frame it was asked to send."""

    def __init__(self):
        super().__init__(verbose=False)
        self.frames: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.frames.append(bytes(data))
        super().send(data)

    def last(self) -> DumlPacket | None:
        return DumlPacket.decode(self.frames[-1]) if self.frames else None


class _DeafTransport:
    """Accepts frames, reports nothing back — models a link the FC ignores."""

    def send(self, data: bytes) -> None:
        pass

    def recv(self, timeout: int = 1) -> bytes:
        return b""

    def close(self) -> None:
        pass


# ---------------------------------------------------------------- 1. model
def part_model() -> None:
    print("[1] model")
    ok(soft_switch_for(FlightMode.CINE) is RcSoftSwitchMode.TRIPOD, "cine -> tripod gear")
    ok(soft_switch_for(FlightMode.NORMAL) is RcSoftSwitchMode.POSITION, "normal -> position gear")
    ok(soft_switch_for(FlightMode.SPORT) is RcSoftSwitchMode.SPORT, "sport -> sport gear")
    # The firmware ordinals, which deliberately differ from the enum declaration order.
    ok(soft_switch_wire_value(RcSoftSwitchMode.SPORT) == 0, "sport gear is wire 0")
    ok(soft_switch_wire_value(RcSoftSwitchMode.POSITION) == 1, "position gear is wire 1")
    ok(soft_switch_wire_value(RcSoftSwitchMode.TRIPOD) == 2, "tripod gear is wire 2")
    for name, mode in (("cine", FlightMode.CINE), ("CINEMA", FlightMode.CINE),
                       (" normal ", FlightMode.NORMAL), ("position", FlightMode.NORMAL),
                       ("Sport", FlightMode.SPORT)):
        ok(flight_mode_from_name(name) is mode, f"{name!r} parses as {mode.value}")
    # "tripod" is a gear, not a user mode; "max" was the old tilt/speed alias and must stay gone.
    for bad in ("tripod", "max", "", "gps", "cinematics"):
        ok(flight_mode_from_name(bad) is None, f"{bad!r} is not a user mode")
    ok(all(flight_mode_name(m) == m.value for m in FlightMode), "names are the canonical values")
    ok(soft_switch_cmd_id_from(0x11) is SoftSwitchCmdId.SET_FUNCTION_SWITCH, "0x11 is a candidate")
    for bad in (0x00, 0x05, 0x07, 0x10, 0x1A, 0xFF):
        ok(soft_switch_cmd_id_from(bad) is None, f"0x{bad:02X} is not a candidate")


# ---------------------------------------------------------------- 2. encoder
def part_encoder() -> None:
    print("[2] encoder")
    pkt = make_soft_switch_packet(RcSoftSwitchMode.SPORT, SoftSwitchCmdId.SET_MACHINE_MODE,
                                  sender=0x02, seq=0x0492)
    ok(pkt.cmd_set == RC_CMD_SET == 0x06, "frame uses the RC cmd_set 0x06")
    ok(pkt.receiver == RC_RECEIVER == 0x06, "frame targets the RC (0x06), not the app (0x02)")
    ok(pkt.sender == 0x02, "frame speaks as the mobile app")
    ok(pkt.cmd_type == 0x40, "frame asks for an ACK")
    ok(pkt.payload == struct.pack("<I", 0), "sport payload is one LE u32 = 0")
    ok(soft_switch_payload(RcSoftSwitchMode.TRIPOD) == b"\x02\x00\x00\x00", "tripod payload is 2")
    # Round-trips through the real codec, so a framing/CRC regression shows up here.
    back = DumlPacket.decode(pkt.encode())
    ok(back is not None, "frame decodes again")
    ok(back is not None and (back.cmd_set, back.cmd_id, back.payload) ==
       (0x06, int(SoftSwitchCmdId.SET_MACHINE_MODE), pkt.payload), "frame survives the round trip")


# ---------------------------------------------------------------- 3. drone command path
def part_drone() -> None:
    print("[3] drone")
    t = Capture()
    d = Drone(t)
    d.encrypt_config = False  # plaintext so the FLYC param frame below decodes

    d.set_flight_mode("sport")
    pkt = t.last()
    ok(pkt is not None, "set_flight_mode put a decodable frame on the wire")
    ok(pkt is not None and (pkt.cmd_set, pkt.receiver) == (0x06, 0x06), "mode frame is an RC frame")
    ok(pkt is not None and not (pkt.cmd_set == 0x03 and pkt.cmd_id == 0xF9),
       "mode frame is NOT the old FLYC param write")
    ok(pkt is not None and pkt.payload[0] == 0, "mode frame selects the Sport gear")

    # Byte-identical to the standalone encoder, the same invariant the C++ test locks in: two ways
    # of building the frame must not drift apart.
    seq_before = (pkt.seq if pkt else 0)
    ref = make_soft_switch_packet(RcSoftSwitchMode.SPORT, d.soft_switch_cmd_id,
                                  sender=0x02, seq=seq_before)
    ok(t.frames[-1] == ref.encode(), "Drone._cmd and make_soft_switch_packet agree byte for byte")

    # An unknown name must be refused before anything reaches the wire.
    n = len(t.frames)
    try:
        d.set_flight_mode("max")
        ok(False, "an unknown mode name raises")
    except ValueError:
        ok(True, "an unknown mode name raises")
    ok(len(t.frames) == n, "a refused mode name sends nothing")

    # Speed is a separate FLYC param write and must never emit a gear frame.
    d.set_horizontal_speed(10.0)
    pkt = t.last()
    ok(pkt is not None and (pkt.cmd_set, pkt.cmd_id) == (0x03, 0xF9), "hspeed writes a FLYC param")
    ok(pkt is not None and pkt.cmd_set != RC_CMD_SET, "hspeed never emits a gear frame")

    # smid re-targets the frame that set_flight_mode sends.
    d.set_soft_switch_cmd_id(0x19)
    d.set_flight_mode(FlightMode.NORMAL)
    pkt = t.last()
    ok(pkt is not None and pkt.cmd_id == 0x19, "the selected cmd_id reaches the wire")
    ok(pkt is not None and pkt.payload[0] == 1, "normal selects the Position gear")
    try:
        d.set_soft_switch_cmd_id(0x07)
        ok(False, "a non-candidate cmd_id is refused")
    except ValueError:
        ok(True, "a non-candidate cmd_id is refused")
    ok(d.soft_switch_cmd_id is SoftSwitchCmdId.SET_CONTROLLER_MODE,
       "a refused cmd_id leaves the previous selection alone")


# ---------------------------------------------------------------- 4. derived mode
def part_derive() -> None:
    print("[4] derive")
    ok(derived_user_mode(31) is DerivedFlightMode.SPORT, "FLYC_STATE 31 is Sport")
    ok(derived_user_mode(19) is DerivedFlightMode.CINE, "FLYC_STATE 19 is Cine")
    ok(derived_user_mode(38) is DerivedFlightMode.TRIPOD, "FLYC_STATE 38 is Tripod")
    for s in (1, 2, 3, 4, 5, 6, 7, 8, 23, 32):
        ok(derived_user_mode(s) is DerivedFlightMode.NORMAL, f"FLYC_STATE {s} is Normal")
    # Transients (17 Joystick during virtual sticks, GoHome, ...) must not be reported as a mode.
    for s in (17, 12, 33, 255, None):
        ok(derived_user_mode(s) is None, f"FLYC_STATE {s} is not a user mode")

    # Sticky: a transient state keeps the last decisive mode instead of blanking the HUD.
    tele = Telemetry()
    tele.feed_packet(_osd(31))
    ok(tele.state.user_mode is DerivedFlightMode.SPORT, "sport is picked up from a push")
    tele.feed_packet(_osd(17))
    ok(tele.state.user_mode is DerivedFlightMode.SPORT, "a Joystick transient keeps Sport")
    ok(tele.state.flight_mode == 17, "the raw FLYC_STATE still follows the transient")
    tele.feed_packet(_osd(6))
    ok(tele.state.user_mode is DerivedFlightMode.NORMAL, "a decisive state replaces it")


def _osd(flyc_state: int) -> DumlPacket:
    """Minimal OSD-common push (0x03/0x43) carrying just a FLYC_STATE at 0x1e."""
    payload = bytearray(0x34)
    payload[0x1e] = flyc_state
    return DumlPacket(sender=0x03, receiver=0x02, cmd_set=0x03, cmd_id=0x43,
                      payload=bytes(payload), seq=0, cmd_type=0x00)


# ---------------------------------------------------------------- 5. simulator loop
def part_sim() -> None:
    print("[5] sim")
    t = LogTransport(verbose=False)
    d = Drone(t)
    tele = Telemetry()

    def pump() -> DerivedFlightMode | None:
        """Consume one sim push, as the client's rx loop does."""
        pkt = DumlPacket.decode(t.recv(1))
        ok(pkt is not None, "sim recv() returns a decodable frame")
        if pkt is not None:
            tele.feed_packet(pkt)
        return tele.state.user_mode

    ok(pump() is DerivedFlightMode.NORMAL, "the sim starts in Normal")
    for name, expect in (("sport", DerivedFlightMode.SPORT),
                         ("cine", DerivedFlightMode.TRIPOD),
                         ("normal", DerivedFlightMode.NORMAL)):
        d.set_flight_mode(name)
        # Cine reads back as Tripod: on the Mini it is delivered through the Tripod gear, which is
        # exactly the hypothesis the hardware checklist still has to confirm.
        ok(pump() is expect, f"fmode {name} moves the sim mode to {expect.value}")


# ---------------------------------------------------------------- 6. cmd_id detection
def part_detect() -> None:
    print("[6] detect")
    # Pure detector against a fake aircraft that only obeys one candidate.
    winner = SoftSwitchCmdId.SET_CONTROLLER_MODE
    state = {"cmd_id": SoftSwitchCmdId.SET_MACHINE_MODE, "mode": DerivedFlightMode.NORMAL}

    hooks = SoftSwitchDetectHooks(
        apply=lambda cid, mode: (state.__setitem__("cmd_id", cid),
                                 state.__setitem__("mode", expected_derived_for(mode))
                                 if cid is winner else None),
        observe=lambda: state["mode"],
        wait=lambda: None)
    r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig(), hooks)
    ok(r.cmd_id is winner, "the detector locks the only candidate that moves the mode")
    ok(r.probes_sent > 0, "the detector reports how many probes it sent")

    # A drone that obeys nothing: the scan must fail and put the cmd_id back.
    state = {"cmd_id": SoftSwitchCmdId.SET_MACHINE_MODE, "mode": DerivedFlightMode.NORMAL}
    deaf = SoftSwitchDetectHooks(apply=lambda cid, mode: state.__setitem__("cmd_id", cid),
                                 observe=lambda: state["mode"], wait=lambda: None)
    r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig(), deaf)
    ok(r.cmd_id is None, "an unresponsive aircraft yields no winner")
    ok(state["cmd_id"] is SoftSwitchCmdId.SET_CONTROLLER_MODE,
       "the pure scan leaves the last candidate latched (restoring is the wrapper's job)")

    # The wrapper against a drone that answers nothing: the failed scan must hand the control path
    # back exactly as it found it, or every later fmode would go out on a candidate we disproved.
    deaf_drone = Drone(_DeafTransport())
    deaf_drone.set_soft_switch_cmd_id(SoftSwitchCmdId.SET_FUNCTION_SWITCH)
    quick = SoftSwitchDetectConfig(attempts_per_candidate=1, polls_per_attempt=1)
    ok(auto_detect_mode_cmd_id(deaf_drone, Telemetry(), quick, poll_interval_s=0.0) is None,
       "auto-detect fails against a silent aircraft")
    ok(deaf_drone.soft_switch_cmd_id is SoftSwitchCmdId.SET_FUNCTION_SWITCH,
       "a failed auto-detect restores the cmd_id it started from")

    # End to end on the sim, through the same wrapper the console's `detectmode` calls.
    t = LogTransport(verbose=False)
    d = Drone(t)
    tele = Telemetry()
    pkt = DumlPacket.decode(t.recv(1))
    if pkt is not None:
        tele.feed_packet(pkt)          # baseline: Normal
    import threading

    stop = threading.Event()

    def rx() -> None:
        while not stop.is_set():
            p = DumlPacket.decode(t.recv(30))
            if p is not None:
                tele.feed_packet(p)

    th = threading.Thread(target=rx, daemon=True)
    th.start()
    try:
        found = auto_detect_mode_cmd_id(d, tele)
    finally:
        stop.set()
        th.join(timeout=2)
    ok(found is not None, "auto-detect finds a cmd_id on the sim")
    ok(d.soft_switch_cmd_id == found, "the winner is locked on the Drone")
    ok(tele.state.user_mode is DerivedFlightMode.NORMAL,
       "the aircraft is left in its starting mode, never in Sport")


def main() -> int:
    for part in (part_model, part_encoder, part_drone, part_derive, part_sim, part_detect):
        part()
    print()
    if _failed:
        print(f"FAILED {len(_failed)} check(s):")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print("flight-mode: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
