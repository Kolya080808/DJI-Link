# Flight-Mode Switching — Implementation Roadmap

Scope: fix flight-mode **selection, switching, and display** (Cine / Normal / Sport) on the
WM160. Tracked on branch `feature/flight-mode-switch`. The overall goal statement lives in
`CLAUDE.md` ("Current goal"); this file is the task breakdown. The repo-wide `ROADMAP.md`
is unrelated work and is not touched here.

**Status 2026-09-05: T1–T9 implemented, reviewed and merged into `feature/flight-mode-switch`.**
The C++ client and the Python beta both switch modes with the SoftSwitchMode gear frame and derive
the shown mode from live `FLYC_STATE`. What is left is **hardware confirmation by the owner** —
which of the three candidate cmd_ids the RC accepts, and Cine's real `FLYC_STATE`. Procedure:
`dji_link_beta/reverse_docs/FLIGHT_MODE_HW_CHECKLIST.md`; mechanism as built:
`dji_link_beta/reverse_docs/FLIGHT_MODE_SOFTSWITCH_2026.md`. `feature/flight-mode-switch` → `main`
stays the owner's merge, after that flight.

No-hardware verification of the whole feature, one command:
`python3 dji_link_beta/test_flight_mode.py` (84 checks) — plus `ctest --test-dir build` for the C++ side.

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

- **[done] T1 — Flight-mode model (core, pure).** _`FlightMode` + `RcSoftSwitchMode` + name parsing in `src/core/flight_mode.*`._ New `FlightMode` `enum class {Cine, Normal, Sport}`
  + string↔enum + `FlightMode`→`RcSoftSwitchMode` mapping. No I/O.
  Files: `src/core/flight_mode.{hpp,cpp}`, `tests/flight_mode_test.cpp`, CMake.
  Done: unit test green, clang-format, review. No hardware risk.

- **[done] T2 — SoftSwitchMode frame encoder (core).** _`make_soft_switch_packet()`; receiver settled at `0x06`, payload = u32 LE gear (SPORT=0/POSITION=1/TRIPOD=2)._ Build the DUML frame for the SoftSwitchMode
  key on cmdset `0x06`, parametrized by candidate cmd_id and `RcSoftSwitchMode` value;
  decide receiver address and payload layout (best-effort from the KeyValue reverse).
  Files: `flight_mode.*` / `drone.*`, tests. Done: deterministic-bytes unit test, review.

- **[done] T3 — `Drone::set_flight_mode` via SoftSwitchMode.** _typed API + `set_soft_switch_cmd_id()`; `DEV_RC` corrected 0x02 → 0x06; `set_horizontal_speed` left as a speed-only setting._ Rewrite it to send the T2 frame
  instead of the tilt write; add a typed `set_flight_mode(FlightMode)` and a configurable
  candidate cmd_id. Keep `set_horizontal_speed` (a *speed* setting) strictly separate.
  Files: `drone.{cpp,hpp}`, tests (fake transport captures the frame).
  Done: unit tests, `--sim` smoke, review.

- **[done] T4 — Derive user mode from telemetry.** _`derived_user_mode()` + sticky `OsdState::user_mode`._ Add a derived user-mode from `FLYC_STATE`
  (31→Sport, 19→Cine, 38→Tripod, GPS/Atti/Hover→Normal; transient states keep last).
  Files: `telemetry.{hpp,cpp}`, tests. Done: mapping unit test, review. (Parallel to T2/T3.)

- **[done] T5 — HUD + buttons.** _HUD MODE = derived mode, raw `FLYC_STATE` secondary; buttons highlight the live mode._ HUD "MODE" shows the derived user mode (T4), raw `FLYC_STATE`
  secondary; Normal/Sport/Cine buttons call the typed API (T3).
  Files: `src/gui/gui.cpp`. Done: `--sim --windowed` visual check, review. Needs T3+T4.

- **[done] T6 — Simulator responds to switching.** _sim transport answers a gear frame with a new `FLYC_STATE` (C++ and Python)._ The sim transport changes the `FLYC_STATE` it
  reports when it sees a SoftSwitchMode frame, so `--sim` demonstrates switching and T7
  terminates. Files: `transport.cpp` (sim path). Done: `--sim` shows the change, review.

- **[done] T7 — Auto-detect the working cmd_id.** _pure bounded state machine + `Client::auto_detect_mode_cmd_id` wrapper that restores cmd_id on failure and the baseline mode on success._ State machine: send candidate → watch
  `FLYC_STATE` for the expected transition within a timeout → else try the next candidate;
  remember the winner for the session; config override; bounded retries.
  Files: `drone`/`client`, tests (fake telemetry). Done: state-machine unit test, `--sim`,
  review. Real cmd_id confirmed on hardware only. Needs T4+T6.

- **[done] T8 — Remove the deprecated path.** _tilt-based emulation gone; `fmode`/`smid`/`detectmode` vs `hspeed` separated in the console._ Delete/rename the old tilt-based emulation; fix the
  `client.cpp` command aliases (`fmode`/`flightmode` → new API; keep `hspeed`/`speed` as
  speed only); update comments. Files: `client.cpp`, `drone.cpp`. Done: build, tests, review.

- **[done] T9 — Docs + hardware checklist.** _`FLIGHT_MODE_SOFTSWITCH_2026.md` + `FLIGHT_MODE_HW_CHECKLIST.md`; the old speed-research doc is re-scoped to speed only._ Record the implemented mechanism in `reverse_docs`;
  add an on-drone checklist (winning cmd_id, expected `FLYC_STATE` per mode, Cine↔TRIPOD
  confirmation, gating: `NoviceModeEnabled=false`, `MultipleFlightModeEnabled`, GPS fix).
  Files: `dji_link_beta/reverse_docs/*`, this file. Done: review.

## Open unknowns (to confirm on hardware)

Full procedure: `dji_link_beta/reverse_docs/FLIGHT_MODE_HW_CHECKLIST.md`.

- **Exact SoftSwitchMode cmd_id** among `0x06` / `0x11` / `0x19`, and whether the payload really is
  the gear value as one u32 LE. All three ship; `detectmode` finds the winner from `FLYC_STATE`.
- **Cine↔`SoftSwitchMode`:** asking for Cine sends the `TRIPOD` gear. If hardware answers
  `FLYC_STATE 19` (Cinematic), Cine needs its own gear value and 38 stays Tripod; if it answers 38,
  `derived_user_mode()` may fold 38 → Cine. Until then 19 and 38 are kept distinct on purpose.
- **Behaviour while virtual sticks are active:** the FC reports `FLYC_STATE 17 (Joystick)`, where
  the gear does not apply and the sticky user mode keeps its previous value. Switch with sticks
  released; confirm that a mode set beforehand survives opening joystick authority.
- **What gates a gear frame.** `novice_cfg.novice_func_enabled_0` must be 0 and a GPS fix is needed
  for Normal/Sport. The `MultipleFlightModeEnabled` setting this file assumed earlier **does not
  exist** on WM160 — no such param in the 132 live hashes and no such key in the app dump; the
  candidates for a refusal are the live `g_config.gear_cfg.*` trio (`gear_func_en_0 0x5f6a490c`,
  `auto_control_enable_0 0x5d45e217`, `hide_gear_en_0 0x58e51319`, all 0 in the WM160 dump).

Resolved along the way: the RC frame's **receiver address is `0x06`** (T2 made it explicit and
corrected `drone.hpp`'s `DEV_RC` from the app address `0x02`), and the gear **wire values are
SPORT=0 / POSITION=1 / TRIPOD=2** — firmware ordinals, not the enum declaration order, so both
implementations map explicitly instead of casting.
