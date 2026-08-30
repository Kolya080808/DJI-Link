// Flight-mode model for the WM160 ground station — pure, I/O-free core (roadmap T1).
//
// On the Mavic Mini there is no writable "current flight mode" FC parameter. The FC keeps four
// pre-loaded config blocks (Position / Sport / CineSmooth / Tripod) and the active one is chosen
// by the RC gear channel. DJI Fly emulates that gear with the KeyValue key
// RemoteController/SoftSwitchMode, routed to the RC component (cmdset 0x06) — NOT a FLYC 0x03
// write. This header holds only the model behind that mechanism: the three user-facing modes,
// their names, and the RcSoftSwitchMode (gear) value each maps to. Frame encoding is T2; the
// Drone command path is T3. Kept free of any transport/I/O so it unit-tests without hardware.
#pragma once

#include "core/bytes.hpp"

#include <cstdint>
#include <optional>
#include <string_view>

namespace djilink {

// From core/duml.hpp. Forward-declared so this pure model does not pull the transport codec
// into every translation unit that only needs the FlightMode enum (see telemetry.hpp).
struct DumlPacket;

// The modes a user of this ground station can pick. "Cine" is DJI's CineSmooth (gentle) profile;
// "Normal" is ordinary GPS Position flight; "Sport" is the fast, geofence-relaxed profile.
enum class FlightMode { Cine, Normal, Sport };

// RemoteController/SoftSwitchMode, as DJI Fly emits it. Each value selects one FC config block:
// Position -> Normal block, Sport -> Sport block, Tripod -> CineSmooth/gentle block (on the Mini,
// Cine is delivered via the Tripod gear position — a hypothesis flagged for hardware check in the
// roadmap's open unknowns).
enum class RcSoftSwitchMode { Position, Sport, Tripod };

// Canonical lower-case name of a mode ("cine" / "normal" / "sport"). Total, never throws.
constexpr std::string_view flight_mode_name(FlightMode mode) {
    switch (mode) {
        case FlightMode::Cine:
            return "cine";
        case FlightMode::Normal:
            return "normal";
        case FlightMode::Sport:
            return "sport";
    }
    return "normal"; // unreachable: the switch is exhaustive over the enum
}

// The RcSoftSwitchMode (gear position) that activates a given user mode. Total mapping:
//   Cine -> Tripod, Normal -> Position, Sport -> Sport.
constexpr RcSoftSwitchMode soft_switch_for(FlightMode mode) {
    switch (mode) {
        case FlightMode::Cine:
            return RcSoftSwitchMode::Tripod;
        case FlightMode::Normal:
            return RcSoftSwitchMode::Position;
        case FlightMode::Sport:
            return RcSoftSwitchMode::Sport;
    }
    return RcSoftSwitchMode::Position; // unreachable: the switch is exhaustive over the enum
}

// --- SoftSwitchMode DUML frame (roadmap T2) -------------------------------------------------
// The gear switch is an RC-component command (cmd_set 0x06), not a FLYC 0x03 write. The exact
// cmd_id is still unconfirmed on hardware, so we keep the three reverse-engineered candidates
// and let config / OSD auto-detection (roadmap T7) pick the winner. The receiver is the RC
// device (0x06), made explicit here because drone.hpp still aliases DEV_RC to the app address
// 0x02 (a bug fixed in T3). Every constant below is best-effort from the DJI Fly KeyValue
// reverse and is re-verified on the drone (see the roadmap's open unknowns).

inline constexpr std::uint8_t kRcCmdSet = 0x06;   // RC-component command set
inline constexpr std::uint8_t kRcReceiver = 0x06; // RC device DUML address

// Candidate cmd_ids for the SoftSwitchMode key; the winner is confirmed on hardware.
enum class SoftSwitchCmdId : std::uint8_t {
    SetMachineMode = 0x06,
    SetFunctionSwitch = 0x11,
    SetControllerMode = 0x19,
};

// Wire byte the firmware expects for each gear. These are the *firmware* ordinals
// (SPORT=0, POSITION=1, TRIPOD=2) and deliberately differ from RcSoftSwitchMode's C++
// declaration order — never cast the enum to a byte; always go through this mapping.
constexpr std::uint8_t soft_switch_wire_value(RcSoftSwitchMode gear) {
    switch (gear) {
        case RcSoftSwitchMode::Sport:
            return 0;
        case RcSoftSwitchMode::Position:
            return 1;
        case RcSoftSwitchMode::Tripod:
            return 2;
    }
    return 1; // unreachable: exhaustive over the enum; Position (=Normal) is the safe default
}

// SoftSwitchMode payload: the wire value as a single little-endian 32-bit word (written with
// put_u32; the value is 0..2, so signedness is moot). Best-effort from the KeyValue reverse; the
// exact RC-DUML wrapper is a roadmap open unknown.
Bytes soft_switch_payload(RcSoftSwitchMode gear);

// Assemble the full DUML packet that selects a gear. cmd_set / receiver / cmd_type are fixed to
// the SoftSwitchMode contract here; the caller supplies its app sender address and the next
// sequence number (Drone owns the atomic seq — roadmap T3). ACK is requested (cmd_type 0x40),
// plaintext: RC frames are not SIMPLE-encrypted like FLYC config.
DumlPacket make_soft_switch_packet(RcSoftSwitchMode gear, SoftSwitchCmdId cmd_id,
                                   std::uint8_t sender, std::uint16_t seq);

// Parse a user-supplied mode name into a FlightMode. Case-insensitive; accepts the canonical
// names plus the aliases the GUI/CLI already send ("cinema" for Cine, "position" for Normal).
// Returns nullopt for anything unrecognised (including "tripod", which is a gear value, not a
// user mode) so callers decide how to report the error.
std::optional<FlightMode> flight_mode_from_name(std::string_view name);

} // namespace djilink
