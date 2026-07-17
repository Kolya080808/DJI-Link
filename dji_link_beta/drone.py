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

    # Ground station mode — PROBABLY required BEFORE the sticks so the FC listens.
    # uav_fc_set_ground_station_on_off_req: cmd_set 0x03, cmd_id 0x80, 1 byte.
    def set_ground_station_mode(self, on: bool = True) -> None:
        self._cmd(0x03, 0x80, bytes([1 if on else 0]), receiver=DEV_FC)

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

    # --- flight limits: max altitude / distance WITHOUT the param hash (0x03/0x2D) ---
    # DataFlycSetLimits, payload [mode u8][value u16 LE metres]. mode High=1/Far=2/Low=3.
    def set_max_altitude(self, metres: int) -> None:
        m = max(15, min(500, int(metres)))            # FC clamps to 15..500
        self._cmd(0x03, 0x2D, bytes([1]) + struct.pack("<H", m), receiver=DEV_FC)

    def set_max_distance(self, metres: int) -> None:
        m = max(15, min(5000, int(metres)))           # FC clamps to 15..5000
        self._cmd(0x03, 0x2D, bytes([2]) + struct.pack("<H", m), receiver=DEV_FC)

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
        # Horizontal velocity limit is an FC param (float m/s). Name/type best-effort.
        import struct as _s
        self.set_param("g_config.control.horiz_vel_atti_range_0", _s.pack("<f", float(mps)))

    def unlock_no_gps(self, unlock: bool = True) -> None:
        # Unlock dark/no-GPS takeoff. Verified from the app: it clears the flag by writing
        # the FC param `fc_dark_need_gps_0 = 0` (DarkNoGpsLockEnable is the KeyValue key
        # name, not the FC param; and 0 = unlocked, 1 = locked). Takes off in ATTI (drifts).
        self.set_param("fc_dark_need_gps_0", bytes([0 if unlock else 1]))

    # --- camera working mode: liveview (flight) vs playback (media) ---
    # The camera can't do both; media list/download only answer in playback mode.
    def enter_playback(self) -> None:
        # File ops (0x00/0x20 list, 0x1F data, 0x28 delete) are serviced ONLY in
        # MEDIA_DOWNLOAD mode (=3), which is DISTINCT from PLAYBACK (=2) in
        # CameraWorkMode.smali. Setting 2 makes the camera NAK the whole file family
        # with 0xe0 ("not available in this state"). Send 3 and nothing after it.
        self._cmd(0x02, 0x10, bytes([3]), receiver=DEV_CAMERA)   # working_mode = MEDIA_DOWNLOAD

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

    def set_flight_mode(self, name: str) -> None:
        # normal/cinema/sport are NOT a single DUML command on WM160 — the app changes a
        # set of control-gain params (hash-written) and the RC mode gear. So mode switching
        # also depends on the param hash, same blocker as speed.
        raise NotImplementedError(
            "flight mode = control-gain params (hash write); not a single command on Mini 1")

    # --- home point (arbitrary lat/lon) 0x03/0x31, coords in RADIANS ---
    def set_home_point(self, lat_deg: float, lon_deg: float, home_type: int = 0) -> None:
        import math
        lat = math.radians(lat_deg)
        lon = math.radians(lon_deg)
        self._cmd(0x03, 0x31,
                  bytes([home_type]) + struct.pack("<dd", lat, lon) + bytes([0]),
                  receiver=DEV_FC)

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

    # --- alternate stick encoding: FLYC float joystick 0x03/0x8E (candidate 3) ---
    # 17 bytes [flag u8][roll,pitch,yaw,throttle f32 LE], physical units (MSDK-like).
    def set_sticks_float(self, roll: float, pitch: float, yaw: float, throttle: float,
                         flag: int = 0) -> None:
        self._cmd(0x03, 0x8E,
                  bytes([flag]) + struct.pack("<ffff", roll, pitch, yaw, throttle),
                  receiver=DEV_FC)

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
    def take_photo(self) -> None:
        # cmd_id 0x01, payload = capture_type (single = protocol 2)
        self._cmd(CMDSET_CAMERA, 0x01, b"\x02", receiver=DEV_CAMERA)
    def start_record(self) -> None:
        self._cmd(CMDSET_CAMERA, 0x02, b"\x01", receiver=DEV_CAMERA)
    def stop_record(self) -> None:
        self._cmd(CMDSET_CAMERA, 0x02, b"\x00", receiver=DEV_CAMERA)
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
        self._cmd(CMDSET_CAMERA, 0x1E, bytes([mode & 0xFF]), receiver=DEV_CAMERA)

    _ISO_INDEX = {0: 0, 100: 3, 200: 4, 400: 5, 800: 6, 1600: 7, 3200: 8}  # 0=AUTO

    def set_iso(self, iso: int) -> None:
        # ISO takes the enum INDEX, and only applies in MANUAL exposure mode.
        self.set_exposure_mode(4)
        idx = self._ISO_INDEX.get(iso, iso)
        self._cmd(CMDSET_CAMERA, 0x2A, bytes([idx & 0x7F]), receiver=DEV_CAMERA)

    def set_ev(self, ev_thirds: int) -> None:
        # Exposure compensation: 0EV=0x10, each full stop = 3 (1/3-EV) steps. Non-manual only.
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
