// Unit test for the pure flight-mode model (roadmap T1). No transport, no hardware: it only
// exercises the name<->enum conversions and the FlightMode->RcSoftSwitchMode gear mapping.
#include "core/flight_mode.hpp"

#include <iostream>
#include <optional>
#include <stdexcept>

using namespace djilink;

// The two pure mappings are constexpr, so pin that down at compile time as well: a future
// refactor that makes them runtime-only would break this build, not just a test.
static_assert(flight_mode_name(FlightMode::Sport) == "sport");
static_assert(soft_switch_for(FlightMode::Cine) == RcSoftSwitchMode::Tripod);

namespace {
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
} // namespace

int main() {
    try {
        // Canonical names.
        require(flight_mode_name(FlightMode::Cine) == "cine", "cine name");
        require(flight_mode_name(FlightMode::Normal) == "normal", "normal name");
        require(flight_mode_name(FlightMode::Sport) == "sport", "sport name");

        // name -> enum -> name round-trips for every mode.
        for (FlightMode m : {FlightMode::Cine, FlightMode::Normal, FlightMode::Sport}) {
            auto parsed = flight_mode_from_name(flight_mode_name(m));
            require(parsed.has_value() && *parsed == m, "canonical round-trip");
        }

        // Aliases the GUI/CLI actually send, plus case-insensitivity.
        require(flight_mode_from_name("cinema") == FlightMode::Cine, "cinema alias");
        require(flight_mode_from_name("POSITION") == FlightMode::Normal, "position alias, upper");
        require(flight_mode_from_name("Sport") == FlightMode::Sport, "mixed case");

        // Unrecognised input -> nullopt. "tripod" is a gear value, not a user mode.
        require(!flight_mode_from_name("tripod").has_value(), "tripod is not a user mode");
        require(!flight_mode_from_name("").has_value(), "empty is rejected");
        require(!flight_mode_from_name("banana").has_value(), "garbage is rejected");

        // FlightMode -> RcSoftSwitchMode (gear) mapping.
        require(soft_switch_for(FlightMode::Normal) == RcSoftSwitchMode::Position,
                "normal -> position");
        require(soft_switch_for(FlightMode::Sport) == RcSoftSwitchMode::Sport, "sport -> sport");
        require(soft_switch_for(FlightMode::Cine) == RcSoftSwitchMode::Tripod, "cine -> tripod");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
