"""
PC keyboard -> virtual sticks -> DUML flight command.

Controls like Minecraft's spectator mode:
    W / S   — pitch forward/backward (fly forward/backward)
    A / D   — roll left/right        (fly left/right)
    Space   — throttle up            (climb)
    Shift   — throttle down          (descend)
    Q / E   — yaw left/right         (turn)
    (left/right arrows — also yaw)

Each keyboard poll frame is turned into 4 normalized axes [-1..1]:
    roll, pitch, yaw, throttle
then scaled to int16 and packed into a flight-control DUML frame.

!!! IMPORTANT about cmd_set/cmd_id/payload layout:
    The exact "virtual stick" packet format depends on the model (WM160, etc.) and has
    NOT yet been extracted from libsdk_jni.so / a dump of a real RC. The values below are
    a STUB with the correct STRUCTURE (4x int16 LE + flags). They need to be refined by
    capturing real traffic or by further reversing the native lib. Everything is moved into
    FlightProfile so it can be changed in one place.
"""

from __future__ import annotations
from dataclasses import dataclass
import struct

from duml import DumlPacket

# DUML addresses
DEV_APP = 0x0a      # us (PC / app)
DEV_FC = 0x03       # flight controller


@dataclass
class FlightProfile:
    """WM160 virtual-stick command — CONFIRMED by reversing libsdk_jni.so
    (VirtualJoyStickHelper::AssemblePack): special_tlv, cmd_set=0x01 cmd_id=0x0A,
    PUSH to FC(0x03). 4 channels of 11 bits each, value=round(norm*660+1024) -> [364..1684].

    Channel order ch0..ch3 = (roll,pitch,yaw,throttle) — HYPOTHESIS (verify on the
    bench); the bit packing and scale are exact."""
    cmd_set: int = 0x01
    cmd_id: int = 0x0A
    center: int = 1024
    axis_range: int = 660
    order: tuple = ("roll", "pitch", "yaw", "throttle")   # ch0..ch3
    flags_word: int = 0x00000200


@dataclass
class Sticks:
    roll: float = 0.0      # -1 (left)     .. +1 (right)
    pitch: float = 0.0     # -1 (backward) .. +1 (forward)
    yaw: float = 0.0       # -1 (left)     .. +1 (right)
    throttle: float = 0.0  # -1 (down)     .. +1 (up)

    def clamp(self) -> "Sticks":
        c = lambda v: max(-1.0, min(1.0, v))
        return Sticks(c(self.roll), c(self.pitch), c(self.yaw), c(self.throttle))


# normalized key name -> contribution to axes
def keys_to_sticks(pressed: set[str]) -> Sticks:
    s = Sticks()
    if "w" in pressed: s.pitch += 1.0
    if "s" in pressed: s.pitch -= 1.0
    if "d" in pressed: s.roll += 1.0
    if "a" in pressed: s.roll -= 1.0
    if "space" in pressed:  s.throttle += 1.0
    if "shift" in pressed:  s.throttle -= 1.0
    if "e" in pressed or "right" in pressed: s.yaw += 1.0
    if "q" in pressed or "left" in pressed:  s.yaw -= 1.0
    return s.clamp()


def _chan(v: float, prof: FlightProfile) -> int:
    """[-1..1] -> 11-bit DJI channel (center 1024, ±660, clamp 364..1684)."""
    raw = prof.center + int(round(v * prof.axis_range))
    raw = max(prof.center - prof.axis_range, min(prof.center + prof.axis_range, raw))
    return raw & 0x7FF


def sticks_to_payload(s: Sticks, prof: FlightProfile) -> bytes:
    """Real payload of the special_tlv command (cmd_set 0x01, cmd_id 0x0A) for WM160.
    TLV #1 (0x01, len 13): 8 bytes of packed channels + uint32 flags + byte 0x06.
    TLV #2 (0x55, len 1): 0x04.  (TLV #3 time 0x56 is optional — not sent yet.)"""
    ch = [_chan(getattr(s, name), prof) for name in prof.order]   # ch0..ch3
    packed = (ch[0] << 8) | (ch[1] << 19) | (ch[2] << 30) | (ch[3] << 41) | (1 << 62)
    tlv1_val = packed.to_bytes(8, "little") + \
        prof.flags_word.to_bytes(4, "little") + bytes([0x06])
    tlv1 = bytes([0x01, len(tlv1_val)]) + tlv1_val
    tlv2 = bytes([0x55, 0x01, 0x04])
    return tlv1 + tlv2


def build_flight_frame(s: Sticks, seq: int, prof: FlightProfile | None = None) -> bytes:
    prof = prof or FlightProfile()
    pkt = DumlPacket(
        sender=DEV_APP, receiver=DEV_FC,
        cmd_set=prof.cmd_set, cmd_id=prof.cmd_id,
        seq=seq, cmd_type=0x00,               # PUSH (no ACK), as in the original
        payload=sticks_to_payload(s, prof),
    )
    return pkt.encode()


# --------------------------------------------------------------------------
# Game input loop. pygame gives real keydown/keyup and holding several
# keys at once (a terminal can't do that).  transport.send(frame) — where to send the frame.
# --------------------------------------------------------------------------
def run_keyboard(drone, prof: FlightProfile | None = None, hz: int = 20):
    """drone — a Drone object (drone.py). WASD/Space/Shift -> drone.set_sticks();
    hotkeys -> RC/app functions."""
    try:
        import pygame
    except Exception:
        raise RuntimeError(
            "pygame is not installed (needed for game input with key-hold):\n"
            "  pip install pygame"
        )
    prof = prof or FlightProfile()

    KEYMAP = {
        pygame.K_w: "w", pygame.K_a: "a", pygame.K_s: "s", pygame.K_d: "d",
        pygame.K_q: "q", pygame.K_e: "e",
        pygame.K_SPACE: "space",
        pygame.K_LSHIFT: "shift", pygame.K_RSHIFT: "shift",
        pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
    }
    # one-shot hotkeys -> RC/app functions (drone.functions())
    HOTKEYS = {
        pygame.K_t: "takeoff", pygame.K_l: "land", pygame.K_h: "rth",
        pygame.K_x: "estop", pygame.K_p: "photo",
        pygame.K_r: "rec_start", pygame.K_f: "rec_stop",
    }
    funcs = drone.functions()

    pygame.init()
    screen = pygame.display.set_mode((460, 190))
    pygame.display.set_caption("DJI PC control (beta) - WASD / Space / Shift / Q,E")
    font = pygame.font.SysFont("monospace", 15)
    clock = pygame.time.Clock()

    last_action = ""
    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in HOTKEYS and HOTKEYS[ev.key] in funcs:
                    name = HOTKEYS[ev.key]
                    funcs[name]()
                    last_action = f"func: {name}()"

        held = pygame.key.get_pressed()
        pressed = {name for key, name in KEYMAP.items() if held[key]}
        s = keys_to_sticks(pressed)
        drone.set_sticks(s.roll, s.pitch, s.yaw, s.throttle)

        screen.fill((18, 18, 22))
        lines = [
            f"roll={s.roll:+.2f} pitch={s.pitch:+.2f} yaw={s.yaw:+.2f} thr={s.throttle:+.2f}",
            "W/S pitch  A/D roll  Space up  Shift down  Q/E yaw",
            "T takeoff  L land  H RTH  P photo  R/F record  X stop",
            f"battery={drone.state.battery_pct}  altitude={drone.state.altitude_m}",
            last_action,
        ]
        for i, ln in enumerate(lines):
            screen.blit(font.render(ln, True, (200, 220, 200)), (10, 12 + i * 30))
        pygame.display.flip()
        clock.tick(hz)

    pygame.quit()
