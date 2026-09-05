// Unit test for Drone::set_flight_mode (roadmap T3): the mode command must leave the wire as a
// real RC-component SoftSwitchMode frame (cmd_set 0x06, receiver 0x06) built from the T2 encoder,
// NOT the old FLYC 0x03 tilt write. A fake transport captures the emitted frame and we decode it.
// set_horizontal_speed must stay a separate FLYC tilt/speed write, proving the two paths split.
#include "core/drone.hpp"
#include "core/duml.hpp"
#include "core/flight_mode.hpp"

#include <cstdint>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <vector>

using namespace djilink;

namespace {
class CaptureTransport final : public Transport {
public:
    void send(const Bytes& data) override {
        frames.push_back(data);
    }
    Bytes recv(int) override {
        return {};
    }
    std::vector<Bytes> frames;
};

void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

// Decode the single most recent frame and assert it is a well-formed SoftSwitchMode packet for
// the given gear wire value and cmd_id. Returns the decoded packet for further field checks.
DumlPacket decode_last(const CaptureTransport& t, const char* what) {
    require(!t.frames.empty(), what);
    const std::optional<DumlPacket> pkt = DumlPacket::decode(t.frames.back());
    require(pkt.has_value(), what);
    return *pkt;
}
} // namespace

int main() {
    try {
        CaptureTransport transport;
        Drone drone(&transport);
        // Plaintext so every captured frame decodes; cmd() would SIMPLE-encrypt FLYC 0x03 config
        // otherwise (the console client sets this too), which would scramble the speed check.
        drone.encrypt_config = false;

        // --- typed set_flight_mode(FlightMode) sends a SoftSwitchMode gear frame ---
        const std::size_t before = transport.frames.size();
        drone.set_flight_mode(FlightMode::Sport);
        require(transport.frames.size() == before + 1, "one frame per mode switch");
        DumlPacket pkt = decode_last(transport, "sport mode frame did not decode");
        require(pkt.sender == DEV_APP && pkt.sender == 0x02, "mode frame sender is the app");
        require(pkt.receiver == kRcReceiver && pkt.receiver == 0x06, "mode frame targets the RC");
        require(pkt.cmd_set == kRcCmdSet && pkt.cmd_set == 0x06, "mode frame uses the RC cmd_set");
        // The whole point of T3: mode selection must NOT be a FLYC 0x03 param/tilt write anymore.
        require(pkt.cmd_set != 0x03, "mode frame must not be a FLYC config write");
        require(pkt.cmd_id == static_cast<std::uint8_t>(SoftSwitchCmdId::SetMachineMode) &&
                    pkt.cmd_id == 0x06,
                "mode frame uses the default candidate cmd_id");
        require(pkt.cmd_type == 0x40, "mode frame requests ACK");
        require(pkt.payload == soft_switch_payload(RcSoftSwitchMode::Sport) &&
                    pkt.payload == Bytes{0, 0, 0, 0},
                "sport gear wire value");

        // The Drone path builds the frame inline through cmd() rather than the T2 encoder, so pin
        // that it stays byte-identical to make_soft_switch_packet for the same gear/cmd_id/seq —
        // the two independent builders of the SoftSwitchMode contract must never silently diverge.
        const Bytes expected =
            make_soft_switch_packet(RcSoftSwitchMode::Sport, SoftSwitchCmdId::SetMachineMode,
                                    DEV_APP, pkt.seq)
                .encode();
        require(transport.frames.back() == expected,
                "Drone frame mirrors make_soft_switch_packet byte-for-byte");

        // Normal -> Position gear (wire 1), Cine -> Tripod gear (wire 2): the non-zero payloads
        // prove the encoder is not just emitting the all-zero Sport buffer for every mode.
        drone.set_flight_mode(FlightMode::Normal);
        require(decode_last(transport, "normal frame").payload == Bytes{1, 0, 0, 0},
                "normal -> position gear wire value");
        drone.set_flight_mode(FlightMode::Cine);
        require(decode_last(transport, "cine frame").payload == Bytes{2, 0, 0, 0},
                "cine -> tripod gear wire value");

        // --- string overload is a thin wrapper over the typed one ---
        // The aliases the GUI/CLI actually send resolve to the same wire frame.
        drone.set_flight_mode("sport");
        require(decode_last(transport, "\"sport\" frame").payload == Bytes{0, 0, 0, 0},
                "\"sport\" matches FlightMode::Sport");
        drone.set_flight_mode("position");
        require(decode_last(transport, "\"position\" frame").payload == Bytes{1, 0, 0, 0},
                "\"position\" alias maps to Normal gear");
        drone.set_flight_mode("cinema");
        require(decode_last(transport, "\"cinema\" frame").payload == Bytes{2, 0, 0, 0},
                "\"cinema\" alias maps to Cine gear");

        // Unknown names are rejected (they used to silently pick a tilt value).
        bool rejected = false;
        try {
            drone.set_flight_mode("banana");
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        require(rejected, "unknown mode name was not rejected");

        // --- configurable candidate cmd_id (roadmap T7 picks the real one on hardware) ---
        drone.set_soft_switch_cmd_id(SoftSwitchCmdId::SetControllerMode);
        drone.set_flight_mode(FlightMode::Sport);
        require(decode_last(transport, "reconfigured cmd_id frame").cmd_id == 0x19,
                "cmd_id follows set_soft_switch_cmd_id");
        drone.set_soft_switch_cmd_id(SoftSwitchCmdId::SetFunctionSwitch);
        drone.set_flight_mode(FlightMode::Sport);
        require(decode_last(transport, "reconfigured cmd_id frame 2").cmd_id == 0x11,
                "cmd_id follows the second reconfiguration");

        // --- set_horizontal_speed stays a separate FLYC tilt/speed write ---
        // It must NOT emit an RC SoftSwitchMode frame: speed and mode are unrelated now.
        drone.set_horizontal_speed(6.0);
        const DumlPacket speed = decode_last(transport, "speed frame");
        require(speed.cmd_set == 0x03 && speed.cmd_id == 0xF9,
                "horizontal speed is still a FLYC param write");
        require(speed.cmd_set != kRcCmdSet, "horizontal speed must not use the RC cmd_set");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
