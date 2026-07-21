"""
High-level Drone API for the DJI Mavic Mini 1 (WM160).

A single point through which ANY command source (keyboard now, neural net
later, a mission script) controls the drone and reads telemetry. Underneath is Transport
(AOA / MITM / loopback), the protocol is DUML.

The project's goal is to implement the functions of BOTH the app AND the RC, i.e. to do
everything from the PC that the phone+RC do together.

!!! The cmd_set/cmd_id/payload values are STRUCTURAL STUBS. The exact codes for WM160
must be captured from real traffic (MITM phone<->RC) or by further reversing libsdk_jni.so.
Each command is a separate method, so codes can be refined one at a time without breaking
the upper layers. Below are the typical DJI CmdSets as a starting point.
"""

from __future__ import annotations
from dataclasses import dataclass
import struct
import threading

from duml import DumlPacket, DumlStream
from transport import Transport
from control import Sticks, FlightProfile, sticks_to_payload

DEV_APP = 0x02     # us = the MOBILE APP address (the drone pushes to 0x02 on the wire).
# 0x0a is the PC/DJI-Assistant address; sending as 0x0a makes the FC think a debug
# assistant is attached and it locks the motors (MotorStartFailedCause=2 AssistantProtected).
DEV_RC = 0x02      # remote controller
DEV_FC = 0x03      # flight controller
DEV_GIMBAL = 0x04
DEV_CAMERA = 0x01
DEV_DM368 = 0x08   # video board/transcoder (receiver of dm368 commands, verify on HW)

# Typical DJI CmdSets (a starting point for WM160, to verify by reversing):
CMDSET_COMMON = 0x00
CMDSET_CAMERA = 0x02
CMDSET_FLIGHT = 0x04     # flight controller
CMDSET_GIMBAL = 0x04     # (separate on some models)


@dataclass
class DroneState:
    """What we extract from telemetry (filled in as the payloads are reversed)."""
    connected: bool = False
    battery_pct: int | None = None
    altitude_m: float | None = None
    gps_sats: int | None = None
    flying: bool | None = None


class Drone:
    def __init__(self, transport: Transport, profile: FlightProfile | None = None):
        self.t = transport
        self.profile = profile or FlightProfile()
        self.state = DroneState()
        # Encrypt FC config/param frames (needed on the app/radio path). The direct-USB
        # (DJI Assistant 2) path uses plaintext — set False for serial.
        self.encrypt_config = True
        self._seq = 0
        self._shutter_denom = None   # last user-set 1/N shutter (None = auto); see set_iso/set_shutter
        self._stream = DumlStream()
        self._rx_thread: threading.Thread | None = None
        self._running = False
        self.on_packet = None     # callback(DumlPacket) -> None

    # ---- low level ----
    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) & 0xFFFF
        return self._seq

    def _cmd(self, cmd_set: int, cmd_id: int, payload: bytes = b"",
             receiver: int = DEV_FC, ack: bool = True) -> None:
        pkt = DumlPacket(
            sender=DEV_APP, receiver=receiver,
            cmd_set=cmd_set, cmd_id=cmd_id,
            seq=self._next_seq(),
            cmd_type=0x40 if ack else 0x00,
            payload=payload,
        )
        frame = pkt.encode()
        # FLYC config/param commands (0x03/0xF0, 0xF7-0xFA) must be SIMPLE-encrypted or the
        # FC silently drops them; flight/OSD 0x03 commands stay plaintext.
        if self.encrypt_config and cmd_set == 0x03 and cmd_id in (0xF0, 0xF7, 0xF8, 0xF9, 0xFA):
            from duml import encrypt_frame
            frame = encrypt_frame(frame)
        self.t.send(frame)

    # ---- background telemetry reception ----
    def start_rx(self) -> None:
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def stop(self) -> None:
        self._running = False

    def _rx_loop(self) -> None:
        while self._running:
            try:
                data = self.t.recv(timeout_ms=500)
            except Exception:
                break                       # port closed/gone — quietly exit
            if not data:
                continue
            self.state.connected = True
            for pkt in self._stream.feed(data):
                self._dispatch(pkt)

    def _dispatch(self, pkt: DumlPacket) -> None:
        # TODO: parse specific telemetry payloads (battery/altitude/GPS)
        if self.on_packet:
            self.on_packet(pkt)

    # ==========================================================
    # RC FUNCTIONS (flight control)
    # ==========================================================
    def set_sticks(self, roll: float, pitch: float, yaw: float, throttle: float) -> None:
        """Virtual sticks [-1..1]. special_tlv PUSH to FC (no ACK)."""
        s = Sticks(roll, pitch, yaw, throttle).clamp()
        self._cmd(self.profile.cmd_set, self.profile.cmd_id,
                  sticks_to_payload(s, self.profile), ack=False)

    # Request/release control (required BEFORE the sticks).
    # SendJoystickControlAuthPack: cmd_set=0x49, cmd_id=0x80, payload=1-byte flag.
    def request_control(self) -> None:
        # VIRTUAL_STICK_NATIVE.md: the native auth pack targets receiver 0x00 (retry 0x03
        # if unrouted). Send both so control authority is actually granted (the #1 reason
        # the FC ignores sticks). 1 = obtain.
        self._cmd(0x49, 0x80, b"\x01", receiver=0x00)
        self._cmd(0x49, 0x80, b"\x01", receiver=DEV_FC)

    def release_control(self) -> None:
        self._cmd(0x49, 0x80, b"\x00", receiver=0x00)
        self._cmd(0x49, 0x80, b"\x00", receiver=DEV_FC)

    # FLYC NavigationSwitch (cmd_set 0x03, cmd_id 0x80, 1 byte) — the real MSDK
    # enable/disable for app control. OPEN_GROUND_STATION=1 flips SDKCtrlDevice->APP;
    # CLOSE_GROUND_STATION=2 returns control to the RC (SDKCtrlDevice->RC). Sending 0 does
    # NOT release — that was the "stuck on APP" bug. (VIRTUAL_STICK_RESEARCH_2026.md §E)
    def set_ground_station_mode(self, on: bool = True) -> None:
        self._cmd(0x03, 0x80, bytes([1 if on else 2]), receiver=DEV_FC)

    def enable_virtual_stick(self, on: bool = True) -> None:
        """MSDK setVirtualStickModeEnabled analogue: request control + ground-station on
        (MSDK_FLIGHT_UNLOCK.md). Must run BEFORE streaming sticks; then arm/takeoff, then
        stream at ~25 Hz. Sticks are IGNORED without this precondition."""
        if on:
            # (api_entry_cfg gate confirmed ABSENT on WM160 firmware — 1-byte NAK — removed.)
            self.request_control()
            self.set_ground_station_mode(True)
        else:
            self.set_ground_station_mode(False)
            self.release_control()

    # Hand control from the RC to the PC (if the FC ignores the sticks after 49/80).
    def rc_to_pc_control(self) -> None:
        self._cmd(0x06, 0xF1, b"\x01")     # RC->PC control handover
    def preempt_control(self) -> None:
        self._cmd(0x19, 0x41, b"\x01")     # preempt right-of-control

    # Takeoff/land/RTH/motors/home/calibration are ALL the one FC function-control command
    # cmd_set=0x03, cmd_id=0x2A, receiver=FC, payload = 1-byte FLYC_COMMAND. Enum values
    # fully resolved from DataFlycFunctionControl$FLYC_COMMAND (reverse_docs/FLIGHT_GATING.md).
    def _fc_function(self, sub: int) -> None:
        self._cmd(0x03, 0x2A, bytes([sub]), receiver=DEV_FC)

    def takeoff(self) -> None:        self._fc_function(0x01)   # AUTO_FLY: starts motors + lifts off
    def cancel_takeoff(self) -> None: self._fc_function(0x0D)
    def land(self) -> None:           self._fc_function(0x02)   # AUTO_LANDING
    def cancel_land(self) -> None:    self._fc_function(0x0E)
    def force_land(self) -> None:     self._fc_function(0x1E)   # ForceLanding (was mislabelled confirm_land)
    def return_to_home(self) -> None: self._fc_function(0x06)   # GOHOME
    def cancel_rth(self) -> None:     self._fc_function(0x0C)
    def start_motors(self) -> None:   self._fc_function(0x07)   # START_MOTOR (arm, no lift)
    def stop_motors(self) -> None:    self._fc_function(0x08)   # STOP_MOTOR (disarm)
    def start_calibration(self) -> None: self._fc_function(0x09)  # compass/IMU FC cali routine
    def set_home_to_aircraft(self) -> None: self._fc_function(0x03)  # HOMEPOINT_NOW

    def motor_force_disable(self, disable: bool = True) -> None:
        # cmd_set 0x03 id 0xFE, 1-byte flag (verified from DataFlycSetMotorForceDisable).
        self._cmd(0x03, 0xFE, bytes([1 if disable else 0]), receiver=DEV_FC)

    # --- flight limits: max altitude / distance ---
    # PRIMARY path = FC param write by name-hash (0x03/0xF9), which is exactly what the DJI
    # app does (DEX-confirmed) and is EEPROM-persisted (attribute 0x0B). Read back with
    # read_param() to verify. FC clamps to its table range. (FLIGHT_LIMITS_RESEARCH_2026.md)
    def set_max_altitude(self, metres: int) -> None:
        m = max(15, min(500, int(metres)))            # FC clamps to 15..500
        self.set_param("g_config.flying_limit.max_height_0", struct.pack("<H", m))

    def set_max_distance(self, metres: int) -> None:
        m = max(15, min(5000, int(metres)))           # FC clamps to 15..5000
        self.set_param("g_config.flying_limit.max_radius_0", struct.pack("<H", m))

    # Fallback: dedicated DataFlycSetLimits command 0x03/0x2D [mode u8][value u16 LE].
    # mode High=1/Far=2/Low=3. Byte-correct but the shipped app never calls it — unproven
    # on WM160, so keep it as a fallback only.
    def set_max_altitude_cmd(self, metres: int) -> None:
        m = max(15, min(500, int(metres)))
        self._cmd(0x03, 0x2D, bytes([1]) + struct.pack("<H", m), receiver=DEV_FC)

    def set_max_distance_cmd(self, metres: int) -> None:
        m = max(15, min(5000, int(metres)))
        self._cmd(0x03, 0x2D, bytes([2]) + struct.pack("<H", m), receiver=DEV_FC)

    def assistant_unlock(self) -> None:
        # 0x03/0xDF assistant/config write-unlock (lock_state u32=1). Some FCs want it once
        # before the first param write; harmless if not required. Not sent automatically.
        self._cmd(0x03, 0xDF, struct.pack("<I", 1), receiver=DEV_FC)

    def get_limits(self, mode: int = 1) -> None:
        self._cmd(0x03, 0x2E, bytes([mode]), receiver=DEV_FC)

    def set_param(self, name: str, value_bytes: bytes) -> None:
        """Write an FC param by name via 0x03/0xF9: [hash u32 LE][value bytes].
        Hash from param_hash (reversed from libGroudStation). Value encoding depends on the
        param type; the caller packs it. Algorithm verified; name->wire mapping still wants
        one live-frame confirmation."""
        from param_hash import param_hash
        import struct as _s
        self._cmd(0x03, 0xF9, _s.pack("<I", param_hash(name)) + value_bytes, receiver=DEV_FC)

    def set_horizontal_speed(self, mps: float) -> None:
        # WM160 has NO m/s speed-cap param (the old horiz_vel_atti_range_0 is Phantom-3-era
        # and ABSENT on Mini -> was a silent no-op). Speed is bounded by the max lean angle,
        # so map speed->angle (~2.5 deg per m/s: 8 m/s ~= 20 deg) and write the tilt param,
        # clamped 5..40 deg. (FLIGHT_MODE_SPEED_RESEARCH_2026.md)
        tilt = max(5.0, min(40.0, float(mps) * 2.5))
        self.set_param("g_config.mode_normal_cfg.tilt_atti_range_0", struct.pack("<f", tilt))

    def unlock_no_gps(self, unlock: bool = True) -> None:
        # Unlock dark/no-GPS takeoff. Verified from the app: it clears the flag by writing
        # the FC param `fc_dark_need_gps_0 = 0` (DarkNoGpsLockEnable is the KeyValue key
        # name, not the FC param; and 0 = unlocked, 1 = locked). Takes off in ATTI (drifts).
        self.set_param("fc_dark_need_gps_0", bytes([0 if unlock else 1]))

    # --- camera working mode: liveview (flight) vs playback (media) ---
    # The camera can't do both; media list/download only answer in playback mode.
    def enter_playback(self) -> None:
        # WM160 is a LEGACY camera: media is served in PLAYBACK = wire mode 2 (via
        # PlaybackManager). Mode 3 = TRANSCODE — sending 3 was the whole cause of the 0xe0
        # NAK on the file family. Send 2, then wait for the 0x02/0x82 PlayBackParams push.
        # (CAMERA_MEDIA_RESEARCH_2026.md)
        self._cmd(0x02, 0x10, bytes([2]), receiver=DEV_CAMERA)   # working_mode = PLAYBACK

    def exit_playback(self) -> None:
        self._cmd(0x02, 0x10, bytes([1]), receiver=DEV_CAMERA)   # back to RECORD/liveview

    def get_param_info(self, index: int) -> None:
        # DataFlycGetParamInfo 0x03/0xF0: request [index u16 LE]; response carries the
        # param NAME (so we can learn the real names straight from the FC and hash them).
        import struct as _s
        self._cmd(0x03, 0xF0, _s.pack("<H", index), receiver=DEV_FC)

    def read_param(self, name: str) -> None:
        # DataFlycReadParamByHash 0x03/0xF8: request [hash u32 LE]; response = the value.
        from param_hash import param_hash
        import struct as _s
        self._cmd(0x03, 0xF8, _s.pack("<I", param_hash(name)), receiver=DEV_FC)

    # Cine/Normal/Sport on WM160 are NOT a DUML mode command — the FC picks a pre-stored
    # block via the RC GEAR channel, which the float joystick 0x03/0x8E has no slot for. So
    # we EMULATE the gears by writing the active (Normal) block's max lean angle = the speed
    # cap. tilt higher -> faster. Param persists (RW+EE); write 20 to restore stock Normal.
    FLIGHT_MODE_TILT = {"cine": 10.0, "cinema": 10.0, "cinematic": 10.0,
                        "normal": 20.0, "sport": 30.0, "max": 40.0}

    def set_flight_mode(self, name: str) -> None:
        tilt = self.FLIGHT_MODE_TILT.get(str(name).strip().lower())
        if tilt is None:
            raise ValueError(f"unknown mode {name!r}; use cine/normal/sport/max")
        self.set_param("g_config.mode_normal_cfg.tilt_atti_range_0", struct.pack("<f", tilt))

    # --- home point (DataFlycSetHomePoint 0x03/0x31, 18-byte payload) ---
    # doPack confirmed byte-for-byte from DJI bytecode (HOME_POINT_RESEARCH_2026_v2.md §3):
    # [0] homeType: APP=0x02 (explicit coord), AIRCRAFT=0x00 (current location), RC=0x01;
    # [1..8] LAT f64 LE RADIANS, [9..16] LON f64 LE RADIANS, [17] interval.
    # NOTE: SET order is lat-first, the OPPOSITE of the READ push (lon-first). The app sends
    # interval=0 for a one-shot set (mInterval only matters for dynamic/FOLLOW home), so we
    # send 0 to mirror it. Preconditions: AIRCRAFT needs a GPS fix; explicit APP coord must be
    # a valid lat/lon (a distance-vs-current-home limit is probable, verify on hardware).
    def set_home_point(self, lat_deg: float, lon_deg: float) -> None:
        """Set home to an EXPLICIT GPS coordinate (type APP=0x02)."""
        import math
        self._cmd(0x03, 0x31,
                  bytes([0x02]) + struct.pack("<dd", math.radians(lat_deg), math.radians(lon_deg))
                  + bytes([0x00]),
                  receiver=DEV_FC)

    def set_home_to_current_location(self) -> None:
        """Set home to the aircraft's CURRENT location (type AIRCRAFT=0x00). Needs GPS fix."""
        self._cmd(0x03, 0x31,
                  bytes([0x00]) + struct.pack("<dd", 0.0, 0.0) + bytes([0x00]),
                  receiver=DEV_FC)

    def set_rth_altitude(self, metres: int) -> None:
        # RTH (go-home) height is a param write, not a command. 20..500 m.
        m = max(20, min(500, int(metres)))
        self.set_param("g_config.go_home.fixed_go_home_altitude_0", struct.pack("<H", m))

    # --- gimbal auto-calibration 0x04/0x08 ---
    def gimbal_calibrate(self) -> None:
        self._cmd(0x04, 0x08, b"", receiver=DEV_GIMBAL)

    # --- fallback stick frame: mobile-RC 0x01/0x02 (VIRTUAL_STICK_NATIVE.md §5) ---
    # Used when the FC reports IsSupportVirtualJoyStick=false (legacy WM160). Tight 11-bit
    # packing at offsets 0/11/22/33 (NOT the 0x0A layout). cfg map: chA=thr chB=roll chC=yaw chD=pitch.
    def set_sticks_mobilerc(self, roll: float, pitch: float, yaw: float,
                            throttle: float, mode: int = 0) -> None:
        def ch(v):
            r = 1024 + int(round(max(-1.0, min(1.0, v)) * 660))
            return max(364, min(1684, r)) & 0x7FF
        packed = ch(throttle) | (ch(roll) << 11) | (ch(yaw) << 22) | (ch(pitch) << 33)
        flags = 0x0200 | ((mode & 3) << 10)
        payload = bytes([0x00]) + packed.to_bytes(8, "little") + b"\x00\x00" + struct.pack("<H", flags)
        self._cmd(0x01, 0x02, payload, receiver=DEV_FC, ack=False)

    # --- FLYC float joystick 0x03/0x8E = DataFlycJoystick (MSDK v4.18, the SDK that
    # officially added Mavic Mini support). CONFIRMED byte-for-byte from provided.jar:
    #   payload = 17 bytes: [0] flag u8, [1..4] roll f32 LE, [5..8] pitch f32 LE,
    #                       [9..12] yaw f32 LE, [13..16] throttle f32 LE.
    #   cmd_set=CmdSet.FLYC(0x03), cmd_id=CmdIdFlyc.JoyStick(79)=0x8E, sender APP->FLYC,
    #   REQUEST, NEEDACK.NO. Values are PHYSICAL units, NOT normalized [-1..1] and NOT
    #   RC channels. Meaning depends on the flag byte (see build_stick_flag).
    #
    # flag byte (from FlightControllerAbstraction.fdd(Vert,RP,Yaw,Coord,bool)):
    #   flag = (rollpitch<<6) | (vertical<<4) | (yaw<<3) | (coord<<1) | advanced
    #   bit6 rollpitch: 0=ANGLE(deg), 1=VELOCITY(m/s)
    #   bit4 vertical : 0=VELOCITY(m/s), 1=POSITION(m)
    #   bit3 yaw      : 0=ANGLE(deg abs heading), 1=ANGULAR_VELOCITY(deg/s)
    #   bit1 coord    : 0=GROUND, 1=BODY
    #   bit0 advanced : setVirtualStickAdvancedModeEnabled
    # Value ranges (Limits.class): vert vel [-4..5] m/s, rollpitch vel ±15 m/s,
    #   rollpitch angle ±30 deg, yaw angle ±180 deg, yaw ang.vel ~±100 deg/s.
    @staticmethod
    def build_stick_flag(rollpitch_velocity=True, yaw_rate=True,
                         vertical_velocity=True, body_frame=False, advanced=False) -> int:
        rp = 1 if rollpitch_velocity else 0
        vt = 0 if vertical_velocity else 1          # VELOCITY=0, POSITION=1
        yw = 1 if yaw_rate else 0                    # ANGLE=0, ANGULAR_VELOCITY=1
        co = 1 if body_frame else 0                  # GROUND=0, BODY=1
        return ((rp << 6) | (vt << 4) | (yw << 3) | (co << 1) | (1 if advanced else 0)) & 0xFF

    # default flag 0x48 = rollpitch VELOCITY + yaw ANGULAR_VELOCITY + vertical VELOCITY
    #                     + GROUND frame + advanced off (the "spectator-mode" velocity setup).
    def set_sticks_float(self, roll: float, pitch: float, yaw: float, throttle: float,
                         flag: int = 0x48) -> None:
        # WM160 wire order (EMPIRICAL on hardware) = pitch, roll, throttle, yaw.
        # The MSDK swaps yaw<->throttle for this DroneType, and on WM160 the first two
        # floats behave as pitch (fwd/back) then roll (lateral) — i.e. roll<->pitch are
        # also swapped vs the generic doPack. Slots: [1..4]=pitch [5..8]=roll [9..12]=throttle
        # [13..16]=yaw. (VIRTUAL_STICK_RESEARCH_2026.md; confirmed by W/S vs A/D behaviour.)
        self._cmd(0x03, 0x8E,
                  bytes([flag]) + struct.pack("<ffff", pitch, roll, throttle, yaw),
                  receiver=DEV_FC)

    # Scale normalized axes [-1..1] to physical units and send via 0x03/0x8E.
    def set_sticks_velocity(self, roll: float, pitch: float, yaw: float, throttle: float,
                            flag: int = 0x4A, h_mps: float = 5.0, v_mps: float = 2.0,
                            yaw_dps: float = 90.0) -> None:
        # Default flag 0x4A = velocity + BODY frame (roll/pitch relative to the aircraft's
        # heading — what a pilot expects). 0x48 = GROUND frame (world-relative; feels
        # diagonal when the nose isn't aligned). Coord bit only rescopes roll/pitch.
        c = lambda v: max(-1.0, min(1.0, v))
        self.set_sticks_float(c(roll) * h_mps, c(pitch) * h_mps,
                              c(yaw) * yaw_dps, c(throttle) * v_mps, flag=flag)

    # ==========================================================
    # APP FUNCTIONS (camera/gimbal/media)
    # ==========================================================
    # --- gimbal (camera) — CONFIRMED by reversing (cmd_set 0x04, receiver gimbal) ---
    @staticmethod
    def _gimbal_angle_payload(pitch, yaw=0.0, roll=0.0, duration_s=1.0) -> bytes:
        # int16 yaw*10, roll*10, pitch*10 (LE) + ctrl(0x01=mode on) + duration*10 (100ms)
        body = struct.pack("<hhh", int(round(yaw * 10)), int(round(roll * 10)),
                           int(round(pitch * 10)))
        dur = max(0, min(255, int(round(duration_s * 10))))
        return body + bytes([0x01, dur])

    @staticmethod
    def _gimbal_speed_payload(pitch_dps, yaw_dps=0.0, roll_dps=0.0) -> bytes:
        body = struct.pack("<hhh", int(round(yaw_dps * 10)), int(round(roll_dps * 10)),
                           int(round(pitch_dps * 10)))
        return body + bytes([0x81, 0x00])

    def gimbal_angle(self, pitch_deg, yaw_deg=0.0, roll_deg=0.0, duration_s=1.0) -> None:
        """Absolute gimbal angle (cmd_set 0x04, cmd_id 0x14)."""
        self._cmd(0x04, 0x14, self._gimbal_angle_payload(pitch_deg, yaw_deg, roll_deg, duration_s),
                  receiver=DEV_GIMBAL)

    def gimbal_speed(self, pitch_dps, yaw_dps=0.0, roll_dps=0.0) -> None:
        """Gimbal speed, °/s (cmd_set 0x04, cmd_id 0x0C). Send at ~10 Hz, stop = 0."""
        self._cmd(0x04, 0x0C, self._gimbal_speed_payload(pitch_dps, yaw_dps, roll_dps),
                  receiver=DEV_GIMBAL)
    # --- camera — CONFIRMED by reversing (cmd_set 0x02, receiver camera) ---
    # DataCameraSetPhoto$TYPE (confirmed from smali): SINGLE=1, HDR=2, BURST=4, AEB=5...
    PHOTO_SINGLE, PHOTO_HDR, PHOTO_BURST, PHOTO_AEB = 1, 2, 4, 5

    def take_photo(self, ptype: int = PHOTO_SINGLE) -> None:
        # Camera must be in PHOTO/capture work mode (0x00) to accept a photo command.
        # If we were recording, the mode is still VIDEO(1) and the photo is silently dropped.
        # Same async fix as start_record: set mode first, wait ~300 ms, then send the command.
        import time
        self._cmd(CMDSET_CAMERA, 0x10, b"\x00", receiver=DEV_CAMERA)   # work mode = PHOTO

        def _seq():
            time.sleep(0.3)
            self._cmd(CMDSET_CAMERA, 0x01, bytes([ptype & 0xFF]), receiver=DEV_CAMERA)
        threading.Thread(target=_seq, daemon=True).start()

    def start_record(self) -> None:
        # ROOT CAUSE of "record won't start": the camera must be in RECORD/video work mode,
        # and the mode switch is ASYNC. Firing set-mode (0x10,1) then START (0x02,1) back-to-
        # back races the transition, so the camera drops the START (the app instead waits for
        # the pushed mode to become RECORD). Fix: set mode, give the switch time to land, then
        # send START — and re-send it a couple times (mirrors DataCameraSetRecord's repeat
        # Timer) so a single dropped frame doesn't leave us un-recording. Run on a daemon
        # thread so the UI/telemetry loop doesn't stall. (RECORD_PHOTO_RESEARCH_2026.md §5-6)
        import time
        self._cmd(CMDSET_CAMERA, 0x10, b"\x01", receiver=DEV_CAMERA)   # work mode = RECORD/video

        def _seq():
            for i in range(3):
                time.sleep(0.4 if i == 0 else 0.6)   # let the mode switch settle, then retry
                self._cmd(CMDSET_CAMERA, 0x02, b"\x01", receiver=DEV_CAMERA)   # 1 = START
        threading.Thread(target=_seq, daemon=True).start()

    def stop_record(self) -> None:
        self._cmd(CMDSET_CAMERA, 0x02, b"\x00", receiver=DEV_CAMERA)   # 0 = STOP
    def set_camera_mode(self, mode: int) -> None:
        # 0x02/0x10 set work mode (0=photo,1=video... SDK enum identity)
        self._cmd(CMDSET_CAMERA, 0x10, bytes([mode & 0xFF]), receiver=DEV_CAMERA)
    # --- liveview (video) — start commands from reversing ---
    def request_i_frame(self) -> None:
        """Ask the camera for an immediate keyframe (uav_camera_get_app_request_i_frame).

        HEVC needs an IRAP to start decoding, and the drone only emits one every ~46
        frames, so a client joining mid-stream stays blank until this is sent.
        """
        self._cmd(0x02, 0xB3, b"", receiver=DEV_CAMERA)

    def start_liveview(self, camera_source: int = 0) -> None:
        """Start the video stream: select the camera + tell it the decoder/fps/bandwidth.
        Receivers/payloads are partly a hypothesis — to be refined on a live Pi."""
        # 1) select the camera source (0x02/0x09)
        self._cmd(0x02, 0x09, bytes([camera_source & 0xFF]), receiver=DEV_CAMERA)
        # 2) decoder capabilities (0x08/0x41): [count=5] + 5×[codec, cap_u32 LE]
        cap = bytes([5])
        for codec in range(5):
            capval = 0xFFFFFFFF if codec == 0 else 0     # codec 0 = H.264 — everything
            cap += bytes([codec]) + struct.pack("<I", capval)
        self._cmd(0x08, 0x41, cap, receiver=DEV_DM368)
        # 3) max framerate (0x08/0x42): u16 fps + 2 zeros
        self._cmd(0x08, 0x42, struct.pack("<H", 30) + b"\x00\x00", receiver=DEV_DM368)
        # 4) bandwidth priority (0x08/0x69): [stream_idx, percent, 0]
        self._cmd(0x08, 0x69, bytes([0, 100, 0]), receiver=DEV_DM368)
        # 5) ask for a keyframe, or we join mid-GOP with nothing to decode against
        self.request_i_frame()

    def set_zoom(self, factor: float) -> None:
        # 0x02/0x34 digital zoom: [09,00,00, zoom_u16 LE], zoom = factor*100
        z = max(0, min(0xFFFF, int(round(factor * 100))))
        self._cmd(CMDSET_CAMERA, 0x34,
                  bytes([0x09, 0x00, 0x00, z & 0xFF, (z >> 8) & 0xFF]), receiver=DEV_CAMERA)

    # Camera settings (cmd_ids are exact; enum values are standard DJI, verify on HW)
    # Exposure mode (0x02/0x1E): PROGRAM=1, SHUTTER=2, APERTURE=3, MANUAL=4.
    def set_exposure_mode(self, mode: int) -> None:
        # DataCameraSetExposureMode = 2 bytes [expMode, senceMode]. Sending only 1 byte gets
        # the frame dropped, so the camera stays AUTO and ignores ISO. mode: Manual(M)=4.
        # (CAMERA_MEDIA_RESEARCH_2026.md)
        self._cmd(CMDSET_CAMERA, 0x1E, bytes([mode & 0xFF, 0x00]), receiver=DEV_CAMERA)

    # 0=AUTO. WM160 sensor tops out at 3200 for video (camera may clamp higher values).
    _ISO_INDEX = {0: 0, 100: 3, 200: 4, 400: 5, 800: 6, 1600: 7, 3200: 8,
                  6400: 9, 12800: 10, 25600: 11}

    def set_iso_auto(self) -> None:
        """Auto ISO: hand exposure back to the camera (PROGRAM), which picks ISO+shutter
        itself. This is the default from startup — the drone auto-exposes until the user
        picks a manual ISO/shutter."""
        self.set_exposure_mode(1)          # PROGRAM (auto)
        self._shutter_denom = None
        self._cmd(CMDSET_CAMERA, 0x2A, bytes([0]), receiver=DEV_CAMERA)   # ISO enum 0 = AUTO

    def set_iso(self, iso: int) -> None:
        # ISO takes the enum INDEX and only applies in MANUAL exposure. BUT the Mini has a
        # FIXED aperture, so in MANUAL brightness = ISO × shutter only. Switching to MANUAL
        # FREEZES the shutter at whatever auto-exposure last picked (outdoors that's very fast,
        # 1/500–1/2000), and ISO alone can't beat a fast frozen shutter — that's the "ISO
        # doesn't brighten, still dark" symptom. So when the user hasn't pinned a shutter,
        # drop to a sane 1/60 here so the ISO change is actually visible. (Video ISO is also
        # hard-capped at 3200 on this sensor — 6400+ get clamped.) For a plain "make it
        # brighter", prefer set_ev() (AUTO + exposure compensation) instead.
        self.set_exposure_mode(4)
        idx = self._ISO_INDEX.get(iso, iso)
        self._cmd(CMDSET_CAMERA, 0x2A, bytes([idx & 0x7F]), receiver=DEV_CAMERA)
        if self._shutter_denom is None:
            # 1/30 is the SLOWEST (brightest) shutter usable for 30fps video, so it's the
            # brightest sane default for a dark scene. In PHOTO the user can go slower still
            # (1/8, 1/4…) via the shutter control for much more light. User override sticks.
            self.set_shutter(30)

    # --- shutter speed = DataCameraSetShutterSpeed (0x02/0x28) — the real brightness lever
    # in MANUAL. Payload 4 B: [type][integral u16 LE][decimal]. For a 1/N shutter,
    # integral = (1<<15) | N (bit15 = "reciprocal"). SLOWER shutter (smaller N) = BRIGHTER.
    # WM160 sensor caps ISO at 3200, so when it's too dark at ISO 3200, slow the shutter.
    def set_shutter(self, denom: int) -> None:
        """Set shutter to 1/denom s (e.g. 30 -> 1/30). Smaller denom = brighter."""
        self.set_exposure_mode(4)                      # shutter only sticks in MANUAL
        self._shutter_denom = int(denom)               # remember so set_iso() won't override it
        integral = (1 << 15) | (int(denom) & 0x7FFF)
        self._cmd(CMDSET_CAMERA, 0x28,
                  bytes([1]) + struct.pack("<H", integral) + bytes([0]), receiver=DEV_CAMERA)

    def set_shutter_auto(self) -> None:
        """Let the camera auto-pick the shutter (type=AUTO)."""
        self._shutter_denom = None
        self._cmd(CMDSET_CAMERA, 0x28, bytes([0, 0, 0, 0]), receiver=DEV_CAMERA)

    def set_ev(self, ev_thirds: int) -> None:
        # Exposure compensation — the natural "make it brighter/darker" lever. EV only works
        # in an AUTO/PROGRAM exposure mode (in MANUAL the camera ignores it), so force PROGRAM
        # first; otherwise a prior set_iso() left us in MANUAL and the EV slider did nothing.
        # 0EV=0x10, each full stop = 3 (1/3-EV) steps.
        self.set_exposure_mode(1)                      # PROGRAM (auto) — EV applies here
        self._shutter_denom = None                     # back under auto-exposure control
        val = 0x10 + int(ev_thirds) * 3
        self._cmd(CMDSET_CAMERA, 0x2E, bytes([max(0, min(0xFF, val))]), receiver=DEV_CAMERA)
    def set_white_balance(self, mode: int, ct_index: int = 0) -> None:
        self._cmd(CMDSET_CAMERA, 0x2C, bytes([mode & 0xFF, ct_index & 0xFF, 0, 0, 0]),
                  receiver=DEV_CAMERA)
    def set_video_format(self, resolution: int, framerate: int, fov: int = 0) -> None:
        self._cmd(CMDSET_CAMERA, 0x18,
                  bytes([resolution & 0xFF, framerate & 0xFF, fov & 0xFF, 0, 0]), receiver=DEV_CAMERA)
    def set_photo_mode(self, code: int) -> None:
        self._cmd(CMDSET_CAMERA, 0x6A, bytes([code & 0xFF]) + b"\x00" * 5, receiver=DEV_CAMERA)
    def set_video_codec(self, h265: bool = False) -> None:
        self._cmd(CMDSET_CAMERA, 0xAB, bytes([1 if h265 else 0, 0]), receiver=DEV_CAMERA)
    def gimbal_recenter(self) -> None:
        # DataGimbalNewResetAndSetMode 0x04/0x4C: [workMode 0xFE=keep, resetCmd 0x01=recenter].
        self._cmd(0x04, 0x4C, bytes([0xFE, 0x01]), receiver=DEV_GIMBAL)

    # UNIVERSAL: send any DUML command (the whole surface from reverse_docs).
    def send_raw(self, cmd_set: int, cmd_id: int, payload: bytes = b"",
                 receiver: int = DEV_FC, ack: bool = True) -> None:
        self._cmd(cmd_set, cmd_id, payload, receiver=receiver, ack=ack)

    # Registry of "all functions" — an extensible name->method map so that command
    # sources (UI/neural net/script) can call functions by name.
    def functions(self) -> dict:
        return {
            "takeoff": self.takeoff, "land": self.land,
            "cancel_takeoff": self.cancel_takeoff, "cancel_land": self.cancel_land,
            "rth": self.return_to_home, "cancel_rth": self.cancel_rth,
            "request_control": self.request_control, "release_control": self.release_control,
            "rc_to_pc": self.rc_to_pc_control, "preempt": self.preempt_control,
            "photo": self.take_photo, "rec_start": self.start_record,
            "rec_stop": self.stop_record,
            "gimbal_up": lambda: self.gimbal_speed(30),
            "gimbal_down": lambda: self.gimbal_speed(-30),
        }
