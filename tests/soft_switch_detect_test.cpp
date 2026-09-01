// Unit test for the SoftSwitchMode cmd_id auto-detector state machine (roadmap T7). The detector is
// pure: all I/O (send a probe, read the derived mode, wait a tick) is injected via hooks, so a fake
// "drone + telemetry" lets us exercise every branch with no hardware, no threads and no clock.
//
// The fake models the one thing that matters: exactly one cmd_id (`working`) actually moves the FC
// block. apply() changes the reported mode to the probe's expected mode only when the candidate
// equals `working`; every other candidate is a no-op, mimicking a frame the FC ignores. observe()
// returns the current reported mode (optionally nullopt for a few ticks, to model a transient OSD).
#include "core/soft_switch_detect.hpp"

#include <iostream>
#include <optional>
#include <stdexcept>

using namespace djilink;

namespace {
void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}

struct Fake {
    std::optional<SoftSwitchCmdId>
        working; // the only cmd_id that switches the mode (nullopt = none)
    DerivedFlightMode mode = DerivedFlightMode::Normal; // what "telemetry" currently reports
    int suppress = 0; // observe() yields nullopt this many more times (transient OSD), then mode
    int sends = 0;    // apply() calls == probes issued
    int waits = 0;    // wait() calls == poll ticks
    FlightMode last_probe = FlightMode::Normal; // probe of the most recent apply()
};

// Build hooks over a Fake by reference. apply() only moves the mode for the working cmd_id; when it
// does, the mode becomes the probe's expected derived mode (what a real FC would report). observe()
// hands back nullopt while `suppress` counts down (a transient/not-yet-arrived OSD) then the mode.
SoftSwitchDetectHooks hooks_for(Fake& f) {
    SoftSwitchDetectHooks h;
    h.apply = [&f](SoftSwitchCmdId id, FlightMode probe) {
        ++f.sends;
        f.last_probe = probe;
        if (f.working && id == *f.working)
            f.mode = expected_derived_for(probe);
    };
    h.observe = [&f]() -> std::optional<DerivedFlightMode> {
        if (f.suppress > 0) {
            --f.suppress;
            return std::nullopt;
        }
        return f.mode;
    };
    h.wait = [&f]() { ++f.waits; };
    return h;
}
} // namespace

int main() {
    try {
        // --- pure helpers ---
        const auto cands = default_soft_switch_candidates();
        require(cands.size() == 3, "three candidates ship by default");
        require(cands[0] == SoftSwitchCmdId::SetMachineMode, "first candidate is SetMachineMode");
        require(cands[1] == SoftSwitchCmdId::SetFunctionSwitch, "second is SetFunctionSwitch");
        require(cands[2] == SoftSwitchCmdId::SetControllerMode, "third is SetControllerMode");
        require(expected_derived_for(FlightMode::Sport) == DerivedFlightMode::Sport,
                "Sport->Sport");
        require(expected_derived_for(FlightMode::Normal) == DerivedFlightMode::Normal,
                "Norm->Norm");
        require(expected_derived_for(FlightMode::Cine) == DerivedFlightMode::Tripod,
                "Cine->Tripod");
        // flight_mode_for is the inverse used to restore the pre-scan mode; Tripod folds into Cine.
        require(flight_mode_for(DerivedFlightMode::Sport) == FlightMode::Sport, "Sport<-Sport");
        require(flight_mode_for(DerivedFlightMode::Normal) == FlightMode::Normal, "Normal<-Normal");
        require(flight_mode_for(DerivedFlightMode::Cine) == FlightMode::Cine, "Cine<-Cine");
        require(flight_mode_for(DerivedFlightMode::Tripod) == FlightMode::Cine, "Cine<-Tripod");

        // --- case 1: the first candidate works -> found immediately, one probe sent ---
        {
            Fake f;
            f.working = SoftSwitchCmdId::SetMachineMode;
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(r.cmd_id == SoftSwitchCmdId::SetMachineMode, "case1: SetMachineMode detected");
            require(r.probes_sent == 1, "case1: exactly one probe");
            require(f.last_probe == FlightMode::Sport, "case1: probed Sport from a non-Sport base");
        }

        // --- case 2: only the third candidate works -> first two exhausted, then found ---
        {
            Fake f;
            f.working = SoftSwitchCmdId::SetControllerMode;
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(r.cmd_id == SoftSwitchCmdId::SetControllerMode, "case2: third candidate wins");
            // 2 attempts x 2 dead candidates + 1 winning attempt on the third = 5 probes.
            require(r.probes_sent == 5, "case2: five probes (2*2 dead + 1)");
        }

        // --- case 3: no candidate works -> nullopt, every attempt exhausted ---
        {
            Fake f;
            f.working = std::nullopt;
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(!r.cmd_id.has_value(), "case3: nothing detected");
            require(r.probes_sent == 6, "case3: 3 candidates x 2 attempts = 6 probes");
        }

        // --- case 4: baseline already Sport -> probe Normal so the transition is observable ---
        {
            Fake f;
            f.working = SoftSwitchCmdId::SetMachineMode;
            f.mode = DerivedFlightMode::Sport; // pretend the drone starts in Sport
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(r.cmd_id == SoftSwitchCmdId::SetMachineMode, "case4: detected from Sport base");
            require(f.last_probe == FlightMode::Normal, "case4: probed Normal, not Sport");
        }

        // --- case 5: transient OSD (nullopt for a few polls) must not condemn a working cmd_id ---
        {
            Fake f;
            f.working = SoftSwitchCmdId::SetMachineMode;
            f.suppress = 3; // first three observes after entry report nullopt
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(r.cmd_id == SoftSwitchCmdId::SetMachineMode,
                    "case5: survives transient nullopt");
            require(r.probes_sent == 1, "case5: still the first candidate, one probe");
        }

        // --- case 6: only the MIDDLE candidate works -> proves the candidate transition (no
        // off-by-one between candidates), first exhausted then found on the second ---
        {
            Fake f;
            f.working = SoftSwitchCmdId::SetFunctionSwitch;
            const auto r = detect_soft_switch_cmd_id(SoftSwitchDetectConfig{}, hooks_for(f));
            require(r.cmd_id == SoftSwitchCmdId::SetFunctionSwitch, "case6: middle candidate wins");
            // 2 attempts x 1 dead candidate + 1 winning attempt on the second = 3 probes.
            require(r.probes_sent == 3, "case6: three probes (2 dead + 1)");
        }

        // --- case 7: config override -> a narrowed candidate list is the roadmap's "force a
        // specific cmd_id". The detector must try only what it is given, in that order. ---
        {
            SoftSwitchDetectConfig cfg;
            cfg.candidates = {SoftSwitchCmdId::SetFunctionSwitch};
            Fake win;
            win.working = SoftSwitchCmdId::SetFunctionSwitch;
            const auto r_win = detect_soft_switch_cmd_id(cfg, hooks_for(win));
            require(r_win.cmd_id == SoftSwitchCmdId::SetFunctionSwitch,
                    "case7: single candidate hit");
            require(r_win.probes_sent == 1, "case7: one probe when the only candidate works");
            Fake miss; // the overridden candidate is not the working one -> exhausted, nullopt
            miss.working = SoftSwitchCmdId::SetMachineMode;
            const auto r_miss = detect_soft_switch_cmd_id(cfg, hooks_for(miss));
            require(!r_miss.cmd_id.has_value(), "case7: no other candidate is ever tried");
            require(r_miss.probes_sent == 2, "case7: only the single candidate's 2 attempts run");
        }

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
