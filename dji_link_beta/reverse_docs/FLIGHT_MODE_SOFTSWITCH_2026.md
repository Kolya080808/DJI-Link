# Flight-Mode Switching (Cine / Normal / Sport) — WM160, SoftSwitchMode gear

Status 2026-09-05: **implemented in this repo** (roadmap `docs/FLIGHT_MODE_ROADMAP.md`, T1–T9),
**cmd_id still unconfirmed on hardware** — three candidates ship with an auto-detector.
On-drone procedure: `FLIGHT_MODE_HW_CHECKLIST.md`.

This supersedes the *mode-switch* verdict of `FLIGHT_MODE_SPEED_RESEARCH_2026.md` ("no way to
switch, rewrite the Normal preset"). That document stays authoritative for **max horizontal
speed** (`mode_normal_cfg.tilt_atti_range_0`), which is a separate setting and stays separate in
the code.

--------------------------------------------------------------------------------
## TL;DR — what the code does

Flight mode is **not** an FC parameter you write. The FC pre-stores one config block per mode
(`mode_normal_cfg` = Position, `mode_sport_cfg` = Sport, `mode_gentle_cfg` = CineSmooth,
`mode_tripod_cfg` = Tripod) and the **RC gear channel** picks which block is active. The
switchless Mini emulates that gear in software: DJI Fly sets the KeyValue key
`RemoteController/SoftSwitchMode` (`POSITION / SPORT / TRIPOD`), which leaves the app as an
**RC-component DUML frame on cmd_set `0x06`** — not a FLYC `0x03/0xF9` parameter write.

So we send the same gear frame ourselves, and we read the result back from the **live
`FLYC_STATE`** in OSD telemetry (not from the reported mode channel, which we do not drive).

| what | value |
|---|---|
| cmd_set | `0x06` (RC component) |
| cmd_id | `0x06` / `0x11` / `0x19` — three candidates, one is real (auto-detected) |
| sender | `0x02` (MOBILE APP — never `0x0a`, see the AssistantProtected note below) |
| receiver | `0x06` (RC device) |
| cmd_type | `0x40` (ACK requested), **plaintext** — RC frames are not SIMPLE-encrypted |
| payload | gear wire value as one u32 LE: **Sport = 0, Position = 1, Tripod = 2** |
| confirmation | OSD-common `FLYC_STATE` (byte `@0x1e`): Sport 31, Cinematic 19, Tripod 38, ordinary GPS = Normal |

--------------------------------------------------------------------------------
## 1) Mechanism

### 1a. Why it is not a parameter write
`CmdIdFlyc$CmdIdType` (129 entries) has no mode/gear/sport/cine setter, and
`DataFlycFunctionControl$FLYC_COMMAND` enumerates only takeoff/land/home/motor verbs. The mode
blocks themselves (`g_config.mode_*_cfg.*`) are *tunings*, not a selector: rewriting
`mode_normal_cfg.tilt_atti_range_0` gives a faster **Normal**, and `FLYC_STATE` never leaves the
GPS-flight codes — which is exactly the bug this roadmap fixed. The selector is
`g_config.control.control_mode[0..2]` (live on WM160: 12 / 8 / 7) fed from `COMMAND_GEAR`, and
that channel is driven by the RC, not by a param write.

### 1b. The app-side lever is the SoftSwitchMode key
DJI Fly's KeyValue layer carries `RemoteController/SoftSwitchMode` with exactly three values
(`RcSoftSwitchMode`: POSITION / SPORT / TRIPOD — `uav/sdk/keyvalue/value/remotecontroller/`), and
the RC component's cmd_set is `0x06`. The Mini has no physical gear switch, so this key *is* the
gear. Three RC cmd_ids in the dump can carry it — SetMachineMode `0x06`, SetFunctionSwitch `0x11`,
SetControllerMode `0x19` — and no capture in `reverse_docs/` pins down which one DJI Fly uses, so
the code keeps all three and probes them against live telemetry (§4).

### 1c. Sender address is a safety constraint, not a detail
The app talks as **MOBILE APP `0x02`**. Address `0x0a` (PC/Assistant) makes the FC treat the link
as DJI Assistant and lock the motors (AssistantProtected). The gear frame is built with the app's
own sender address for that reason; see `reverse_docs/TAKEOFF_UNLOCK.md`.

--------------------------------------------------------------------------------
## 2) Wire format — real frames from the shipped encoder

Byte layout: `55 | len_lo | ver<<2\|len_hi | crc8 | sender | receiver | seq_lo seq_hi | cmd_type |
cmd_set | cmd_id | payload… | crc16_lo crc16_hi`.

Below: `seq = 1`, `cmd_id = 0x06`, produced by `dji_link_beta/flight_mode.py`
`make_soft_switch_packet()` (the C++ `make_soft_switch_packet` emits the identical bytes — a
unit test in both languages asserts it):

```
mode   gear      wire  frame
normal POSITION  1     55 11 04 92 02 06 01 00 40 06 06 01 00 00 00 fd d8
sport  SPORT     0     55 11 04 92 02 06 01 00 40 06 06 00 00 00 00 46 c4
cine   TRIPOD    2     55 11 04 92 02 06 01 00 40 06 06 02 00 00 00 30 fd
```

Same gear (Sport), the three candidate cmd_ids:

```
0x06 SetMachineMode      55 11 04 92 02 06 01 00 40 06 06 00 00 00 00 46 c4
0x11 SetFunctionSwitch   55 11 04 92 02 06 01 00 40 06 11 00 00 00 00 da 40
0x19 SetControllerMode    55 11 04 92 02 06 01 00 40 06 19 00 00 00 00 fa 1a
```

**The wire values are not the enum declaration order.** Firmware ordinals are SPORT=0,
POSITION=1, TRIPOD=2; the C++/Python enums declare Position first. Both implementations go
through an explicit mapping (`soft_switch_wire_value`) and never cast the enum to a byte — a cast
would silently swap Sport and Normal, i.e. put the aircraft in Sport when the pilot asked for
Normal. Tests assert the mapping in both languages.

Payload is the wire value as a **single u32 LE** (`01 00 00 00`). This is best-effort from the
KeyValue reverse: the RC-DUML wrapper for this key was never captured, so the payload layout is
an open unknown alongside the cmd_id (§6).

--------------------------------------------------------------------------------
## 3) Reading the mode back — `FLYC_STATE`, not the mode channel

`DataOsdGetPushCommon.getModeChannel()` mirrors the **RC gear channel**, which on our link keeps
reading its default; it is not a confirmation. The usable signal is `getFlycState()` — OSD-common
push (`0x03/0x43`, byte `@0x1e & 0x7F`), parsed in `src/core/telemetry.cpp` and
`dji_link_beta/telemetry.py`:

| `FLYC_STATE` | name | derived user mode |
|---|---|---|
| 31 | SPORT | **Sport** |
| 19 | Cinematic | **Cine** |
| 38 | TRIPOD_GPS | **Tripod** (kept distinct from Cine until §6 is settled) |
| 1–8, 23, 32 | Atti, Atti_CL, Atti_Hover, Hover, GPS_Blake, GPS_Atti, GPS_CL, GPS_HomeLock, Atti_Limited, NOVICE | **Normal** |
| 17 | Joystick (virtual sticks active) | *no verdict* → keep last |
| anything else | Manual, AutoTakeoff, AutoLanding, GoHome, QuickShot, Pano, GPS_HotPoint, … | *no verdict* → keep last |

**Sticky keep-last:** only a decisive code overwrites the shown mode, so a mid-manoeuvre
transient does not blank the HUD. Consequence for testing: right after a switch the mode may
still show the previous value for one OSD push (~100–200 ms) — poll, do not read once.

**Virtual sticks:** while `0x03/0x8E` joystick authority is open the FC reports 17 and the gear
does not apply. Switch modes with sticks released, then re-open joystick authority.

**GPS loss reads as Normal, by design.** Sport and Cine degrade into the Atti codes when the fix
drops, so the derived mode falls back to Normal even though the pilot never switched. That is not
a bug in the derivation — Atti *is* a Normal-block flight — but it means a mode readback is only
meaningful with a fix. Stickiness protects against transient *action* states, not against a real
block change.

--------------------------------------------------------------------------------
## 4) cmd_id auto-detection (`detectmode`)

`src/core/soft_switch_detect.{hpp,cpp}` / `dji_link_beta/soft_switch_detect.py` hold a bounded
state machine, pure (all I/O injected as hooks: apply / observe / wait) so it unit-tests without
hardware:

```
for candidate in [0x06, 0x11, 0x19]:            # config may shorten this list = "force a cmd_id"
    for attempt in 1..attempts_per_candidate:   # default 2 — one dropped RC frame must not condemn a cmd_id
        apply(candidate, probe_mode)            # select cmd_id, send the gear frame
        for poll in 1..polls_per_attempt:       # default 8 × 150 ms ≈ 1.2 s per attempt
            if observe() == expected_derived_for(probe_mode): return candidate
```

Probe uses Sport/Normal, whose `FLYC_STATE` codes (31 vs the GPS-flight set) are the least
ambiguous; Cine is a poor probe because of the open Cine↔Tripod question.

**Restore is layered on purpose.** The pure scan leaves the last probed candidate latched — it has
no business owning aircraft state. The wrapper (`Client::auto_detect_mode_cmd_id` /
`auto_detect_mode_cmd_id()`) snapshots the cmd_id and the baseline mode first, and then:
on failure restores the previous cmd_id, on success returns the aircraft to the mode it started
in. It never leaves the aircraft in Sport because a detection ran.

--------------------------------------------------------------------------------
## 5) Where this lives in the code

| layer | C++ | Python beta |
|---|---|---|
| model, gear mapping, frame | `src/core/flight_mode.{hpp,cpp}` | `dji_link_beta/flight_mode.py` |
| command path | `Drone::set_flight_mode(FlightMode)`, `set_soft_switch_cmd_id()` (`src/core/drone.cpp`) | `Drone.set_flight_mode()`, `set_soft_switch_cmd_id()` (`drone.py`) |
| derived mode from telemetry | `derived_user_mode()`, `OsdState::user_mode` (`src/core/telemetry.cpp`) | `telemetry.py` |
| cmd_id detection | `src/core/soft_switch_detect.{hpp,cpp}` + `Client::auto_detect_mode_cmd_id` | `soft_switch_detect.py` |
| console | `fmode cine\|normal\|sport`, `smid 0x06\|0x11\|0x19`, `detectmode` (`src/core/client.cpp`) | same commands in `pc_client.py` |
| HUD | "MODE" = derived user mode, raw `FLYC_STATE` secondary (`src/gui/gui.cpp`) | `pc_client.py` HUD |
| sim | sim transport answers a gear frame with a new `FLYC_STATE` (`src/core/transport.cpp`) | `LogTransport` (`transport.py`) |
| tests | `tests/flight_mode_test.cpp`, `tests/soft_switch_detect_test.cpp`, … (`ctest`) | `python3 dji_link_beta/test_flight_mode.py` (84 checks, no hardware) |

--------------------------------------------------------------------------------
## 6) Open unknowns — settle these on the drone

1. **Which cmd_id** (`0x06` / `0x11` / `0x19`) the RC accepts, and whether the u32-LE payload is
   the right wrapper for the key.
2. **Cine↔Tripod:** asking for Cine sends the TRIPOD gear; in the sim it reads back as
   `FLYC_STATE 38` (Tripod), not 19 (Cinematic). Whether real hardware answers 19 or 38 — and
   whether CineSmooth needs a different gear value entirely — is unverified.
3. **Gating** (does a gear frame apply at all in the current aircraft state): see the checklist.
4. **Virtual sticks:** confirm that the gear is ignored at `FLYC_STATE 17` and that switching
   before opening joystick authority sticks.

`FLIGHT_MODE_HW_CHECKLIST.md` is the step-by-step for all four.

--------------------------------------------------------------------------------
## Citations
- KeyValue / RC: `all_classes.txt` → `uav/sdk/keyvalue/value/remotecontroller/RcSoftSwitchMode`,
  `RcSoftSwitchModeMsg`, `com/uav/flymodel/handwrite/map/flight/v1/V1FlightSoftSwitchModeKt`.
- No FLYC mode setter: `dji/midware/data/config/P3/CmdIdFlyc$CmdIdType.class`,
  `dji/midware/data/model/P3/DataFlycFunctionControl$FLYC_COMMAND.class` (via
  `FLIGHT_MODE_SPEED_RESEARCH_2026.md` §1a).
- Telemetry: `dji/midware/data/model/P3/DataOsdGetPushCommon.class` (`getFlycState`,
  `getModeChannel`) + `$FLYC_STATE.class`; parsed layout in `OSD_TELEMETRY_RESEARCH_2026.md`.
- Gear/preset params (live WM160): `PARAM_TABLE_WM160.md` — `control.control_mode[0..2]`
  (`0xde3b160b`/`0xdf3b160b`/`0xe03b160b`, 12/8/7), `gear_cfg.*`, `mode_normal_cfg.tilt_atti_range_0`
  (`0x95544807`).
- Sender-address rule: `TAKEOFF_UNLOCK.md`, `DUML_ENCRYPTION.md` (RC frames plaintext; FLYC
  config/param frames SIMPLE-encrypted on the radio path).
- Speed (separate setting): `FLIGHT_MODE_SPEED_RESEARCH_2026.md`.
