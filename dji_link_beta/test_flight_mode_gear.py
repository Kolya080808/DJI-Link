#!/usr/bin/env python3
"""Hardware-free tests for the flight-mode gear path (set_flight_mode -> 0x01/0x02).

Covers:
  - gear mapping (names -> RcSoftSwitchMode wire values SPORT=0 / NORMAL=1 / CINE=2);
  - the byte layout of the mode field in the mobile-RC virtual-RC frame
    (byte[12] bits 2-3 == bits 10-11 of the u16 LE flags word == mode & 3);
  - agreement with the DJI Fly default stream (mode=1 -> byte[12] == 0x06, the value the
    app's own 0x01/0x0A TLV stick frame carries);
  - burst behaviour: bounded frame count, replacement by a newer call, cancellation by
    stop();
  - separation of concerns: a mode switch never touches the tilt/speed parameter
    (0x03/0xF9), and set_horizontal_speed never carries a gear field.

Run: python3 dji_link_beta/test_flight_mode_gear.py  (no hardware, no serial).
Evidence chain lives in reverse_docs/FLIGHT_MODE_VIRTUAL_GEAR_2026.md.
"""
from __future__ import annotations
import struct
import sys
import threading
import time
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from duml import DumlPacket
from drone import Drone
from telemetry import Telemetry

PASSED = 0


def ok(cond: bool, label: str) -> None:
    global PASSED
    if not cond:
        raise AssertionError(label)
    PASSED += 1


class FakeTransport:
    """Records everything Drone.send() puts out; nothing touches a real link."""

    def __init__(self):
        self.frames: list[bytes] = []
        self.lock = threading.Lock()

    def send(self, frame: bytes) -> None:
        with self.lock:
            self.frames.append(bytes(frame))

    def sent(self) -> list[bytes]:
        with self.lock:
            return list(self.frames)


def gear_frames(d: Drone) -> list[tuple[DumlPacket, bytes]]:
    """All 0x01/0x02 frames sent so far, decoded to (packet, payload)."""
    out = []
    for frame in d.t.sent():
        pkt = DumlPacket.decode(frame)
        if pkt.cmd_set == 0x01 and pkt.cmd_id == 0x02:
            out.append((pkt, bytes(pkt.payload)))
    return out


def mode_of(payload: bytes) -> int:
    # [11..12] u16 LE = 0x0200 | ((mode & 3) << 10)  ->  byte[12] bits 2-3.
    flags = struct.unpack("<H", payload[11:13])[0]
    assert flags & 0x0200, f"0x0200 base flag missing: {flags:#06x}"
    return (flags >> 10) & 3


def test_gear_mapping():
    ok(Drone.flight_mode_gear("sport") == 0, "sport -> gear 0 (RcSoftSwitchMode.SPORT)")
    ok(Drone.flight_mode_gear(" normal ") == 1, "normal -> gear 1 (POSITION)")
    ok(Drone.flight_mode_gear("Cine") == 2, "cine -> gear 2 (TRIPOD)")
    ok(Drone.flight_mode_gear("cinema") == 2, "cinema alias")
    ok(Drone.flight_mode_gear("position") == 1, "position alias")
    for bad in ("max", "", "tripod", "40"):
        try:
            Drone.flight_mode_gear(bad)
            ok(False, f"{bad!r} must be rejected")
        except ValueError:
            ok(True, f"{bad!r} rejected")


def test_frame_layout():
    d = Drone(FakeTransport())
    d.set_sticks_mobilerc(0.0, 0.0, 0.0, 0.0, mode=Drone.GEAR_NORMAL)
    frames = gear_frames(d)
    ok(len(frames) == 1, "one 0x01/0x02 frame sent")
    pkt, payload = frames[0]
    ok(pkt.sender == 0x02, "sender = APP 0x02")
    ok(pkt.receiver == 0x03, "receiver = FLYC 0x03")
    ok(pkt.cmd_type == 0x00, "no ack requested (stream frame)")
    ok(len(payload) == 13, "13-byte mobile-RC payload")
    ok(payload[0] == 0x00, "byte0 = 0x00")
    ok(payload[9:11] == b"\x00\x00", "bytes 9..10 = 0x0000")
    # channels centered: 4 x 1024 at 11-bit offsets 0/11/22/33
    packed = int.from_bytes(payload[1:9], "little")
    ok(all((packed >> (11 * i)) & 0x7FF == 1024 for i in range(4)),
       "all four channels centered at 1024")
    ok(mode_of(payload) == 1, "mode field carries gear 1")
    # byte-exact agreement with the DJI Fly default stream: mode=1 -> byte[12] == 0x06
    ok(payload[12] == 0x06, "mode=1 encodes to byte[12]=0x06 (matches TLV stick stream)")


def test_burst_and_replacement():
    d = Drone(FakeTransport())
    d.MODE_BURST_FRAMES = 5
    d.MODE_BURST_INTERVAL_S = 0.0
    d.set_flight_mode("sport")
    d._mode_burst_thread.join(timeout=5)
    ok(not d._mode_burst_thread.is_alive(), "burst finished on its own")
    frames = gear_frames(d)
    ok(len(frames) == 5, f"burst sent MODE_BURST_FRAMES frames (got {len(frames)})")
    ok(all(mode_of(p) == 0 for _, p in frames), "every burst frame carries gear 0")

    # A newer mode call replaces a running burst: the old burst stops early.
    d2 = Drone(FakeTransport())
    d2.MODE_BURST_FRAMES = 10_000
    d2.MODE_BURST_INTERVAL_S = 0.001
    d2.set_flight_mode("cine")
    time.sleep(0.05)
    d2.set_flight_mode("normal")
    d2._mode_burst_thread.join(timeout=5)
    cine = [p for _, p in gear_frames(d2) if mode_of(p) == 2]
    normal = [p for _, p in gear_frames(d2) if mode_of(p) == 1]
    ok(len(cine) < 100 and len(normal) >= 1,
       "old burst was replaced, new gear is being sent")

    # stop() cancels a running burst.
    d3 = Drone(FakeTransport())
    d3.MODE_BURST_FRAMES = 10_000
    d3.MODE_BURST_INTERVAL_S = 0.001
    d3.set_flight_mode("sport")
    time.sleep(0.05)
    d3.stop()
    d3._mode_burst_thread.join(timeout=5)
    ok(not d3._mode_burst_thread.is_alive(), "stop() cancelled the burst")


def test_mode_and_speed_are_separate():
    d = Drone(FakeTransport())
    d.MODE_BURST_FRAMES = 2
    d.MODE_BURST_INTERVAL_S = 0.0
    d.set_flight_mode("normal")
    d._mode_burst_thread.join(timeout=5)
    for frame in d.t.sent():
        pkt = DumlPacket.decode(frame)
        ok(not (pkt.cmd_set == 0x03 and pkt.cmd_id == 0xF9),
           "mode switch never writes the tilt/speed param")
    # ... and the speed setter never carries a gear field.
    d.set_horizontal_speed(10.0)
    for pkt, payload in gear_frames(d):
        ok(mode_of(payload) == 1, "no gear change from speed writes")


def test_mode_channel_parse():
    """The gear readback the bench test relies on: OSD dword@0x20 bits 13-14 must surface
    as state.mode_channel (getModeChannel), so a grounded gear check is observable."""
    tel = Telemetry()
    payload = bytearray(0x35)          # OSD-common minimum is 0x34
    payload[0x1e] = 4                  # flyc_state = Hover (typical grounded)
    w = 0 | (2 << 13)                  # groundOrSky=0, gear=2 (tripod)
    payload[0x20:0x24] = struct.pack("<I", w)
    tel.feed_packet(DumlPacket(sender=0x03, receiver=0x02, cmd_set=0x03, cmd_id=0x43,
                               seq=1, cmd_type=0x00, payload=bytes(payload)))
    ok(tel.state.mode_channel == 2, "OSD dword@0x20 bits 13-14 parsed as mode_channel")
    ok(tel.state.flight_mode == 4, "synthetic flyc_state parsed alongside")


def main() -> None:
    test_gear_mapping()
    test_frame_layout()
    test_burst_and_replacement()
    test_mode_and_speed_are_separate()
    test_mode_channel_parse()
    print(f"OK: {PASSED} checks passed (no hardware needed)")


if __name__ == "__main__":
    main()
