// Unit test for the simulator's flight-mode feedback (roadmap T6). The sim transport
// (LogTransport) must react to a SoftSwitchMode gear frame by changing the FLYC_STATE it streams
// back, so `--sim` demonstrates a real mode switch and T7's auto-detect can terminate.
//
// End-to-end and hardware-free: we feed a real make_soft_switch_packet frame into send(), then
// decode the next recv() and also run it through Telemetry to confirm the derived user mode flips.
#include "core/duml.hpp"
#include "core/flight_mode.hpp"
#include "core/telemetry.hpp"
#include "core/transport.hpp"

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

// The FLYC_STATE the sim currently reports: decode the next OSD-common push from recv() and read
// byte 0x1e. Also asserts the frame is a well-formed OSD push (decodable, right cmd, size gate).
// A tiny timeout keeps the test fast (recv() paces itself to the caller's timeout).
std::uint8_t reported_state(LogTransport& lt) {
    const Bytes raw = lt.recv(1);
    const auto pkt = DumlPacket::decode(raw);
    require(pkt.has_value(), "recv() returns a decodable DUML frame");
    require(pkt->cmd_set == 0x03 && pkt->cmd_id == 0x43, "recv() is an OSD-common push");
    require(pkt->payload.size() >= 0x34, "OSD payload clears Telemetry's size gate");
    const auto s = get_u8(pkt->payload, 0x1e);
    require(s.has_value(), "FLYC_STATE present at 0x1e");
    return *s;
}

// Push a gear via a real SoftSwitchMode frame, exactly as Drone::set_flight_mode does.
void select_gear(LogTransport& lt, RcSoftSwitchMode gear,
                 SoftSwitchCmdId cmd_id = SoftSwitchCmdId::SetMachineMode) {
    lt.send(make_soft_switch_packet(gear, cmd_id, /*sender=*/0x02, /*seq=*/0).encode());
}

// The derived user mode Telemetry ends up in after consuming one sim push.
std::optional<DerivedFlightMode> derived_after_recv(LogTransport& lt) {
    Telemetry tel;
    tel.feed_packet(*DumlPacket::decode(lt.recv(1)));
    return tel.state().user_mode;
}
} // namespace

int main() {
    try {
        // --- default before any command: a decisive Normal (GPS_Atti = 6) ---
        {
            LogTransport lt(/*verbose=*/false);
            require(reported_state(lt) == 6, "sim defaults to GPS_Atti (Normal)");
            require(derived_after_recv(lt) == DerivedFlightMode::Normal, "default derives Normal");
        }

        // --- each gear flips the reported FLYC_STATE and the derived mode ---
        {
            LogTransport lt(/*verbose=*/false);

            select_gear(lt, soft_switch_for(FlightMode::Sport)); // Sport gear
            require(reported_state(lt) == 31, "Sport gear -> SPORT(31)");
            require(derived_after_recv(lt) == DerivedFlightMode::Sport, "Sport gear derives Sport");

            select_gear(lt, soft_switch_for(FlightMode::Normal)); // Position gear
            require(reported_state(lt) == 6, "Normal gear -> GPS_Atti(6)");
            require(derived_after_recv(lt) == DerivedFlightMode::Normal,
                    "Normal gear derives Normal");

            // Cine maps to the Tripod gear on the Mini, so the sim reports TRIPOD_GPS(38) and the
            // HUD reads Tripod — the documented Cine<->Tripod open question (T9 hardware
            // checklist).
            select_gear(lt, soft_switch_for(FlightMode::Cine)); // Tripod gear
            require(reported_state(lt) == 38, "Cine (Tripod gear) -> TRIPOD_GPS(38)");
            require(derived_after_recv(lt) == DerivedFlightMode::Tripod,
                    "Cine/Tripod derives Tripod");
        }

        // --- all three candidate cmd_ids are honoured (T7 auto-detect relies on this) ---
        for (const SoftSwitchCmdId id :
             {SoftSwitchCmdId::SetMachineMode, SoftSwitchCmdId::SetFunctionSwitch,
              SoftSwitchCmdId::SetControllerMode}) {
            LogTransport lt(/*verbose=*/false);
            select_gear(lt, RcSoftSwitchMode::Sport, id);
            require(reported_state(lt) == 31, "every candidate cmd_id flips the sim to SPORT");
        }

        // --- a non-SoftSwitch frame must NOT change the reported mode ---
        {
            LogTransport lt(/*verbose=*/false);
            DumlPacket other;
            other.receiver = 0x03;
            other.cmd_set = 0x03; // FLYC set, not the RC set — must be ignored by the sim
            other.cmd_id = 0x01;
            put_u32(other.payload, 0); // would map to SPORT if it were a gear frame
            lt.send(other.encode());
            require(reported_state(lt) == 6, "non-SoftSwitch frame leaves the mode unchanged");
        }

        // --- a SoftSwitch frame with an out-of-range gear value leaves the mode unchanged ---
        {
            LogTransport lt(/*verbose=*/false);
            DumlPacket bad;
            bad.receiver = kRcReceiver;
            bad.cmd_set = kRcCmdSet;
            bad.cmd_id = static_cast<std::uint8_t>(SoftSwitchCmdId::SetMachineMode);
            put_u32(bad.payload, 99); // no such gear
            lt.send(bad.encode());
            require(reported_state(lt) == 6, "unknown gear value leaves the mode unchanged");
        }

        // --- RC command set but a NON-candidate cmd_id is ignored (exercises the switch
        // fall-through in is_soft_switch) ---
        {
            LogTransport lt(/*verbose=*/false);
            DumlPacket rc;
            rc.receiver = kRcReceiver;
            rc.cmd_set = kRcCmdSet;
            rc.cmd_id = 0x07;       // not one of the three SoftSwitchMode candidates
            put_u32(rc.payload, 0); // would map to SPORT if the cmd_id matched
            lt.send(rc.encode());
            require(reported_state(lt) == 6, "non-candidate RC cmd_id leaves the mode unchanged");
        }

        // --- RC command set + candidate cmd_id but a too-short payload is rejected by the size
        // gate (payload.size() < 4) ---
        {
            LogTransport lt(/*verbose=*/false);
            DumlPacket rc;
            rc.receiver = kRcReceiver;
            rc.cmd_set = kRcCmdSet;
            rc.cmd_id = static_cast<std::uint8_t>(SoftSwitchCmdId::SetMachineMode);
            rc.payload = Bytes{0x00, 0x00, 0x00}; // only 3 bytes — no full u32 wire value
            lt.send(rc.encode());
            require(reported_state(lt) == 6, "short SoftSwitch payload leaves the mode unchanged");
        }

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
