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

#include <optional>
#include <string>
#include <string_view>

namespace djilink {

// The modes a user of this ground station can pick. "Cine" is DJI's CineSmooth (gentle) profile;
// "Normal" is ordinary GPS Position flight; "Sport" is the fast, geofence-relaxed profile.
enum class FlightMode { Cine, Normal, Sport };

// RemoteController/SoftSwitchMode, as DJI Fly emits it. Each value selects one FC config block:
// Position -> Normal block, Sport -> Sport block, Tripod -> CineSmooth/gentle block (on the Mini,
// Cine is delivered via the Tripod gear position — a hypothesis flagged for hardware check in the
// roadmap's open unknowns).
enum class RcSoftSwitchMode { Position, Sport, Tripod };

// Canonical lower-case name of a mode ("cine" / "normal" / "sport"). Total, never throws.
std::string_view flight_mode_name(FlightMode mode);

// Parse a user-supplied mode name into a FlightMode. Case-insensitive; accepts the canonical
// names plus the aliases the GUI/CLI already send ("cinema" for Cine, "position" for Normal).
// Returns nullopt for anything unrecognised (including "tripod", which is a gear value, not a
// user mode) so callers decide how to report the error.
std::optional<FlightMode> flight_mode_from_name(std::string_view name);

// The RcSoftSwitchMode (gear position) that activates a given user mode. Total mapping:
//   Cine -> Tripod, Normal -> Position, Sport -> Sport.
RcSoftSwitchMode soft_switch_for(FlightMode mode);

} // namespace djilink
