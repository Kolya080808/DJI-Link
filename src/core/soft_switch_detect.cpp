#include "core/soft_switch_detect.hpp"

#include <optional>
#include <vector>

namespace djilink {

std::vector<SoftSwitchCmdId> default_soft_switch_candidates() {
    return {SoftSwitchCmdId::SetMachineMode, SoftSwitchCmdId::SetFunctionSwitch,
            SoftSwitchCmdId::SetControllerMode};
}

DerivedFlightMode expected_derived_for(FlightMode probe) {
    switch (probe) {
        case FlightMode::Sport:
            return DerivedFlightMode::Sport;
        case FlightMode::Normal:
            return DerivedFlightMode::Normal;
        case FlightMode::Cine:
            // Cine is delivered through the Tripod gear on the WM160, so it reports TRIPOD_GPS ->
            // Tripod (the open Cine<->Tripod question). This branch exists only for totality; Cine
            // is never used as a probe precisely because its outcome is the unconfirmed one.
            return DerivedFlightMode::Tripod;
    }
    return DerivedFlightMode::Normal; // unreachable: the switch is exhaustive over the enum
}

FlightMode flight_mode_for(DerivedFlightMode mode) {
    switch (mode) {
        case DerivedFlightMode::Sport:
            return FlightMode::Sport;
        case DerivedFlightMode::Normal:
            return FlightMode::Normal;
        case DerivedFlightMode::Cine:
        case DerivedFlightMode::Tripod:
            // Cine's gear (soft_switch_for(Cine) == Tripod) is what the WM160 reports as
            // TRIPOD_GPS, so both derived modes are reproduced by sending FlightMode::Cine.
            return FlightMode::Cine;
    }
    return FlightMode::Normal; // unreachable: the switch is exhaustive over the enum
}

namespace {
// Choose a probe whose success is observable: its expected derived mode must differ from where the
// drone already is, so seeing that mode after the send proves the switch took effect rather than
// reflecting the pre-existing state. Sport (31) and Normal (6) are the two most distinct codes; if
// the drone is already in Sport we probe Normal, otherwise we probe Sport. A nullopt baseline (no
// telemetry yet) is treated as "not Sport", so we probe Sport.
FlightMode choose_probe(std::optional<DerivedFlightMode> baseline) {
    return baseline == DerivedFlightMode::Sport ? FlightMode::Normal : FlightMode::Sport;
}
} // namespace

SoftSwitchDetectResult detect_soft_switch_cmd_id(const SoftSwitchDetectConfig& cfg,
                                                 const SoftSwitchDetectHooks& hooks) {
    SoftSwitchDetectResult result;
    const FlightMode probe = choose_probe(hooks.observe());
    const DerivedFlightMode expected = expected_derived_for(probe);

    for (const SoftSwitchCmdId candidate : cfg.candidates) {
        for (int attempt = 0; attempt < cfg.attempts_per_candidate; ++attempt) {
            hooks.apply(candidate, probe);
            ++result.probes_sent;
            for (int poll = 0; poll < cfg.polls_per_attempt; ++poll) {
                hooks.wait();
                if (hooks.observe() == expected) {
                    result.cmd_id = candidate; // this cmd_id actually moved the FC block
                    return result;
                }
            }
        }
    }
    return result; // cmd_id stays nullopt: no candidate produced the expected transition
}

} // namespace djilink
