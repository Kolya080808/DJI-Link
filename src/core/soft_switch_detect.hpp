// Auto-detect which SoftSwitchMode cmd_id actually drives the WM160 (roadmap T7).
//
// The exact SoftSwitchMode cmd_id is an open unknown: we ship three reverse-engineered candidates
// (flight_mode.hpp's SoftSwitchCmdId) and let the drone tell us which one works. This module is
// the decision logic only — a bounded state machine that sends a probe switch through each
// candidate and watches the derived user mode (from the live FLYC_STATE OSD, roadmap T4) for the
// expected transition. All I/O is injected via hooks so the state machine is pure and unit-tests
// against a fake telemetry source, with no hardware, no threads and no real clock. Client wires
// the hooks to Drone (send) and Telemetry (observe); see Client::auto_detect_mode_cmd_id.
#pragma once

#include "core/flight_mode.hpp"
#include "core/telemetry.hpp"

#include <functional>
#include <optional>
#include <vector>

namespace djilink {

// The three reverse-engineered cmd_id candidates in probe order; the first that switches the mode
// wins. Narrowing this list (e.g. to a single known-good value) is the "config override" the
// roadmap calls for — the detector simply tries fewer candidates.
std::vector<SoftSwitchCmdId> default_soft_switch_candidates();

// The derived user mode a successful switch to `probe` must produce, so the detector can tell a
// real block change from noise. Only Sport/Normal are ever used as probes (their FLYC_STATE codes,
// 31 and 6, are the most distinct); Cine is included for totality but maps to Tripod — the
// unconfirmed Cine<->Tripod equivalence — which is exactly why it makes a poor probe. Never throws.
DerivedFlightMode expected_derived_for(FlightMode probe);

// The FlightMode whose SoftSwitchMode gear reproduces a given derived user mode — the inverse of
// expected_derived_for, used to put the aircraft back where it started after a scan. Normal/Sport
// map straight through; both Cine and Tripod map to FlightMode::Cine, since Cine's gear is the one
// that produces TRIPOD_GPS on the WM160 (the open Cine<->Tripod question). Never throws.
FlightMode flight_mode_for(DerivedFlightMode mode);

struct SoftSwitchDetectConfig {
    // Candidates to try, in order. Defaults to all three; shorten it to force a specific cmd_id.
    std::vector<SoftSwitchCmdId> candidates = default_soft_switch_candidates();
    // Re-send the probe this many times per candidate before giving up on it — a dropped RC frame
    // or a slow FC should not condemn a cmd_id that actually works (bounded retries).
    int attempts_per_candidate = 2;
    // Observe the telemetry this many times after each send before declaring the attempt failed.
    int polls_per_attempt = 8;
};

// The I/O the detector needs, injected so the state machine stays pure and testable. Every hook
// must be set; detect_soft_switch_cmd_id does not check for null.
struct SoftSwitchDetectHooks {
    // Select `id` as the active cmd_id and send a mode switch to `probe`. Client wires this to
    // Drone::set_soft_switch_cmd_id followed by Drone::set_flight_mode.
    std::function<void(SoftSwitchCmdId id, FlightMode probe)> apply;
    // The current derived user mode, or nullopt while telemetry is transient or not yet arrived.
    std::function<std::optional<DerivedFlightMode>()> observe;
    // Wait one poll interval before the next observe: a real sleep in production, a no-op or a
    // fake-clock tick in tests. Keeping the clock out of the state machine is what makes it fast
    // and deterministic to unit-test.
    std::function<void()> wait;
};

struct SoftSwitchDetectResult {
    std::optional<SoftSwitchCmdId> cmd_id; // the winner, or nullopt if none produced the switch
    int probes_sent = 0;                   // candidate sends issued (diagnostics / test assertions)
};

// Bounded state machine. Reads the current mode once as a baseline and picks a probe whose expected
// FLYC_STATE differs from it (so an observed transition proves the command took effect rather than
// echoing where the drone already was). Then, for each candidate in order, it re-sends the probe up
// to attempts_per_candidate times and polls telemetry polls_per_attempt times after each send; the
// first candidate that reaches the expected mode wins and is returned. Returns nullopt once the
// candidate list is exhausted. Never throws (hooks are assumed set).
SoftSwitchDetectResult detect_soft_switch_cmd_id(const SoftSwitchDetectConfig& cfg,
                                                 const SoftSwitchDetectHooks& hooks);

} // namespace djilink
