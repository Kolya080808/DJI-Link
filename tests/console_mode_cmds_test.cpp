// Unit test for the console's flight-mode commands (roadmap T8). Three regressions are locked in:
//
//  1. "fmode" / "hspeed" with no argument must not index a[0] on an empty vector (they did before
//     T8; that is UB reachable from the in-flight console by a single stray Enter).
//  2. "fmode <name>" must put a SoftSwitchMode gear frame on the wire and NOTHING else — never
//     the old FLYC 0x03 param write that used to fake Sport by widening the Normal block's tilt.
//  3. "hspeed <m/s>" must stay a plain FLYC param write and never emit a gear frame, so speed and
//     mode selection cannot be re-entangled by a later edit.
//
// Also covers the "smid" candidate selector added with T8 and its validator, since a bad cmd_id
// must not reach the control path. Hardware-free and display-free: a capture transport stands in
// for the link, and Client's constructor starts no threads.
#include "core/client.hpp"
#include "core/drone.hpp"
#include "core/duml.hpp"
#include "core/flight_mode.hpp"
#include "core/transport.hpp"

#include <cstdint>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

using namespace djilink;

namespace {
class CaptureTransport final : public Transport {
public:
    void send(const Bytes& data) override {
        frames.push_back(data);
    }
    // No telemetry feed: the console commands under test never read one, and returning nothing
    // keeps the test instant (nothing here polls or sleeps).
    Bytes recv(int) override {
        return {};
    }
    std::vector<Bytes> frames;
};

void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

DumlPacket decode_last(const CaptureTransport& t, const char* what) {
    require(!t.frames.empty(), what);
    const std::optional<DumlPacket> pkt = DumlPacket::decode(t.frames.back());
    require(pkt.has_value(), what);
    return *pkt;
}

bool contains(const std::string& haystack, const char* needle) {
    return haystack.find(needle) != std::string::npos;
}

// Gear wire value carried by a SoftSwitchMode frame. Checks the payload really is the 32-bit word
// the contract promises before indexing it, so a shrunk payload fails the test instead of reading
// out of bounds.
std::uint8_t gear_wire(const DumlPacket& pkt) {
    require(pkt.payload.size() >= 4, "gear frame carries a 32-bit payload");
    return pkt.payload[0];
}
} // namespace

int main() {
    try {
        auto owned = std::make_unique<CaptureTransport>();
        CaptureTransport* wire = owned.get();
        Client cli(std::move(owned), "sim", /*live=*/false);
        Drone& d = cli.drone();
        // Plaintext, like the console client: cmd() would otherwise SIMPLE-encrypt the FLYC 0x03
        // param frame and the speed check below could not decode it.
        d.encrypt_config = false;

        // --- 1. bare "fmode" reports instead of crashing -------------------------------------
        std::size_t before = wire->frames.size();
        run_console_cmd(cli, "fmode");
        require(wire->frames.size() == before, "bare fmode sends nothing");
        require(contains(cli.msg(), "usage: fmode"), "bare fmode prints usage");
        // No telemetry has arrived, so the derived mode is unknown — and it must say so rather
        // than claim the mode we last commanded.
        require(contains(cli.msg(), "now: ?"), "bare fmode reports the derived mode as unknown");

        // --- 2. "fmode sport" emits exactly one gear frame -----------------------------------
        before = wire->frames.size();
        run_console_cmd(cli, "fmode sport");
        require(wire->frames.size() == before + 1, "fmode sends one frame");
        DumlPacket pkt = decode_last(*wire, "fmode frame did not decode");
        require(pkt.cmd_set == kRcCmdSet, "fmode uses the RC cmd_set");
        require(pkt.receiver == kRcReceiver, "fmode targets the RC");
        require(pkt.sender == DEV_APP, "fmode speaks as the mobile app");
        require(!(pkt.cmd_set == 0x03 && pkt.cmd_id == 0xF9), "fmode is not a FLYC param write");
        require(gear_wire(pkt) == soft_switch_wire_value(RcSoftSwitchMode::Sport),
                "fmode sport selects the Sport gear");
        require(contains(cli.msg(), "SoftSwitchMode"), "fmode names the mechanism it used");

        // --- 3. an unknown mode name is rejected, silently on the wire ------------------------
        before = wire->frames.size();
        run_console_cmd(cli, "fmode max");
        require(wire->frames.size() == before, "an unknown mode name sends nothing");
        require(contains(cli.msg(), "unknown flight mode"), "an unknown mode name is reported");

        // --- 4. bare "hspeed" reports instead of crashing ------------------------------------
        before = wire->frames.size();
        run_console_cmd(cli, "hspeed");
        require(wire->frames.size() == before, "bare hspeed sends nothing");
        require(contains(cli.msg(), "usage: hspeed"), "bare hspeed prints usage");

        // --- 5. "hspeed 10" is a speed write, not a mode switch ------------------------------
        before = wire->frames.size();
        run_console_cmd(cli, "hspeed 10");
        require(wire->frames.size() == before + 1, "hspeed sends one frame");
        pkt = decode_last(*wire, "hspeed frame did not decode");
        require(pkt.cmd_set == 0x03 && pkt.cmd_id == 0xF9, "hspeed writes a FLYC param");
        require(pkt.cmd_set != kRcCmdSet, "hspeed never emits a gear frame");

        // --- 6. "smid" shows, validates and actually re-targets the gear frame ---------------
        before = wire->frames.size();
        run_console_cmd(cli, "smid");
        require(wire->frames.size() == before, "bare smid sends nothing");
        require(contains(cli.msg(), "0x06"), "bare smid shows the current cmd_id");

        run_console_cmd(cli, "smid 0x19");
        require(d.soft_switch_cmd_id() == SoftSwitchCmdId::SetControllerMode, "smid sets a cmd_id");
        run_console_cmd(cli, "fmode normal");
        pkt = decode_last(*wire, "fmode after smid did not decode");
        require(pkt.cmd_id == static_cast<std::uint8_t>(SoftSwitchCmdId::SetControllerMode),
                "the selected cmd_id reaches the wire");
        require(gear_wire(pkt) == soft_switch_wire_value(RcSoftSwitchMode::Position),
                "fmode normal selects the Position gear");

        before = wire->frames.size();
        run_console_cmd(cli, "smid 0x07");
        require(d.soft_switch_cmd_id() == SoftSwitchCmdId::SetControllerMode,
                "a bogus cmd_id leaves the previous selection alone");
        require(wire->frames.size() == before, "a bogus cmd_id sends nothing");
        require(contains(cli.msg(), "unknown cmd_id"), "a bogus cmd_id is reported");

        // --- 7. the validator behind "smid" --------------------------------------------------
        require(soft_switch_cmd_id_from(0x06) == SoftSwitchCmdId::SetMachineMode, "0x06 parses");
        require(soft_switch_cmd_id_from(0x11) == SoftSwitchCmdId::SetFunctionSwitch, "0x11 parses");
        require(soft_switch_cmd_id_from(0x19) == SoftSwitchCmdId::SetControllerMode, "0x19 parses");
        for (const unsigned bad : {0x00u, 0x05u, 0x07u, 0x10u, 0x1Au, 0xFFu})
            require(!soft_switch_cmd_id_from(bad), "a non-candidate cmd_id is rejected");

        // --- 8. no console command ever writes the old mode-emulation parameter --------------
        // The tilt parameter may only be written by the speed path (case 5); a mode command that
        // touches it would be the deprecated emulation coming back.
        before = wire->frames.size();
        run_console_cmd(cli, "fmode cine");
        require(wire->frames.size() == before + 1, "fmode cine sends one frame");
        pkt = decode_last(*wire, "fmode cine frame did not decode");
        require(pkt.cmd_set == kRcCmdSet && pkt.cmd_id != 0xF9, "fmode cine stays a gear frame");
        require(gear_wire(pkt) == soft_switch_wire_value(RcSoftSwitchMode::Tripod),
                "fmode cine selects the Tripod gear");

        std::cout << "console_mode_cmds: OK\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "console_mode_cmds FAILED: " << e.what() << "\n";
        return 1;
    }
}
