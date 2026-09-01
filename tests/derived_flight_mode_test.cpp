// Unit test for the FLYC_STATE -> observed-profile derivation. Two things are covered:
//   1. observed_flight_profile() maps explicit codes and rejects ambiguous states, and
//      returns nullopt for transient/action states.
//   2. Fed through Telemetry, OsdState::observed_profile keeps the last explicit value.
//      decisive value instead of dropping to Normal ("transient states keep last").
// Display-free and network-free: we hand-build OSD-common frames and feed them to Telemetry.
#include "core/duml.hpp"
#include "core/telemetry.hpp"

#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>

using namespace djilink;

namespace {
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

// A minimal OSD-common push (cmd_set 0x03 / cmd_id 0x43) carrying FLYC_STATE @0x1e. The payload
// only needs to clear Telemetry's size gate (>=0x34); every other field reads as zero.
DumlPacket osd(int flyc_state) {
    DumlPacket pkt;
    pkt.cmd_set = 0x03;
    pkt.cmd_id = 0x43;
    pkt.payload = Bytes(0x40, 0);
    pkt.payload[0x1e] = static_cast<std::uint8_t>(flyc_state);
    return pkt;
}
} // namespace

int main() {
    try {
        // --- pure mapping: the three explicitly selectable modes ---
        require(observed_flight_profile(31) == ObservedFlightProfile::Sport, "31 -> Sport");
        require(observed_flight_profile(19) == ObservedFlightProfile::Cine, "19 -> Cine");
        require(observed_flight_profile(38) == ObservedFlightProfile::Tripod, "38 -> Tripod");

        // --- ordinary controlled flight (GPS / Atti / Hover / Novice family) is Normal ---
        for (int code : {6, 32}) {
            require(observed_flight_profile(code) == ObservedFlightProfile::Normal,
                    "explicit normal code should derive Normal");
        }

        // --- transient / action / intelligent states derive nothing (keep last) ---
        for (int code : {0,  1,  2,  3,  4,  5,  7,  8,  9,  10, 11, 12,
                         13, 14, 15, 16, 17, 23, 24, 28, 30, 33, 37, 100}) {
            require(observed_flight_profile(code) == std::nullopt,
                    "ambiguous state must not derive a profile");
        }

        // --- names ---
        require(observed_flight_profile_name(ObservedFlightProfile::Normal) == "Normal",
                "name Normal");
        require(observed_flight_profile_name(ObservedFlightProfile::Sport) == "Sport",
                "name Sport");
        require(observed_flight_profile_name(ObservedFlightProfile::Cine) == "Cine", "name Cine");
        require(observed_flight_profile_name(ObservedFlightProfile::Tripod) == "Tripod",
                "name Tripod");

        // --- sticky behaviour through Telemetry ---
        Telemetry tel;
        // Before any explicit state, an ambiguous push leaves the profile unset.
        tel.feed_packet(osd(17)); // Joystick
        require(!tel.state().observed_profile.has_value(), "no explicit profile seen yet");
        // The raw FLYC_STATE fields are still populated regardless.
        require(tel.state().flight_mode == 17, "raw flight_mode still parsed");
        require(tel.state().flight_mode_name == "Joystick", "raw flight_mode_name still parsed");

        // Sport becomes the sticky mode; a following transient state keeps it.
        tel.feed_packet(osd(31)); // SPORT
        require(tel.state().observed_profile == ObservedFlightProfile::Sport, "sport is explicit");
        tel.feed_packet(osd(17)); // Joystick (virtual sticks) — gear does not apply here
        require(tel.state().observed_profile == ObservedFlightProfile::Sport,
                "joystick keeps Sport");
        tel.feed_packet(osd(11)); // AutoTakeoff
        require(tel.state().observed_profile == ObservedFlightProfile::Sport,
                "takeoff keeps Sport");

        // M1 (roadmap): losing GPS degrades Sport into Atti — a DECISIVE Normal, not a transient
        // action — so the sticky mode intentionally drops to Normal. Stickiness guards against
        // transient actions, never against a real flight-block change.
        tel.feed_packet(osd(1)); // Atti (GPS lost)
        require(tel.state().observed_profile == ObservedFlightProfile::Sport,
                "GPS loss does not identify the selected profile");
        tel.feed_packet(osd(31)); // re-select SPORT
        require(tel.state().observed_profile == ObservedFlightProfile::Sport,
                "Sport re-observed after Atti");

        // A decisive Normal push overwrites it; RTH then keeps Normal.
        tel.feed_packet(osd(6)); // GPS_Atti
        require(tel.state().observed_profile == ObservedFlightProfile::Normal,
                "GPS_Atti is explicit Normal");
        tel.feed_packet(osd(15)); // GoHome
        require(tel.state().observed_profile == ObservedFlightProfile::Normal, "RTH keeps Normal");

        // Tripod and Cine each overwrite when reported.
        tel.feed_packet(osd(38)); // TRIPOD_GPS
        require(tel.state().observed_profile == ObservedFlightProfile::Tripod,
                "tripod is explicit");
        tel.feed_packet(osd(19)); // Cinematic
        require(tel.state().observed_profile == ObservedFlightProfile::Cine, "cine is explicit");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
