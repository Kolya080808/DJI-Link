// Unit test for the FLYC_STATE -> user-mode derivation (roadmap T4). Two things are covered:
//   1. derived_user_mode() maps the raw FLYC_STATE code to the right DerivedFlightMode, and
//      returns nullopt for transient/action states.
//   2. Fed through Telemetry, the OsdState::user_mode is STICKY: a transient state keeps the last
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
        require(derived_user_mode(31) == DerivedFlightMode::Sport, "31 -> Sport");
        require(derived_user_mode(19) == DerivedFlightMode::Cine, "19 -> Cine");
        require(derived_user_mode(38) == DerivedFlightMode::Tripod, "38 -> Tripod (kept != Cine)");

        // --- ordinary controlled flight (GPS / Atti / Hover / Novice family) is Normal ---
        for (int code : {1, 2, 3, 4, 5, 6, 7, 8, 23, 32}) {
            require(derived_user_mode(code) == DerivedFlightMode::Normal,
                    "GPS/Atti/Hover/Novice code should derive Normal");
        }

        // --- transient / action / intelligent states derive nothing (keep last) ---
        for (int code : {0, 9, 10, 11, 12, 13, 14, 15, 16, 17, 24, 28, 30, 33, 37, 100}) {
            require(derived_user_mode(code) == std::nullopt,
                    "transient state must not derive a mode");
        }

        // --- names ---
        require(derived_flight_mode_name(DerivedFlightMode::Normal) == "Normal", "name Normal");
        require(derived_flight_mode_name(DerivedFlightMode::Sport) == "Sport", "name Sport");
        require(derived_flight_mode_name(DerivedFlightMode::Cine) == "Cine", "name Cine");
        require(derived_flight_mode_name(DerivedFlightMode::Tripod) == "Tripod", "name Tripod");

        // --- sticky behaviour through Telemetry ---
        Telemetry tel;
        // Before any decisive state, a transient push leaves user_mode unset.
        tel.feed_packet(osd(17)); // Joystick
        require(!tel.state().user_mode.has_value(), "no decisive state seen yet -> nullopt");
        // The raw FLYC_STATE fields are still populated regardless.
        require(tel.state().flight_mode == 17, "raw flight_mode still parsed");
        require(tel.state().flight_mode_name == "Joystick", "raw flight_mode_name still parsed");

        // Sport becomes the sticky mode; a following transient state keeps it.
        tel.feed_packet(osd(31)); // SPORT
        require(tel.state().user_mode == DerivedFlightMode::Sport, "sport is decisive");
        tel.feed_packet(osd(17)); // Joystick (virtual sticks) — gear does not apply here
        require(tel.state().user_mode == DerivedFlightMode::Sport, "transient keeps Sport");
        tel.feed_packet(osd(11)); // AutoTakeoff
        require(tel.state().user_mode == DerivedFlightMode::Sport, "takeoff keeps Sport");

        // M1 (roadmap): losing GPS degrades Sport into Atti — a DECISIVE Normal, not a transient
        // action — so the sticky mode intentionally drops to Normal. Stickiness guards against
        // transient actions, never against a real flight-block change.
        tel.feed_packet(osd(1)); // Atti (GPS lost)
        require(tel.state().user_mode == DerivedFlightMode::Normal,
                "GPS loss (Atti) is decisive Normal");
        tel.feed_packet(osd(31)); // re-select SPORT
        require(tel.state().user_mode == DerivedFlightMode::Sport, "Sport re-selected after Atti");

        // A decisive Normal push overwrites it; RTH then keeps Normal.
        tel.feed_packet(osd(6)); // GPS_Atti
        require(tel.state().user_mode == DerivedFlightMode::Normal, "GPS_Atti is decisive Normal");
        tel.feed_packet(osd(15)); // GoHome
        require(tel.state().user_mode == DerivedFlightMode::Normal, "RTH keeps Normal");

        // Tripod and Cine each overwrite when reported.
        tel.feed_packet(osd(38)); // TRIPOD_GPS
        require(tel.state().user_mode == DerivedFlightMode::Tripod, "tripod is decisive");
        tel.feed_packet(osd(19)); // Cinematic
        require(tel.state().user_mode == DerivedFlightMode::Cine, "cine is decisive");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
