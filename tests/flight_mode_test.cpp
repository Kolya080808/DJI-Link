// Unit test for the pure flight-mode model (roadmap T1) and the SoftSwitchMode frame encoder
// (roadmap T2). No transport, no hardware: it exercises the name<->enum conversions, the
// FlightMode->RcSoftSwitchMode gear mapping, and the deterministic bytes of the DUML frame.
#include "core/bytes.hpp"
#include "core/duml.hpp"
#include "core/flight_mode.hpp"

#include <iostream>
#include <optional>
#include <stdexcept>

using namespace djilink;

// The two pure mappings are constexpr, so pin that down at compile time as well: a future
// refactor that makes them runtime-only would break this build, not just a test.
static_assert(flight_mode_name(FlightMode::Sport) == "sport");
static_assert(soft_switch_for(FlightMode::Cine) == RcSoftSwitchMode::Tripod);

// T2 wire values are the firmware ordinals (SPORT=0, POSITION=1, TRIPOD=2), which are NOT the
// C++ enum order — guard that they never silently collapse back to a raw enum cast.
static_assert(soft_switch_wire_value(RcSoftSwitchMode::Sport) == 0);
static_assert(soft_switch_wire_value(RcSoftSwitchMode::Position) == 1);
static_assert(soft_switch_wire_value(RcSoftSwitchMode::Tripod) == 2);

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

        // --- T2: SoftSwitchMode frame encoder ---
        // Payload is the gear's wire value as a little-endian 32-bit word.
        require(soft_switch_payload(RcSoftSwitchMode::Sport) == Bytes{0, 0, 0, 0}, "sport payload");
        require(soft_switch_payload(RcSoftSwitchMode::Position) == Bytes{1, 0, 0, 0},
                "position payload");
        require(soft_switch_payload(RcSoftSwitchMode::Tripod) == Bytes{2, 0, 0, 0},
                "tripod payload");

        // End-to-end with the T1 model: a user mode maps through the gear to the right payload.
        require(soft_switch_payload(soft_switch_for(FlightMode::Sport)) == Bytes{0, 0, 0, 0},
                "Sport mode -> sport gear payload");
        require(soft_switch_payload(soft_switch_for(FlightMode::Normal)) == Bytes{1, 0, 0, 0},
                "Normal mode -> position gear payload");

        // The assembled packet pins the SoftSwitchMode contract, and survives a wire round-trip,
        // for every candidate cmd_id (0x02 is the app sender address, DEV_APP in drone.hpp).
        for (SoftSwitchCmdId id :
             {SoftSwitchCmdId::SetMachineMode, SoftSwitchCmdId::SetFunctionSwitch,
              SoftSwitchCmdId::SetControllerMode}) {
            const DumlPacket pkt =
                make_soft_switch_packet(RcSoftSwitchMode::Sport, id, 0x02, 0x1234);
            require(pkt.sender == 0x02, "packet sender is the app address");
            require(pkt.receiver == kRcReceiver && pkt.receiver == 0x06, "packet targets the RC");
            require(pkt.cmd_set == kRcCmdSet && pkt.cmd_set == 0x06, "packet uses the RC cmd_set");
            require(pkt.cmd_id == static_cast<std::uint8_t>(id),
                    "packet carries the chosen cmd_id");
            require(pkt.cmd_type == 0x40, "packet requests ACK");
            require(pkt.payload == Bytes{0, 0, 0, 0}, "packet payload is the sport wire value");

            // Deterministic round-trip: encode -> decode reproduces every field bit-for-bit.
            const Bytes frame = pkt.encode();
            require(!frame.empty() && frame.front() == DUML_MAGIC, "frame starts with DUML magic");
            const std::optional<DumlPacket> decoded = DumlPacket::decode(frame);
            require(decoded.has_value(), "frame decodes");
            require(decoded->sender == pkt.sender && decoded->receiver == pkt.receiver,
                    "round-trip addresses");
            require(decoded->cmd_set == pkt.cmd_set && decoded->cmd_id == pkt.cmd_id,
                    "round-trip cmd");
            require(decoded->cmd_type == pkt.cmd_type, "round-trip cmd_type (ACK flag)");
            require(decoded->seq == 0x1234, "round-trip seq");
            require(decoded->payload == pkt.payload, "round-trip payload");
        }

        // A non-Sport gear travels through encode->decode with its (non-zero) payload intact,
        // so the round-trip above is not just exercising the all-zero Sport buffer.
        const DumlPacket normal_pkt = make_soft_switch_packet(
            RcSoftSwitchMode::Position, SoftSwitchCmdId::SetMachineMode, 0x02, 7);
        require(normal_pkt.payload == Bytes{1, 0, 0, 0}, "position packet payload");
        const std::optional<DumlPacket> normal_decoded = DumlPacket::decode(normal_pkt.encode());
        require(normal_decoded.has_value() && normal_decoded->payload == Bytes{1, 0, 0, 0},
                "position round-trip payload");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
