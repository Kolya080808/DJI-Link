"""
DJI Mavic Mini (WM160) telemetry decoder — from reversing libsdk_jni.so.

The main source is the OSD push from the flight controller (cmd_set=0x03). The
structure matches the classic flyc_osd_general; the offsets are byte offsets in the
payload (taken from the disassembly of the Key<Field>Push decoders). All HIGH-confidence
on the offsets; enum names are standard DJI (MED).

Usage: DumlStream -> DumlPacket -> feed_packet(pkt) -> updates OsdState.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
import struct


def s16(b, off):
    return struct.unpack_from("<h", b, off)[0] if off + 2 <= len(b) else None


def u8(b, off):
    return b[off] if off < len(b) else None


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0] if off + 4 <= len(b) else None


# flyc_state (byte +0x1e & 0x7F) — standard DJI values
FLYC_STATE = {
    0: "MANUAL", 1: "ATTI", 3: "ATTI_HOVER", 4: "HOVER", 5: "GPS_BLAKE",
    6: "GPS_ATTI", 7: "GPS_CRUISE", 8: "GPS_HOME_LOCK", 9: "GPS_HOT_POINT",
    10: "ASSISTED_TAKEOFF", 11: "AUTO_TAKEOFF", 12: "AUTO_LANDING",
    15: "GO_HOME", 17: "JOYSTICK", 33: "GPS_ATTI_WRISTBAND", 40: "CLICK_GO",
}

# Motor start failure cause (payload +0x33) — standard DJI enum (low codes)
# The motor-failure cause is decoded by diag_codes.motor_fail_text, which walks the whole
# chain (name -> DiagnosticCode -> code text) from the tables reversed out of libsdk_jni.
# Keeping a second, shorter copy of that table here only invited them to drift apart.
from diag_codes import motor_fail_text


@dataclass
class OsdState:
    satellites: int | None = None
    gps_level: int | None = None          # 0..5
    battery_pct: int | None = None
    altitude_m: float | None = None
    vx: float | None = None
    vy: float | None = None
    vz: float | None = None
    pitch: float | None = None
    roll: float | None = None
    yaw: float | None = None
    flight_mode: int | None = None
    flight_mode_name: str | None = None
    is_flying: bool | None = None
    motors_on: bool | None = None
    home_set: bool | None = None
    motor_fail_code: int | None = None
    motor_fail_reason: str | None = None
    imu_fail_code: int | None = None
    # from OSD low-freq push
    flight_time_s: int | None = None
    total_flights: int | None = None
    sim_started: bool | None = None
    near_height_limit: bool | None = None
    near_dist_limit: bool | None = None
    max_height_m: int | None = None
    # position (radians -> degrees)
    home_lat: float | None = None
    home_lon: float | None = None
    drone_lat: float | None = None
    drone_lon: float | None = None
    updated: set = field(default_factory=set)

    def summary(self) -> str:
        m = self.flight_mode_name or self.flight_mode
        parts = [
            f"mode={m}",
            f"satellites={self.satellites}", f"gps={self.gps_level}",
            f"battery={self.battery_pct}%",
            f"altitude={self.altitude_m}m",
            f"flying={self.is_flying}", f"motors={self.motors_on}",
            f"home={self.home_set}",
        ]
        if self.motor_fail_code:
            parts.append(f"!MOTOR_START_FAIL_CAUSE={self.motor_fail_reason}({self.motor_fail_code})")
        return "  ".join(str(p) for p in parts)


class Telemetry:
    """Accumulates fields from different pushes into a single OsdState.

    NOTE: the OSD push cmd_id is not statically fixed (string-based pub/sub). So we
    recognize the OSD structure heuristically: a push from the FC (sender=0x03) with a
    payload of sufficient length whose byte +0x1e&0x7F yields a valid flyc mode. This is
    a hypothesis for live parsing; we'll confirm/adjust on the first dump of a real push.
    """

    def __init__(self):
        self.state = OsdState()

    def feed_packet(self, pkt) -> None:
        p = pkt.payload
        # OSD general from the FC. Identify by cmd_id 0x43 (not "any FC packet ≥52 B",
        # which caught unrelated 0x03 messages and produced garbage). The push originates
        # from the FC side, which appears as sender 0x03 or 0x09 depending on the build.
        if pkt.cmd_set == 0x03 and pkt.cmd_id == 0x43 and len(p) >= 0x34:
            self._parse_osd(p)
        elif pkt.cmd_set == 0x0D and pkt.cmd_id == 0x02 and len(p) >= 0x14:
            self._parse_battery(p)

    def _parse_battery(self, p: bytes) -> None:
        # Smart-battery dynamic (0x0D/0x02), calibrated against a real WM160 capture:
        #   voltage  u32 @0x01 (mV)   current s32 @0x05 (mA)
        #   full_cap u32 @0x09 (mAh)  remaining u32 @0x0D (mAh)   percent u8 @0x14
        # (percent 0x50=80 matched remaining/full = 1723/2154 = 80%).
        st = self.state
        pct = u8(p, 0x14)
        if pct is not None and 0 <= pct <= 100:
            st.battery_pct = pct
        else:
            full = u32(p, 0x09)
            rem = u32(p, 0x0D)
            if full and rem is not None and full > 0:
                st.battery_pct = min(100, round(rem / full * 100))

    def _parse_osd(self, p: bytes) -> None:
        st = self.state
        alt = s16(p, 0x10)
        if alt is not None: st.altitude_m = alt * 0.1
        vx, vy, vz = s16(p, 0x12), s16(p, 0x14), s16(p, 0x16)
        if vx is not None: st.vx = vx * 0.1
        if vy is not None: st.vy = vy * 0.1
        if vz is not None: st.vz = vz * 0.1
        pi, ro, ya = s16(p, 0x18), s16(p, 0x1a), s16(p, 0x1c)
        if pi is not None: st.pitch = pi * 0.1
        if ro is not None: st.roll = ro * 0.1
        if ya is not None: st.yaw = ya * 0.1
        mode = u8(p, 0x1e)
        if mode is not None:
            st.flight_mode = mode & 0x7F
            st.flight_mode_name = FLYC_STATE.get(st.flight_mode, f"?{st.flight_mode}")
        w = u32(p, 0x20)
        if w is not None:
            st.is_flying = (w & 0x0E) != 0
            st.motors_on = bool((w >> 3) & 1)
        sats = u8(p, 0x24)
        if sats is not None: st.satellites = sats
        gl = u32(p, 0x20)
        if gl is not None: st.gps_level = (gl >> 18) & 0xF
        mf = u8(p, 0x33)
        if mf is not None:
            st.motor_fail_code = mf
            st.motor_fail_reason = motor_fail_text(mf)

    def parse_osd_lowfreq(self, p: bytes) -> None:
        """OSD low-freq push (same cmd_set 0x03, different id/structure).
        Call only when the payload is recognized as lowfreq (guard @0x61 != 0)."""
        st = self.state
        if len(p) < 0x66 or u8(p, 0x61) in (None, 0):
            return
        ta = s16(p, 0x10)
        if ta is not None: st.altitude_m = st.altitude_m  # takeoff alt is here; don't mix up
        ft = struct.unpack_from("<H", p, 0x64)[0]
        tf = struct.unpack_from("<H", p, 0x62)[0]
        st.flight_time_s = ft
        st.total_flights = tf
        w14 = struct.unpack_from("<H", p, 0x14)[0]
        st.near_height_limit = bool((w14 >> 5) & 1)
        st.near_dist_limit = bool((w14 >> 4) & 1)
        mh = u8(p, 0x25)
        if mh is not None: st.max_height_m = mh
        w20 = u32(p, 0x20)
        if w20 is not None: st.sim_started = bool(w20 & 1)

    @staticmethod
    def rad_to_deg(v):
        return None if v is None else v * 180.0 / math.pi

    def parse_home_location(self, p: bytes) -> None:
        """Home lat/lon push: f64 radians @+0x00/+0x08."""
        if len(p) >= 16:
            self.state.home_lat = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
            self.state.home_lon = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])

    def parse_aircraft_location(self, p: bytes) -> None:
        if len(p) >= 16:
            self.state.drone_lat = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
            self.state.drone_lon = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])
