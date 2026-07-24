// PC keyboard -> virtual sticks -> DUML flight command, ported from control.py.
// The legacy special_tlv packet (cmd_set 0x01 / cmd_id 0x0A, 11-bit channels).
#pragma once

#include "core/bytes.hpp"

#include <array>
#include <cstdint>
#include <set>
#include <string>

namespace djilink {

// WM160 virtual-stick command profile (legacy special_tlv path).
struct FlightProfile {
    std::uint8_t cmd_set = 0x01;
    std::uint8_t cmd_id = 0x0A;
    int center = 1024;
    int axis_range = 660;
    // ch0..ch3 order — VERIFIED from an RC dump.
    std::array<std::string, 4> order = {"roll", "pitch", "throttle", "yaw"};
    std::uint32_t flags_word = 0x00000200;
};

struct Sticks {
    double roll = 0.0;     // -1 (left)     .. +1 (right)
    double pitch = 0.0;    // -1 (backward) .. +1 (forward)
    double yaw = 0.0;      // -1 (left)     .. +1 (right)
    double throttle = 0.0; // -1 (down)     .. +1 (up)

    Sticks clamp() const;
    double axis(const std::string& name) const; // by name, for FlightProfile.order
};

// Map the set of held (normalized) key names to stick axes.
Sticks keys_to_sticks(const std::set<std::string>& pressed);

// Real payload of the special_tlv command (cmd_set 0x01, cmd_id 0x0A) for WM160.
Bytes sticks_to_payload(const Sticks& s, const FlightProfile& prof);

} // namespace djilink
