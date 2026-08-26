# DJI Link Roadmap

This roadmap is intentionally small. The goal is not to expose every DJI command; it is to make the verified subset more reliable, easier to use, and easier to extend.

## Near term

- [ ] Expand portable tests around DUML, telemetry, composite transport, and Pi discovery.
- [ ] Improve connection diagnostics so failures point to the first broken layer (PC → Pi → RC → aircraft).
- [ ] Keep hardware/firmware compatibility reports current.

## 2.0.0 — Media + controllers

The 2.0 line is planned as the next major usability jump after the current flight-control foundation is stable. The goal is to make DJI Link feel less like a protocol project and more like a complete desktop ground station.

- [ ] Finish and harden the **media pipeline**: camera/media operations, state handling, downloads/transfers, and recovery from asynchronous transitions.
- [ ] Port more hardware-verified media operations from the Python research implementation to the native C++ client.
- [ ] Add **gamepad/controller input** alongside keyboard and mouse controls. The initial target is conventional **PS/Xbox-style controllers** with configurable mappings.
- [ ] Add controller profiles, dead-zone/sensitivity configuration, and a safe way to switch between keyboard/mouse and gamepad input.
- [ ] Improve cross-platform packaging and first-run UX.

## 3.0.0 — AI-assisted capabilities

The 3.0 line is intentionally experimental. The idea is to use AI where it can add capabilities that are difficult or impractical to reproduce manually, while keeping flight-critical behavior explicit, bounded, and user-controlled.

Possible directions include:

- [ ] Re-create useful capabilities found on newer DJI aircraft but absent from the Mavic Mini 1, where the hardware and available data make that feasible.
- [ ] Explore intelligent camera/media assistance and higher-level automation.
- [ ] Prototype novel capabilities that were never shipped on the Mini but could be implemented through the desktop/Pi architecture.
- [ ] Investigate AI-assisted scene understanding, flight assistance, diagnostics, and workflow automation.
- [ ] Keep experimental AI features separate from deterministic flight-control primitives and clearly label their maturity/safety status.

> **Note:** 2.0.0 and 3.0.0 are roadmap targets, not promises. Hardware limitations may rule out individual features, and research results will determine what actually ships.

## Research

- [ ] Increase coverage of the WM160 state machine and capability discovery.
- [ ] Reconcile historical reverse-engineering notes into fewer authoritative documents.
- [ ] Verify protocol behavior across additional firmware/application combinations where hardware is available.
- [ ] Document new findings with evidence level, capture context, and hardware verification status.

## Good first contributions

The most useful contributions do not require risky flight testing. Examples:

- simulator/UI improvements;
- documentation fixes;
- parser/unit tests;
- packaging fixes;
- telemetry display improvements;
- hardware compatibility reports;
- clean-room ports of already verified research notes.

For changes that affect flight control or undocumented protocol behavior, open an issue first and explain the proposed approach.
