#include "core/flight_mode.hpp"

#include <algorithm>
#include <cctype>

namespace djilink {
namespace {

std::string to_lower(std::string_view s) {
    std::string out(s);
    std::transform(out.begin(), out.end(), out.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return out;
}

} // namespace

std::string_view flight_mode_name(FlightMode mode) {
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

std::optional<FlightMode> flight_mode_from_name(std::string_view name) {
    const std::string key = to_lower(name);
    if (key == "cine" || key == "cinema")
        return FlightMode::Cine;
    if (key == "normal" || key == "position")
        return FlightMode::Normal;
    if (key == "sport")
        return FlightMode::Sport;
    return std::nullopt;
}

RcSoftSwitchMode soft_switch_for(FlightMode mode) {
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

} // namespace djilink
