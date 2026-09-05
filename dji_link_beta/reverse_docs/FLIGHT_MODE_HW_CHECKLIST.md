# On-drone checklist — flight-mode switching (WM160)

Purpose: settle the four open unknowns of `FLIGHT_MODE_SOFTSWITCH_2026.md` on real hardware —
**which cmd_id the RC accepts**, the **`FLYC_STATE` each mode reports**, whether **Cine is
Tripod**, and what **gates** a gear frame. Everything up to step 8 is done **on the ground with
the propellers off**; step 9 is the only flying step and is optional.

Run the whole thing in `--sim` first (`./dji_link --sim`, or `python3 dji_link_beta/pc_client.py
--sim`) so you know what a passing line looks like before the aircraft is powered.

## 0. Before you plug anything in

- [ ] **Propellers off** for steps 1–8. A gear frame is not a motor command, but an unknown cmd_id
      on the control path is exactly what we are testing.
- [ ] Aircraft activated, battery > 50 %, outdoors with sky view (Normal/Sport need a GPS fix).
- [ ] **DJI Fly closed** and not holding the link.
- [ ] Start the client with logging on (`--verbose`) and keep the log — it is the deliverable.
- [ ] Sanity: `python3 dji_link_beta/test_flight_mode.py` green (84 checks) and `ctest` green on
      the build you are about to fly.

## 1. Baseline (write it down before touching anything)

| reading | where | value |
|---|---|---|
| `FLYC_STATE` (raw) | HUD secondary / `tele` | |
| MODE (derived) | HUD "MODE" | |
| satellites / GPS level | HUD | |
| active cmd_id | `smid` with no argument | |

`smid` with no argument prints the current cmd_id and the candidate list; it sends nothing.

## 2. Gating — read, do not write

Read each and record it. All are `0x03/0xF8` reads; none of them changes state:

- [ ] `rp g_config.novice_cfg.novice_func_enabled_0` → must be **0**. Novice/Beginner mode forces
      GPS and caps height/radius; expect it to block Sport. (hash `0xde9b1b7b`, u8)
- [ ] `rp gpsenable` (`g_config.gps_cfg.gps_enable_0`) → **1**, and a real fix in the HUD.
- [ ] `rp g_config.gear_cfg.gear_func_en_0` (`0x5f6a490c`), `…gear_cfg.auto_control_enable_0`
      (`0x5d45e217`), `…gear_cfg.hide_gear_en_0` (`0x58e51319`) → the live WM160 dump has all
      three at **0**. If a gear frame is rejected in step 3, these are the first suspects: note
      the values, do **not** flip them yet, and report them.
- [ ] `rp tilt` (`mode_normal_cfg.tilt_atti_range_0`) → expected **20.0**. This is the *speed*
      setting; it is recorded here only so step 8 can prove `fmode` never touches it.

There is **no** `MultipleFlightModeEnabled` parameter in the WM160 live table or in the app dump
(the roadmap listed one as a guess). If a gear frame is refused while novice is off and GPS is
locked, the `gear_cfg.*` trio above is the thing to investigate, not that name.

## 3. Which cmd_id does the RC accept?

- [ ] Sticks released, **no** virtual-stick authority (`control off`).
- [ ] Run `detectmode`. It probes `0x06`, `0x11`, `0x19` (2 attempts × 8 polls × 150 ms each, so
      up to ~7 s in total, blocking) and returns the aircraft to the mode it started in — on
      success it also re-sends that baseline mode through the winning cmd_id, so it never leaves
      the aircraft in the Sport probe.
      - `flight-mode cmd_id detected: 0x__` → record it: `cmd_id = ______`. Skip to step 4.
      - `flight-mode cmd_id auto-detect failed (no candidate switched the mode)` → the previous
        cmd_id is restored; do the manual sweep below. A silent failure is still information.
- [ ] Manual sweep (only if `detectmode` found nothing) — for each of `0x06`, `0x11`, `0x19`:

  ```
  smid 0x06          # select the candidate
  fmode sport        # send the gear frame
  # wait 2 s, watch the raw FLYC_STATE, repeat twice
  fmode normal       # put it back before trying the next candidate
  ```

  | cmd_id | `FLYC_STATE` after `fmode sport` | MODE shown | verdict |
  |---|---|---|---|
  | `0x06` | | | |
  | `0x11` | | | |
  | `0x19` | | | |

- [ ] If all three do nothing: capture the frame the RC *does* accept (run DJI Fly, switch P→S,
      log the link) — the payload wrapper, not just the cmd_id, may be wrong (§2 of the mechanism
      doc). End the session here and report.

## 4. `FLYC_STATE` per mode (with the winning cmd_id selected)

Wait ~1 s after each command — the derived MODE only updates on the next OSD push, and it is
sticky (a transient state keeps the previous value).

| command | expected `FLYC_STATE` | actual | MODE shown |
|---|---|---|---|
| `fmode normal` | 1–8 / 23 / 32 (GPS_Atti 6, Hover 4 …) | | |
| `fmode sport` | 31 (SPORT) | | |
| `fmode cine` | 19 (Cinematic) **or** 38 (TRIPOD_GPS) | | |
| `fmode normal` | back to the GPS set | | |

- [ ] Switch Normal→Sport→Normal three times in a row: it must work every time, not only the
      first (a one-shot success means the FC latched something else).
- [ ] **Do not read a mode without a GPS fix.** Sport/Cine degrade into the Atti codes when the fix
      drops, and those resolve to Normal — the HUD will say "Normal" while the FC is still in the
      Sport block. If the fix is marginal, note it next to every reading above.

## 5. Is Cine the Tripod gear?

- [ ] `fmode cine` reported **19** → Cine has its own gear/state; the Cine↔Tripod mapping in
      `flight_mode.*` needs a separate Tripod value, and 38 must stay distinct.
- [ ] `fmode cine` reported **38** → Cine is delivered by the Tripod gear on the WM160 (the
      hypothesis). Then `derived_user_mode` may fold 38 → Cine, and the HUD should say "Cine".
- [ ] Either way, note whether the aircraft *behaves* slow/smooth (CineSmooth) in that mode, since
      `mode_gentle_cfg` and `mode_tripod_cfg` are different blocks.

## 6. Virtual sticks

- [ ] `control on` → confirm `FLYC_STATE 17` (Joystick).
- [ ] `fmode sport` while authority is open → expected: **no** mode change (gear does not apply).
      Record what actually happens.
- [ ] `control off`, wait for a GPS state, `fmode sport` again → must switch.
- [ ] `fmode sport` first, then `control on`: does the Sport block stay active while flying on
      joystick input? Record the `FLYC_STATE`/behaviour — this is what a scripted Sport flight
      depends on.

## 7. Does the mode survive?

- [ ] Set Sport, disconnect and reconnect the client → what does MODE read? (The gear is a runtime
      selection, not a persisted param; expect the aircraft to keep it until power-cycle.)
- [ ] Power-cycle the aircraft → expected back to Normal. Confirm.

## 8. Separation from the speed setting (regression guard)

- [ ] `rp tilt` → still **20.0** after all the `fmode` traffic (a mode switch must never write it).
- [ ] `hspeed 10` → `rp tilt` changes (≈25°), and MODE / `FLYC_STATE` do **not** change.
- [ ] Restore: `hspeed 8` → `rp tilt` back to 20.0. This param is RW+EE and self-persists.

## 9. In flight (optional, only if 1–8 are clean)

Open area, VLOS, RTH altitude sane, home point recorded, novice off.

- [ ] Hover in Normal, full forward pitch, read steady ground speed from the HUD (OSD `vx/vy`).
- [ ] `fmode sport`, repeat → the speed ceiling must be visibly higher (≈8 → ≈13 m/s).
- [ ] `fmode normal` before landing, and confirm MODE reads Normal.

**Safety.** Sport relaxes the FC's own limits: braking distance grows and obstacle behaviour
changes. Do not use it to work around geofencing or an unlocked zone, and keep the first switch
high enough and far enough from anything to be uneventful. Nothing in this repo bypasses the
aircraft's safety checks — if a mode is refused, that refusal is the answer, not an obstacle.

## What to report back

1. The winning cmd_id (or "none of the three").
2. The filled tables from steps 3, 4 and the values from step 2.
3. Cine's real `FLYC_STATE` (19 vs 38) and whether it felt like CineSmooth.
4. Virtual-stick behaviour from step 6.
5. The `--verbose` log.

Items 1 and 3 are what turn `FLIGHT_MODE_SOFTSWITCH_2026.md` §6 from open unknowns into facts and
let the code drop the candidate list.
