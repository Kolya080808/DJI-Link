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


def u16(b, off):
    return struct.unpack_from("<H", b, off)[0] if off + 2 <= len(b) else None


def u8(b, off):
    return b[off] if off < len(b) else None


def s8(b, off):
    return struct.unpack_from("<b", b, off)[0] if off < len(b) else None


def u32(b, off):
    return struct.unpack_from("<I", b, off)[0] if off + 4 <= len(b) else None


def s32(b, off):
    return struct.unpack_from("<i", b, off)[0] if off + 4 <= len(b) else None


# flyc_state (byte @0x1e & 0x7F). Verified against the WM160 app enum
# DataOsdGetPushCommon$FLYC_STATE (index == code); see TELEMETRY_TRUTH.md §1.
# DataOsdGetPushCommon$SDKCtrlDevice — who currently commands the FC (OSD-common @0x34).
# APP(1) means the FC accepted our virtual-stick control; RC(0) means the RC still owns it.
SDK_CTRL_DEVICE = {0: "RC", 1: "APP", 2: "ONBOARD", 3: "CAMERA"}

# DataOsdGetPushCommon$FLYC_STATE: enum find() matches the CODE (3rd ctor arg), which is
# NOT dense — 18,20,21,22,34,40,42,44,45,47,48 are unused gaps. Codes verified from the jar.
FLYC_STATE = {
    0: "Manual", 1: "Atti", 2: "Atti_CL", 3: "Atti_Hover", 4: "Hover",
    5: "GPS_Blake", 6: "GPS_Atti", 7: "GPS_CL", 8: "GPS_HomeLock", 9: "GPS_HotPoint",
    10: "AssistedTakeoff", 11: "AutoTakeoff", 12: "AutoLanding", 13: "AttiLanding",
    14: "NaviGo", 15: "GoHome", 16: "ClickGo", 17: "Joystick",
    19: "Cinematic", 23: "Atti_Limited", 24: "NaviSubMode_Draw", 25: "NaviMissionFollow",
    26: "NaviSubMode_Tracking", 27: "NaviSubMode_Pointing", 28: "PANO", 29: "Farming",
    30: "FPV", 31: "SPORT", 32: "NOVICE", 33: "FORCE_LANDING", 35: "TERRAIN_TRACKING",
    36: "PALM_CONTROL", 37: "QUICK_SHOT", 38: "TRIPOD_GPS", 39: "TRACK_HEADLOCK",
    41: "ENGINE_START", 43: "DETOUR", 46: "TIME_LAPSE", 49: "OMNI_MOVING",
    50: "POI_WITH_VISION", 51: "SMART_TRACK", 52: "LOST_POWER_FORCE_LANDING", 100: "OTHER",
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
    battery_mv: int | None = None         # pack voltage, mV (0x0D/0x02 @0x01)
    battery_ma: int | None = None         # pack current, mA, signed (0x0D/0x02 @0x05)
    battery_temp_c: float | None = None   # pack temperature, degC (0x0D/0x02 @0x11)
    remaining_flight_time_s: int | None = None  # FC estimate, seconds (separate FC push)
    altitude_m: float | None = None       # relative/baro height, OSD 0x43 @0x10 s16 x0.1
    vps_height_m: float | None = None     # ultrasonic/VPS height, OSD 0x43 @0x29 s16 x0.1
    vx: float | None = None               # OSD 0x43 @0x12 s16 x0.1
    vy: float | None = None               # OSD 0x43 @0x14 s16 x0.1
    vz: float | None = None               # vertical velocity / CLIMB RATE, @0x16 s16 x0.1
    pitch: float | None = None
    roll: float | None = None
    yaw: float | None = None
    flight_mode: int | None = None
    flight_mode_name: str | None = None
    is_flying: bool | None = None
    motors_on: bool | None = None
    ctrl_device: int | None = None      # SDKCtrlDevice: 1=APP => FC accepted our sticks
    is_recording: bool | None = None    # camera state push (0x02/0x80)
    record_time_s: int | None = None    # video record duration, seconds (climbs while recording)
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
    max_height_m: float | None = None
    home_recorded: bool | None = None
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
            f"altitude={self.altitude_m}m", f"climb={self.vz}m/s",
            f"remain_time={self.remaining_flight_time_s}s",
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
        elif pkt.cmd_set == 0x03 and pkt.cmd_id == 0x44 and len(p) >= 16:
            self.parse_home_location(p)          # DataOsdGetPushHome (home lat/lon + recorded)
        elif pkt.cmd_set == 0x0D and pkt.cmd_id == 0x02 and len(p) >= 0x14:
            self._parse_battery(p)
        elif pkt.cmd_set == 0x02 and pkt.cmd_id == 0x80 and len(p) >= 0x1f:
            self._parse_camera_state(p)

    def _parse_camera_state(self, p: bytes) -> None:
        # DataCameraGetPushStateInfo (0x02/0x80): recording flag in the byte-0 bitfield
        # (0xC0), video record duration u16 @0x1d (seconds, climbs while recording).
        st = self.state
        b0 = u8(p, 0)
        if b0 is not None:
            st.is_recording = ((b0 >> 6) & 3) in (1, 2)   # getRecordState: 3=STOP is not recording
        rt = u16(p, 0x1d)
        if rt is not None:
            st.record_time_s = rt

    def _parse_battery(self, p: bytes) -> None:
        # Smart-battery dynamic (0x0D/0x02) — DataSmartBatteryGetPushDynamicData,
        # verified byte-perfect against a real WM160 capture (see TELEMETRY_TRUTH.md §3):
        #   index u8 @0x00   voltage u32 @0x01 (mV)   current s32 @0x05 (mA, signed:
        #   negative = discharge)   full_cap u32 @0x09 (mAh)   remaining u32 @0x0D (mAh)
        #   temperature s16 @0x11 (x0.1 degC)   percent u8 @0x14
        # NOTE: remaining FLIGHT TIME is NOT here — it is a separate FC push
        #   (u16 seconds); call parse_remaining_flight_time() for that.
        st = self.state
        st.battery_mv = u32(p, 0x01)
        st.battery_ma = s32(p, 0x05)
        temp = s16(p, 0x11)
        if temp is not None:
            st.battery_temp_c = temp * 0.1
        pct = u8(p, 0x14)
        if pct is not None and 0 <= pct <= 100:
            st.battery_pct = pct
        else:
            full = u32(p, 0x09)
            rem = u32(p, 0x0D)
            if full and rem is not None and full > 0:
                st.battery_pct = min(100, round(rem / full * 100))

    def parse_remaining_flight_time(self, p: bytes) -> None:
        """FC battery-capacity / gohome-landing assessment push (cmd_set 0x03, keyed FC
        push — distinct from the 0x0D smart-battery frames). The estimated remaining
        flight time is a u16 in SECONDS at payload offset 0x00. See TELEMETRY_TRUTH.md §4."""
        t = u16(p, 0x00)
        if t is not None:
            self.state.remaining_flight_time_s = t

    def _parse_osd(self, p: bytes) -> None:
        # DataOsdGetPushCommon (cmd_set 0x03 / cmd_id 0x43). Offsets verified against the
        # app's own byte parser + native lib; see TELEMETRY_TRUTH.md §1.
        # IMPORTANT: ALTITUDE = @0x10, CLIMB RATE (vz) = @0x16 — different fields, 6 B
        # apart. Do NOT feed vz into the altitude HUD slot (that is the "altitude looks
        # like climb rate" symptom, and it is a DISPLAY-side wiring bug, not an offset bug).
        st = self.state
        alt = s16(p, 0x10)
        if alt is not None: st.altitude_m = alt * 0.1        # relative/baro height, metres
        vx, vy, vz = s16(p, 0x12), s16(p, 0x14), s16(p, 0x16)
        if vx is not None: st.vx = vx * 0.1
        if vy is not None: st.vy = vy * 0.1
        if vz is not None: st.vz = vz * 0.1                  # vertical velocity = CLIMB RATE
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
            st.is_flying = ((w >> 1) & 3) == 2   # groundOrSky==2 = flying (DataOsdGetPushCommon)
            st.motors_on = bool((w >> 3) & 1)
            st.gps_level = (w >> 18) & 0xF   # getGpsLevel: (u32@0x20 >> 0x12) & 0xF
        sats = u8(p, 0x24)                   # getGpsNum is 1 BYTE @0x24 (the "Short" is boxing,
        if sats is not None: st.satellites = sats   # not width; u16 here inflates when p[0x25]!=0)
        vps = s8(p, 0x29)                    # getSwaveHeight (VPS) is 1 signed BYTE @0x29 (s16 spilled into flyTime)
        if vps is not None: st.vps_height_m = vps * 0.1
        cd = u8(p, 0x34)                     # SDKCtrlDevice: 1=APP => our sticks accepted
        if cd is not None: st.ctrl_device = cd
        # Motor start-fail cause = u8 @0x26 & 0x7F (getMotorFailedCause). The old 0x33
        # was wrong.
        mf = u8(p, 0x26)
        if mf is not None:
            code = mf & 0x7F
            st.motor_fail_code = code
            st.motor_fail_reason = motor_fail_text(code)

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
        # LimitMaxFlightHeightInMeter = float32 @0x25 (was mis-read as u8) — the enforced
        # ceiling reads back here, so writing max_height and watching this confirms it.
        if len(p) >= 0x29:
            st.max_height_m = round(struct.unpack_from("<f", p, 0x25)[0], 1)
        w20 = u32(p, 0x20)
        if w20 is not None: st.sim_started = bool(w20 & 1)

    @staticmethod
    def rad_to_deg(v):
        return None if v is None else v * 180.0 / math.pi

    def parse_home_location(self, p: bytes) -> None:
        """DataOsdGetPushHome (cmd_set 0x03 / cmd_id 0x44). f64 radians, but the order is
        LON @+0x00, LAT @+0x08 (opposite of the OSD-general frame). Verified in
        HOME_POINT_RESEARCH_2026.md — the previous lat/lon were swapped here."""
        if len(p) >= 16:
            self.state.home_lon = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
            self.state.home_lat = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])
        if len(p) >= 0x16:                       # flags u16 @0x14, bit0 = home recorded
            self.state.home_recorded = bool(struct.unpack_from("<H", p, 0x14)[0] & 1)

    def parse_aircraft_location(self, p: bytes) -> None:
        if len(p) >= 16:
            self.state.drone_lat = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
            self.state.drone_lon = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])
