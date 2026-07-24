#include "core/control.hpp"

#include <algorithm>
#include <cmath>

namespace djilink {
namespace {
double clamp1(double v) {
    return std::max(-1.0, std::min(1.0, v));
}
} // namespace

Sticks Sticks::clamp() const {
    return {clamp1(roll), clamp1(pitch), clamp1(yaw), clamp1(throttle)};
}

double Sticks::axis(const std::string& name) const {
    if (name == "roll")
        return roll;
    if (name == "pitch")
        return pitch;
    if (name == "yaw")
        return yaw;
    if (name == "throttle")
        return throttle;
    return 0.0;
}

Sticks keys_to_sticks(const std::set<std::string>& pressed) {
    auto has = [&](const char* k) { return pressed.count(k) > 0; };
    Sticks s;
    if (has("w"))
        s.pitch += 1.0;
    if (has("s"))
        s.pitch -= 1.0;
    if (has("d"))
        s.roll += 1.0;
    if (has("a"))
        s.roll -= 1.0;
    if (has("space"))
        s.throttle += 1.0;
    if (has("shift"))
        s.throttle -= 1.0;
    if (has("e") || has("right"))
        s.yaw += 1.0;
    if (has("q") || has("left"))
        s.yaw -= 1.0;
    return s.clamp();
}

namespace {
// [-1..1] -> 11-bit DJI channel (center 1024, +-660, clamp 364..1684).
int chan(double v, const FlightProfile& prof) {
    int raw = prof.center + static_cast<int>(std::llround(v * prof.axis_range));
    raw = std::max(prof.center - prof.axis_range, std::min(prof.center + prof.axis_range, raw));
    return raw & 0x7FF;
}
} // namespace

Bytes sticks_to_payload(const Sticks& s, const FlightProfile& prof) {
    int ch[4];
    for (int i = 0; i < 4; ++i)
        ch[i] = chan(s.axis(prof.order[static_cast<std::size_t>(i)]), prof);

    // TLV #1 (0x01, len 13): 8 bytes of packed channels + uint32 flags + byte 0x06.
    const std::uint64_t packed =
        (static_cast<std::uint64_t>(ch[0]) << 8) | (static_cast<std::uint64_t>(ch[1]) << 19) |
        (static_cast<std::uint64_t>(ch[2]) << 30) | (static_cast<std::uint64_t>(ch[3]) << 41) |
        (static_cast<std::uint64_t>(1) << 62);
    Bytes tlv1_val;
    for (int i = 0; i < 8; ++i)
        tlv1_val.push_back(static_cast<std::uint8_t>((packed >> (8 * i)) & 0xFF));
    put_u32(tlv1_val, prof.flags_word);
    tlv1_val.push_back(0x06);

    Bytes out;
    out.push_back(0x01);
    out.push_back(static_cast<std::uint8_t>(tlv1_val.size()));
    out.insert(out.end(), tlv1_val.begin(), tlv1_val.end());
    // TLV #2 (0x55, len 1): 0x04.
    out.push_back(0x55);
    out.push_back(0x01);
    out.push_back(0x04);
    return out;
}

} // namespace djilink
