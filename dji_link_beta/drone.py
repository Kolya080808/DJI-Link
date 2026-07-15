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

DEV_APP = 0x0a     # us (PC, playing the role of app/RC)
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
        self.t.send(pkt.encode())

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
        self._cmd(0x49, 0x80, b"\x01")     # 1 = request control (wait for ACK)

    def release_control(self) -> None:
        self._cmd(0x49, 0x80, b"\x00")     # 0 = release control

    # Ground station mode — PROBABLY required BEFORE the sticks so the FC listens.
    # uav_fc_set_ground_station_on_off_req: cmd_set 0x03, cmd_id 0x80, 1 byte.
    def set_ground_station_mode(self, on: bool = True) -> None:
        self._cmd(0x03, 0x80, bytes([1 if on else 0]), receiver=DEV_FC)

    # Hand control from the RC to the PC (if the FC ignores the sticks after 49/80).
    def rc_to_pc_control(self) -> None:
        self._cmd(0x06, 0xF1, b"\x01")     # RC->PC control handover
    def preempt_control(self) -> None:
        self._cmd(0x19, 0x41, b"\x01")     # preempt right-of-control

    # Takeoff/landing/RTH — CONFIRMED by reversing: a single FC function control command
    # cmd_set=0x03, cmd_id=0x2A, receiver=FC, payload = 1-byte sub-function.
    def _fc_function(self, sub: int) -> None:
        self._cmd(0x03, 0x2A, bytes([sub]), receiver=DEV_FC)

    def takeoff(self) -> None:        self._fc_function(0x01)   # auto-takeoff (starts the motors)
    def cancel_takeoff(self) -> None: self._fc_function(0x0D)
    def land(self) -> None:           self._fc_function(0x02)   # auto-landing
    def cancel_land(self) -> None:    self._fc_function(0x0E)
    def confirm_land(self) -> None:   self._fc_function(0x1E)
    def return_to_home(self) -> None: self._fc_function(0x06)
    def cancel_rth(self) -> None:     self._fc_function(0x0C)

    def motor_force_disable(self, disable: bool = True) -> None:
        # cmd_set 0x03 id 0xFE, 2 bytes (flag, mode). Locks the motors on the ground.
        self._cmd(0x03, 0xFE, bytes([1 if disable else 0, 0]), receiver=DEV_FC)

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

    def set_zoom(self, factor: float) -> None:
        # 0x02/0x34 digital zoom: [09,00,00, zoom_u16 LE], zoom = factor*100
        z = max(0, min(0xFFFF, int(round(factor * 100))))
        self._cmd(CMDSET_CAMERA, 0x34,
                  bytes([0x09, 0x00, 0x00, z & 0xFF, (z >> 8) & 0xFF]), receiver=DEV_CAMERA)

    # Camera settings (cmd_ids are exact; enum values are standard DJI, verify on HW)
    def set_iso(self, code: int) -> None:
        self._cmd(CMDSET_CAMERA, 0x2A, bytes([code & 0xFF]), receiver=DEV_CAMERA)
    def set_ev(self, code: int) -> None:
        self._cmd(CMDSET_CAMERA, 0x2E, bytes([code & 0xFF]), receiver=DEV_CAMERA)
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
        # action reset gimbal (0x04/... work_mode_and_return_center); sent like an FC-function-style command
        self._cmd(0x04, 0x4C, b"\x01", receiver=DEV_GIMBAL)

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
