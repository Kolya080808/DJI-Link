#include "core/flight_mode.hpp"

#include <algorithm>
#include <cctype>
#include <string>

namespace djilink {
namespace {

std::string to_lower(std::string_view s) {
    std::string out(s);
    std::transform(out.begin(), out.end(), out.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return out;
}

} // namespace

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

} // namespace djilink
