# Flight-Mode Switching — Implementation Roadmap

Scope: fix flight-mode **selection, switching, and display** (Cine / Normal / Sport) on the
WM160. Tracked on branch `feature/flight-mode-switch`. The overall goal statement lives in
`CLAUDE.md` ("Current goal"); this file is the task breakdown. The repo-wide `ROADMAP.md`
is unrelated work and is not touched here.

## Root cause (already reverse-engineered)

Flight mode is **not** a writable FC parameter. It is the choice of which pre-loaded FC
config block is active (`mode_normal_cfg` = Position, `mode_sport_cfg` = Sport,
`mode_gentle_cfg` = CineSmooth, `mode_tripod_cfg` = Tripod), selected by the RC **gear
channel**. DJI Fly emulates that gear on the Mini with the KeyValue key
`RemoteController/SoftSwitchMode` (enum `POSITION / SPORT / TRIPOD`) routed to the RC
component (**cmdset `0x06`**) — not a FLYC `0x03` write.

Current code (`Drone::set_flight_mode`, `src/core/drone.cpp:196`) instead overwrites one
Normal-block tilt parameter (`g_config.mode_normal_cfg.tilt_atti_range_0`). That is a
"sped-up Normal", never a real block switch → Normal→Sport does not work.

## Chosen approach (owner decisions)

- **Protocol:** build against the three candidate cmd_ids `0x06/0x06`, `0x06/0x19`,
  `0x06/0x11`, selectable by config, with **auto-detection from the OSD `FLYC_STATE`
  response**. The winning cmd_id is confirmed only on real hardware by the owner.
- **Scope:** three user modes (Cine / Normal / Sport) **+ HUD** shows the real current
  mode derived from live telemetry, not the gear channel.
- **Verification:** a task is "done" when it passes `--sim`, ships unit tests, and clears
  subagent code review. On-drone verification is the owner's; each PR carries a hardware
  checklist where relevant.
- **Git flow:** each task = a sub-branch off `feature/flight-mode-switch` → PR **into**
  `feature/flight-mode-switch` (after subagent review). Final `feature/flight-mode-switch`
  → `main` merge is done by the owner after on-drone verification.

## Signals we already have

`FLYC_STATE` (OSD-common byte `@0x1e`, parsed in `telemetry.cpp:160`) distinguishes user
modes: `SPORT=31`, `Cinematic=19`, `TRIPOD_GPS=38`; ordinary GPS flight (`GPS_Atti=6`,
`Hover=4`, …) = Normal. This is both the HUD source (T4) and the auto-detect feedback (T7).

## Task breakdown

Each task is small, single-purpose, and reviewed before its PR. Order respects dependencies.

- **T1 — Flight-mode model (core, pure).** New `FlightMode` `enum class {Cine, Normal, Sport}`
  + string↔enum + `FlightMode`→`RcSoftSwitchMode` mapping. No I/O.
  Files: `src/core/flight_mode.{hpp,cpp}`, `tests/flight_mode_test.cpp`, CMake.
  Done: unit test green, clang-format, review. No hardware risk.

- **T2 — SoftSwitchMode frame encoder (core).** Build the DUML frame for the SoftSwitchMode
  key on cmdset `0x06`, parametrized by candidate cmd_id and `RcSoftSwitchMode` value;
  decide receiver address and payload layout (best-effort from the KeyValue reverse).
  Files: `flight_mode.*` / `drone.*`, tests. Done: deterministic-bytes unit test, review.

- **T3 — `Drone::set_flight_mode` via SoftSwitchMode.** Rewrite it to send the T2 frame
  instead of the tilt write; add a typed `set_flight_mode(FlightMode)` and a configurable
  candidate cmd_id. Keep `set_horizontal_speed` (a *speed* setting) strictly separate.
  Files: `drone.{cpp,hpp}`, tests (fake transport captures the frame).
  Done: unit tests, `--sim` smoke, review.

- **T4 — Derive user mode from telemetry.** Add a derived user-mode from `FLYC_STATE`
  (31→Sport, 19→Cine, 38→Tripod, GPS/Atti/Hover→Normal; transient states keep last).
  Files: `telemetry.{hpp,cpp}`, tests. Done: mapping unit test, review. (Parallel to T2/T3.)

- **T5 — HUD + buttons.** HUD "MODE" shows the derived user mode (T4), raw `FLYC_STATE`
  secondary; Normal/Sport/Cine buttons call the typed API (T3).
  Files: `src/gui/gui.cpp`. Done: `--sim --windowed` visual check, review. Needs T3+T4.

- **T6 — Simulator responds to switching.** The sim transport changes the `FLYC_STATE` it
  reports when it sees a SoftSwitchMode frame, so `--sim` demonstrates switching and T7
  terminates. Files: `transport.cpp` (sim path). Done: `--sim` shows the change, review.

- **T7 — Auto-detect the working cmd_id.** State machine: send candidate → watch
  `FLYC_STATE` for the expected transition within a timeout → else try the next candidate;
  remember the winner for the session; config override; bounded retries.
  Files: `drone`/`client`, tests (fake telemetry). Done: state-machine unit test, `--sim`,
  review. Real cmd_id confirmed on hardware only. Needs T4+T6.

- **T8 — Remove the deprecated path.** Delete/rename the old tilt-based emulation; fix the
  `client.cpp` command aliases (`fmode`/`flightmode` → new API; keep `hspeed`/`speed` as
  speed only); update comments. Files: `client.cpp`, `drone.cpp`. Done: build, tests, review.

- **T9 — Docs + hardware checklist.** Record the implemented mechanism in `reverse_docs`;
  add an on-drone checklist (winning cmd_id, expected `FLYC_STATE` per mode, Cine↔TRIPOD
  confirmation, gating: `NoviceModeEnabled=false`, `MultipleFlightModeEnabled`, GPS fix).
  Files: `dji_link_beta/reverse_docs/*`, this file. Done: review.

## Open unknowns (to confirm on hardware)

- Exact SoftSwitchMode cmd_id among the three candidates, and the exact payload byte layout.
- Receiver address for the RC frame (`drone.hpp` currently defines `DEV_RC = 0x02`, i.e. the
  app address; the RC device may be `0x06`). T2 must make this explicit.
- Cine↔`SoftSwitchMode` value: Cine is likely `TRIPOD` (gentle/CineSmooth), to be verified.
- Behaviour while virtual sticks are active: FC goes to `FLYC_STATE 17 (Joystick)`, where the
  gear does not apply — document and handle in T3/T7.
