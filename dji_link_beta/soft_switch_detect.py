"""
Auto-detect which SoftSwitchMode cmd_id actually drives the WM160.

The exact SoftSwitchMode cmd_id is an open unknown: three reverse-engineered candidates ship
(flight_mode.SoftSwitchCmdId) and the drone tells us which one works. This module holds the
decision logic only — a bounded state machine that sends a probe switch through each candidate
and watches the derived user mode (from the live FLYC_STATE OSD) for the expected transition.
All I/O is injected via hooks, so the state machine is pure and unit-tests against a fake
telemetry source with no hardware, no threads and no real clock. auto_detect_mode_cmd_id() at
the bottom is the thin wiring to a live Drone/Telemetry pair (the console `detectmode` command).

Port of src/core/soft_switch_detect.{hpp,cpp} + Client::auto_detect_mode_cmd_id.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import time

from flight_mode import FlightMode, SoftSwitchCmdId
from telemetry import DerivedFlightMode

# Probe order; the first candidate that switches the mode wins. Shortening this list (e.g. to a
# single known-good value) is the "config override": the detector simply tries fewer candidates.
DEFAULT_CANDIDATES = (SoftSwitchCmdId.SET_MACHINE_MODE,
                      SoftSwitchCmdId.SET_FUNCTION_SWITCH,
                      SoftSwitchCmdId.SET_CONTROLLER_MODE)

# The derived user mode a successful switch must produce, so the detector can tell a real block
# change from noise. Only Sport/Normal are ever used as probes (FLYC_STATE 31 and 6 are the most
# distinct codes); Cine is here for totality but maps to Tripod — the unconfirmed Cine<->Tripod
# equivalence — which is exactly why it makes a poor probe.
_EXPECTED_DERIVED = {
    FlightMode.SPORT: DerivedFlightMode.SPORT,
    FlightMode.NORMAL: DerivedFlightMode.NORMAL,
    FlightMode.CINE: DerivedFlightMode.TRIPOD,
}

# The inverse: which gear reproduces a given derived mode, used to put the aircraft back where it
# started after a scan. Both CINE and TRIPOD map to FlightMode.CINE, since Cine's gear is the one
# that produces TRIPOD_GPS on the WM160.
_MODE_FOR_DERIVED = {
    DerivedFlightMode.SPORT: FlightMode.SPORT,
    DerivedFlightMode.NORMAL: FlightMode.NORMAL,
    DerivedFlightMode.CINE: FlightMode.CINE,
    DerivedFlightMode.TRIPOD: FlightMode.CINE,
}


def expected_derived_for(probe: FlightMode) -> DerivedFlightMode:
    """The derived user mode a successful switch to `probe` must produce."""
    return _EXPECTED_DERIVED[probe]


def flight_mode_for(mode: DerivedFlightMode) -> FlightMode:
    """The FlightMode whose gear reproduces a given derived user mode."""
    return _MODE_FOR_DERIVED[mode]


@dataclass
class SoftSwitchDetectConfig:
    candidates: tuple[SoftSwitchCmdId, ...] = DEFAULT_CANDIDATES
    # Re-send the probe this many times per candidate before giving up on it: a dropped RC frame or
    # a slow FC should not condemn a cmd_id that actually works (bounded retries).
    attempts_per_candidate: int = 2
    # Observe the telemetry this many times after each send before declaring the attempt failed.
    polls_per_attempt: int = 8


@dataclass
class SoftSwitchDetectHooks:
    """The I/O the detector needs, injected so the state machine stays pure and testable.

    apply(id, probe)  — select `id` as the active cmd_id and send a mode switch to `probe`.
    observe()         — the current derived user mode, or None while telemetry is transient
                        or has not arrived yet.
    wait()            — wait one poll interval; a real sleep in production, a no-op in tests.
    """

    apply: Callable[[SoftSwitchCmdId, FlightMode], None]
    observe: Callable[[], DerivedFlightMode | None]
    wait: Callable[[], None]


@dataclass
class SoftSwitchDetectResult:
    cmd_id: SoftSwitchCmdId | None = None   # the winner, or None if no candidate switched the mode
    probes_sent: int = 0                    # candidate sends issued (diagnostics / assertions)


def _choose_probe(baseline: DerivedFlightMode | None) -> FlightMode:
    """Choose a probe whose success is observable.

    Its expected derived mode must differ from where the drone already is, so seeing that mode
    after the send proves the switch took effect rather than reflecting the pre-existing state.
    If the drone is already in Sport we probe Normal, otherwise Sport. A None baseline (no
    telemetry yet) counts as "not Sport", so we probe Sport.
    """
    return FlightMode.NORMAL if baseline is DerivedFlightMode.SPORT else FlightMode.SPORT


def detect_soft_switch_cmd_id(cfg: SoftSwitchDetectConfig,
                              hooks: SoftSwitchDetectHooks) -> SoftSwitchDetectResult:
    """Bounded scan: probe each candidate in order, first one that moves the FC block wins.

    Reads the current mode once as a baseline and picks a probe whose expected FLYC_STATE differs
    from it. Then, per candidate, re-sends the probe up to attempts_per_candidate times and polls
    telemetry polls_per_attempt times after each send. Returns a result whose cmd_id is None once
    the candidate list is exhausted.
    """
    result = SoftSwitchDetectResult()
    probe = _choose_probe(hooks.observe())
    expected = expected_derived_for(probe)

    for candidate in cfg.candidates:
        for _ in range(cfg.attempts_per_candidate):
            hooks.apply(candidate, probe)
            result.probes_sent += 1
            for _ in range(cfg.polls_per_attempt):
                hooks.wait()
                if hooks.observe() is expected:
                    result.cmd_id = candidate   # this cmd_id actually moved the FC block
                    return result
    return result


def auto_detect_mode_cmd_id(drone, telemetry, cfg: SoftSwitchDetectConfig | None = None,
                            poll_interval_s: float = 0.15, log=None) -> SoftSwitchCmdId | None:
    """Wire the pure detector to a live Drone/Telemetry pair (console `detectmode`).

    The scan mutates control-path state (it re-selects cmd_ids and switches gears), so both are
    snapshotted and restored: a FAILED scan must not leave a wrong cmd_id latched, and a
    SUCCESSFUL one must not silently leave the aircraft in the probe mode (Sport is the
    geofence-relaxed profile). Only Normal/Sport/Cine gear frames are ever sent — never
    takeoff/land/RTH. The sim streams an OSD push about every rx timeout and a real link is
    faster, so 150 ms x 8 polls gives each candidate ~1.2 s per attempt to reveal the transition
    without dragging the scan out.
    """
    original_cmd_id = drone.soft_switch_cmd_id
    baseline = telemetry.state.user_mode

    def apply(cmd_id: SoftSwitchCmdId, probe: FlightMode) -> None:
        drone.set_soft_switch_cmd_id(cmd_id)
        drone.set_flight_mode(probe)

    hooks = SoftSwitchDetectHooks(apply=apply,
                                  observe=lambda: telemetry.state.user_mode,
                                  wait=lambda: time.sleep(poll_interval_s))
    r = detect_soft_switch_cmd_id(cfg or SoftSwitchDetectConfig(), hooks)

    if r.cmd_id is not None:
        drone.set_soft_switch_cmd_id(r.cmd_id)   # lock the winner for the rest of the session
        # Put the aircraft back in the mode it started in (Normal when it was unknown, never
        # leaving it in the relaxed Sport probe), now using the confirmed cmd_id.
        drone.set_flight_mode(flight_mode_for(baseline or DerivedFlightMode.NORMAL))
        if log:
            log(f"flight-mode cmd_id detected: 0x{int(r.cmd_id):02X} ({r.probes_sent} probes)")
    else:
        drone.set_soft_switch_cmd_id(original_cmd_id)   # failed scan restores the prior selection
        if log:
            log(f"flight-mode cmd_id auto-detect failed after {r.probes_sent} probes "
                f"(no candidate switched the mode)")
    return r.cmd_id
