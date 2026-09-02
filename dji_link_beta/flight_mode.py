"""
Flight-mode model + SoftSwitchMode frame encoder for the DJI Mavic Mini 1 (WM160).

Port of the C++ core (src/core/flight_mode.{hpp,cpp}) into the Python beta so the whole
flight-mode feature can be exercised with `python3 pc_client.py --sim` instead of rebuilding
the C++ client per change.

WHY this module exists at all: on the Mini there is NO writable "current flight mode" FC
parameter. The FC keeps four pre-loaded config blocks (mode_normal_cfg = Position,
mode_sport_cfg = Sport, mode_gentle_cfg = CineSmooth, mode_tripod_cfg = Tripod) and the
ACTIVE one is chosen by the RC GEAR channel. DJI Fly emulates that gear with the KeyValue
key RemoteController/SoftSwitchMode (enum POSITION/SPORT/TRIPOD) routed to the RC component
(cmd_set 0x06) — it is NOT a FLYC 0x03 param write. The old beta code wrote the Normal
block's tilt limit instead, which only ever produced a "sped-up Normal" and never activated
the Sport block (see drone.py's set_flight_mode before this port).

This module is pure: no transport, no I/O, no threads — so it is unit-testable on a bare
laptop (test_flight_mode.py). Frame *sending* lives in drone.py.
"""

from __future__ import annotations
from enum import Enum, IntEnum
import struct

from duml import DumlPacket

# --- user-facing modes -------------------------------------------------------------------


class FlightMode(Enum):
    """The modes a user of this ground station can pick.

    CINE is DJI's CineSmooth (gentle) profile, NORMAL is ordinary GPS Position flight,
    SPORT is the fast, geofence-relaxed profile. The value doubles as the canonical
    lower-case CLI name.
    """

    CINE = "cine"
    NORMAL = "normal"
    SPORT = "sport"


class RcSoftSwitchMode(Enum):
    """RemoteController/SoftSwitchMode, as DJI Fly emits it.

    Each value selects one FC config block: POSITION -> Normal block, SPORT -> Sport block,
    TRIPOD -> CineSmooth/gentle block. On the Mini, Cine is delivered through the TRIPOD gear
    position — a hypothesis still flagged for the on-drone checklist (it reports FLYC_STATE
    TRIPOD_GPS=38 rather than Cinematic=19).
    """

    POSITION = "position"
    SPORT = "sport"
    TRIPOD = "tripod"


# Cine -> Tripod, Normal -> Position, Sport -> Sport.
_GEAR_FOR_MODE = {
    FlightMode.CINE: RcSoftSwitchMode.TRIPOD,
    FlightMode.NORMAL: RcSoftSwitchMode.POSITION,
    FlightMode.SPORT: RcSoftSwitchMode.SPORT,
}

# The *firmware* ordinals for each gear. These deliberately differ from the declaration
# order above — never derive the wire byte from the enum's position, always go through here.
_WIRE_VALUE = {
    RcSoftSwitchMode.SPORT: 0,
    RcSoftSwitchMode.POSITION: 1,
    RcSoftSwitchMode.TRIPOD: 2,
}

# Names accepted from the CLI/GUI. "cinema" is what the existing GUI widget already sends and
# "position" is the firmware's own word for Normal. "tripod" is deliberately absent: it is a
# gear value, not a user mode. So is the old "max" alias — that was a tilt/speed setting, and
# speed now stays with hspeed (set_horizontal_speed), never with mode selection.
_NAME_ALIASES = {
    "cine": FlightMode.CINE,
    "cinema": FlightMode.CINE,
    "cinematic": FlightMode.CINE,
    "normal": FlightMode.NORMAL,
    "position": FlightMode.NORMAL,
    "sport": FlightMode.SPORT,
}


def flight_mode_name(mode: FlightMode) -> str:
    """Canonical lower-case name ("cine" / "normal" / "sport")."""
    return mode.value


def flight_mode_from_name(name: str) -> FlightMode | None:
    """Parse a user-supplied mode name. Case/space-insensitive; None if unrecognised."""
    return _NAME_ALIASES.get(str(name).strip().lower())


def soft_switch_for(mode: FlightMode) -> RcSoftSwitchMode:
    """The gear position that activates a given user mode."""
    return _GEAR_FOR_MODE[mode]


def soft_switch_wire_value(gear: RcSoftSwitchMode) -> int:
    """The byte the firmware expects for a gear (SPORT=0, POSITION=1, TRIPOD=2)."""
    return _WIRE_VALUE[gear]


# --- SoftSwitchMode DUML frame -----------------------------------------------------------
# Best-effort from the DJI Fly KeyValue reverse; every constant is re-verified on the drone.
# The exact cmd_id is still unconfirmed, so all three reverse-engineered candidates ship and
# either config or the OSD auto-detector (soft_switch_detect.py) picks the winner.


class SoftSwitchCmdId(IntEnum):
    """Candidate cmd_ids for the SoftSwitchMode key inside RC cmd_set 0x06."""

    SET_MACHINE_MODE = 0x06
    SET_FUNCTION_SWITCH = 0x11
    SET_CONTROLLER_MODE = 0x19


RC_CMD_SET = 0x06    # RC-component command set
RC_RECEIVER = 0x06   # RC device DUML address (drone.py's DEV_RC was 0x02 = the app; fixed)


def soft_switch_cmd_id_from(value: int) -> SoftSwitchCmdId | None:
    """Validate a raw byte as one of the three candidates, or None.

    Mirrors the C++ soft_switch_cmd_id_from(): a typo in `smid` must never latch an arbitrary
    cmd_id onto the control path, where it would go out to the RC as an unknown command.
    """
    try:
        return SoftSwitchCmdId(value)
    except ValueError:
        return None


def soft_switch_payload(gear: RcSoftSwitchMode) -> bytes:
    """Payload: the wire value as one little-endian u32 (value is 0..2, so signedness is moot)."""
    return struct.pack("<I", soft_switch_wire_value(gear))


def make_soft_switch_packet(gear: RcSoftSwitchMode, cmd_id: SoftSwitchCmdId,
                            sender: int, seq: int) -> DumlPacket:
    """Assemble the full DUML packet that selects a gear.

    cmd_set / receiver / cmd_type are fixed to the SoftSwitchMode contract; the caller
    supplies its app sender address (0x02) and the next sequence number. ACK is requested
    (cmd_type 0x40) and the frame stays plaintext — RC frames are not SIMPLE-encrypted the
    way FLYC 0x03 config writes are.
    """
    return DumlPacket(
        sender=sender,
        receiver=RC_RECEIVER,
        cmd_set=RC_CMD_SET,
        cmd_id=int(cmd_id),
        payload=soft_switch_payload(gear),
        seq=seq,
        cmd_type=0x40,
    )
