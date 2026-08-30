#include "core/flight_mode.hpp"

#include "core/duml.hpp"

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

Bytes soft_switch_payload(RcSoftSwitchMode gear) {
    Bytes payload;
    put_u32(payload, soft_switch_wire_value(gear));
    return payload;
}

DumlPacket make_soft_switch_packet(RcSoftSwitchMode gear, SoftSwitchCmdId cmd_id,
                                   std::uint8_t sender, std::uint16_t seq) {
    DumlPacket pkt;
    pkt.sender = sender;
    pkt.receiver = kRcReceiver;
    pkt.cmd_set = kRcCmdSet;
    pkt.cmd_id = static_cast<std::uint8_t>(cmd_id);
    pkt.payload = soft_switch_payload(gear);
    pkt.seq = seq;
    pkt.cmd_type = 0x40; // ACK required; plaintext (RC frames are not SIMPLE-encrypted)
    return pkt;
}

} // namespace djilink
