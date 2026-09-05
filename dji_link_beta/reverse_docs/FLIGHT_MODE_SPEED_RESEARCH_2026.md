# Flight Mode (Cine / Normal / Sport) & Max Horizontal Speed — WM160 (Mavic Mini 1)

Dedicated reverse pass, 2026-07-18. Ground truth = `dji-sdk-provided-4.18.jar`
(`scratchpad/msdk/all/`), the WM160 live param table (`PARAM_TABLE_WM160.md`, 132 params
that actually answer on this airframe), and `flyc_param_infos.json` (dji-firmware-tools).

> **★ Update 2026-09-05 — the mode-switch half of this document is SUPERSEDED by
> `FLIGHT_MODE_SOFTSWITCH_2026.md`.** Flight mode *is* switchable from the app: the gear channel is
> driven by the RC-component key `RemoteController/SoftSwitchMode` (cmd_set `0x06`, receiver `0x06`,
> plaintext, payload = u32 LE gear value), which this repo now implements and confirms via
> `FLYC_STATE`. What stays true below: there is no *FLYC* (`0x03`) mode setter, the modes are
> pre-stored config blocks, and — the reason to keep this file — **max horizontal speed** is
> `mode_normal_cfg.tilt_atti_range_0`. Treat every "we cannot switch, so rewrite the Normal block"
> conclusion as history; the tilt param is a **speed** setting only.

--------------------------------------------------------------------------------
## TL;DR — what to implement

**There is NO "set flight mode" command in the FLYC (`0x03`) command set.** Cine / Normal /
Sport are three flight-controller **parameter presets**, selected on a normal aircraft by
the RC's *gear channel* (`COMMAND_GEAR` → `g_config.control.control_mode[0..2]`). Our float
joystick `0x03/0x8E` carries no gear field — but the gear can be driven directly on the **RC**
command set (`0x06`), which is what `FLIGHT_MODE_SOFTSWITCH_2026.md` documents and the code now
does. Rewriting the preset params, described below, is therefore the **speed** lever, not a mode
switch.

**Max horizontal speed on WM160 is set by the max lean/attitude angle** — the param
`tilt_atti_range` (degrees). There is **no dedicated horizontal-velocity (m/s) limit param on
WM160**: `g_config.control.horiz_vel_atti_range_0` (what `drone.py` writes today) is **ABSENT**
on this airframe, so the current `set_horizontal_speed()` is a no-op. Bigger tilt = faster.

| desired mode | param to write (`0x03/0xF9  [hash u32 LE][f32 LE]`) | value |
|---|---|---|
| **Cinematic** (slow/smooth) | `g_config.mode_normal_cfg.tilt_atti_range_0` = **10.0°** | ~4 m/s |
| **Normal** (default)        | `g_config.mode_normal_cfg.tilt_atti_range_0` = **20.0°** | ~8 m/s |
| **Sport** (fast)            | `g_config.mode_normal_cfg.tilt_atti_range_0` = **30.0°** | ~13 m/s |
| **Beyond Sport**            | `g_config.mode_normal_cfg.tilt_atti_range_0` = **35–40°** | ~15–17 m/s |

`mode_normal_cfg.tilt_atti_range_0`: **hash `0x95544807`**, type **f32**, access **RW+EE**
(writable + self-persists), current live value **20.0**, published min/max `-360/360` (loose;
keep to 8–40 for safety). This one param IS both the "flight mode" gear *and* the horizontal
speed limit for our use case.

--------------------------------------------------------------------------------
## 1) FLIGHT MODE SWITCH — mechanism

### 1a. It is not a command
`CmdIdFlyc$CmdIdType` has 129 entries; **none** is a mode/gear/sport/cine setter. The only
"set" verbs that touch flight behaviour are the param writers:
`SetParamsByHash` (**cmd 0xF9**), `SetParamsByIndex`, `SetPushParams`.
`DataFlycFunctionControl$FLYC_COMMAND` (cmd `FunctionControl`) enumerates only
AUTO_FLY(1), AUTO_LANDING(2), HOMEPOINT_NOW(3), HOMEPOINT_HOT(4), HOMEPOINT_LOC(5),
GOHOME(6), START_MOTOR(7), STOP_MOTOR(8)… — **no mode/gear**.
`DataFlycSetJoyStickParams$FlycMode` = A(0)/P(1)/F(2)/(100) — those are the classic
Atti/GPS/Func control modes carried in the legacy RC-channel emulation, **not** Cine/Normal/Sport.
Citations: `all/dji/midware/data/config/P3/CmdIdFlyc$CmdIdType.class`,
`all/dji/midware/data/model/P3/DataFlycFunctionControl$FLYC_COMMAND.class`,
`all/dji/midware/data/model/P3/DataFlycSetJoyStickParams$FlycMode.class`.

### 1b. It is a gear index selecting a pre-stored FC config block
The FC pre-stores one config block per mode (`g_config.mode_normal_cfg.*`,
`g_config.mode_sport_cfg.*`, `g_config.mode_gentle_cfg.*` = CineSmooth, `g_config.mode_tripod_cfg.*`).
Switching mode does **not** rewrite these blocks at runtime — the RC/app just tells the FC
**which block is active** via the *gear channel* → `g_config.control.control_mode[0..2]`
(mapped from `COMMAND_GEAR`, mapper hash `0x2dba613c`; live gear value in
`g_real.input.channel[COMMAND_GEAR]`). A drone with a physical 3-position switch drives the gear
channel from the RC; the **switchless Mini emulates the same channel in software** when you tap
P/S/C in DJI Fly. *(Corrected 2026-09-05: that software gear is reachable — DJI Fly's
`RemoteController/SoftSwitchMode` key goes out on the RC command set `0x06`. The claim below that
no "set gear" opcode exists holds only for the **public MSDK/FLYC** surface, which is why Litchi
cannot do it.)* *Cross-check confirms our live capture: on
WM160 `control_mode[0/1/2]` = 12 / 8 / 7 exactly (444A49/minifindings Mini dump).*

Because we never inject a gear channel (our float joystick `0x03/0x8E` has no gear field), the FC
runs whatever block the default gear selects (Normal). So the airframe-verified way to change our
"mode"/speed is to **rewrite the active (Normal) block's `tilt_atti_range`** — this is exactly the
firmware-modder path (permanently re-tune the mode). On WM160 the writable preset tilt params
that *answer live* are:

| preset (mode) | tilt-angle param | live | notes |
|---|---|---|---|
| Normal | `g_config.mode_normal_cfg.tilt_atti_range_0` (`0x95544807`, f32) | **20.0** | RW+EE — **our lever** |
| Sport  | `mode_sport_cfg_tilt_atti_range_0` (`0x3bf365ce`, f32) | **30.0** | RW+EE; `mode_sport_cfg_vert_vel_up_0` (`0xac320b0d`, f32)=4.0 |
| CineSmooth | `g_config.mode_gentle_cfg.*` / `rc_scale` | **not in our sweep** | Mini Cine = *gentle* block; its slowness comes from **`rc_scale` 0.25** (stick→tilt scaling), tilt is still 20°, not a lower ceiling |

**Nuance (from cross-check):** CineSmooth does NOT lower the tilt ceiling — gentle tilt is also
20°; its 4 m/s cap comes from `rc_scale` 0.25 (how much full stick maps to tilt). Sport is a real
tilt-ceiling raise (20°→30°). Since `mode_gentle_cfg`/`rc_scale` were not in our WM160 sweep, our
practical Cine proxy is simply a **lower** `mode_normal_cfg.tilt_atti_range` (e.g. 10–12°).

The tilt-angle → speed identity is confirmed by the Mini's published, per-mode DJI figures:
CineSmooth 4 m/s, Position 8 m/s, Sport 13 m/s (the FC vertical-speed defaults 1.5/1.0, 2.0/1.8,
4.0/3.0 match DJI's spec **exactly**, proving these blocks are the real limiters).
tan(20°)/tan(30°) ≈ 0.364/0.577 ≈ 1.59, and 13/8 ≈ 1.6 — the modes ARE tilt-angle presets.
(Empirical anchors: 20°→~8, 30°→~13 m/s; community mods push `mode_sport_cfg.tilt_atti_range`
toward its live max ~60° for higher speed.)

--------------------------------------------------------------------------------
## 2) MAX HORIZONTAL SPEED — the lever

**On WM160 there is no dedicated horizontal-velocity-limit parameter.** The generic
`flyc_param_infos.json` lists `g_config.control.horiz_vel_atti_range_0` and
`g_config.control.atti_range_0`, but **neither answers on WM160** (not in the 132 live params;
their read-by-hash returns nothing). So max horizontal speed is bounded purely by the max lean
angle of the active config = `tilt_atti_range`.

**Write:** `0x03/0xF9  [hash u32 LE][value f32 LE]`
```
hash  = 0x95544807   # g_config.mode_normal_cfg.tilt_atti_range_0  (Normal/active preset)
value = struct.pack("<f", angle_deg)   # 8.0 .. 40.0 sane; 20.0 = stock Normal
```
- Type f32, degrees. RW+EE (persists across power cycles — remember to restore 20.0).
- Safe first test value: **25.0°** (a clear, modest bump over stock 20° → ~10 m/s).
- To also unlock Sport's own block (in case a gear ever selects it):
  `mode_sport_cfg_tilt_atti_range_0` (`0x3bf365ce`). Our `flyc_param_infos.json` reports max 40;
  the live 444A49 Mini dump reports range 10–60 (community mods go to 60). Keep test values ≤40
  until confirmed on your airframe.
- Cross-check ruled out the alternatives: `g_config.control.horiz_vel_atti_range` is the
  *older* Phantom-3/Inspire global tilt param (degrees, def 45) — replaced on Mavic-gen FCs by the
  per-mode `mode_*_cfg.tilt_atti_range`, and **absent on WM160**. `horiz_vel_p_gain` is a
  velocity-loop gain, not a limit. There is NO m/s horizontal-velocity cap for manual flight
  (only `waypoint_cfg.max_horiz_vel` / `farm.max_vel_limit`, which are autonomous-mode only).
- Vertical (not horizontal, but adjacent): `mode_sport_cfg_vert_vel_up_0` (`0xac320b0d`, f32,
  live 4.0, min1/max10).

The captured attrs (`RW+EE`, attribute byte bit0 set) confirm the FC **accepts** `0xF9` writes
to these; RO params silently drop the write. Write mechanics, hashing (gbk, `h=(b+(h<<8)) %
(2**32-5)`), and the `0x40` plaintext cmd_type are already proven — see `PARAM_WRITE_TRUTH.md`,
`PARAM_TABLE_WM160.md`.

--------------------------------------------------------------------------------
## 3) VERIFY — how to confirm a change on hardware

1. **Param readback (write took):** `0x03/0xF8 [hash u32 LE]` → reply
   `[retcode][hash u32 LE][value]`. Read `0x95544807` back; the f32 must equal what you wrote.
   (`drone.read_param("g_config.mode_normal_cfg.tilt_atti_range_0")`.) RW+EE self-persists, so a
   correct echo means it's live.
2. **Speed actually changed (in flight):** watch OSD ground velocity. OSD general push
   (cmd_id `0x43`): `vx = s16 @0x12 ×0.1 m/s`, `vy = s16 @0x14 ×0.1 m/s` (already parsed in
   `telemetry.py`). Command full forward pitch and read steady-state `sqrt(vx²+vy²)` — it should
   rise as `tilt_atti_range` rises.
3. **The user gear/mode readout (do NOT rely on it here):** the reported gear is
   `DataOsdGetPushCommon.getModeChannel()` → `RcModeChannel` = CHANNEL_MANUAL/A/P/NAV/FPV/FARM/
   S/F/M/G/T. This mirrors the **RC gear channel**, which we do not drive, so it will keep
   reading its default (e.g. CHANNEL_P) even after we change speed. Confirm via (1)+(2), not the
   mode channel. `flyc_state` in OSD (`@0x1e & 0x7F`) is the *flying* state, not the user gear, so
   it does not confirm a **tilt/speed** write either. *(2026-09-05: it IS the confirmation for a
   real gear switch — Sport 31 / Cinematic 19 / Tripod 38 — see `FLIGHT_MODE_SOFTSWITCH_2026.md`
   §3. The point that stands here is narrower: a tilt param write leaves `flyc_state` unchanged.)*
   Citations:
   `all/dji/midware/data/model/P3/DataOsdGetPushCommon.class` (`getModeChannel`, `getFlycState`),
   `all/dji/midware/data/model/P3/DataOsdGetPushCommon$RcModeChannel.class`.

--------------------------------------------------------------------------------
## 4) Exact drone.py replacement — HISTORY (do not re-apply the mode part)

> **Superseded 2026-09-05.** `set_flight_mode()` now sends the SoftSwitchMode gear frame
> (`FLIGHT_MODE_SOFTSWITCH_2026.md`); the `_MODE_TILT` table below would silently turn a mode
> switch back into a speed change. Only `set_max_tilt_angle()` / `set_horizontal_speed()` — the
> speed half — reflect the shipped code, which keeps mode and speed strictly separate.

Replace the current no-op `set_horizontal_speed()` (writes the absent
`g_config.control.horiz_vel_atti_range_0`) and the `NotImplementedError` `set_flight_mode()`:

```python
# Max horizontal speed on WM160 = max lean angle of the active (Normal) config.
# There is NO horizontal-velocity(m/s) limit param on this airframe; tilt_atti_range
# (degrees) is the lever. RW+EE (persists) -> restore 20.0 when done.
_NORMAL_TILT = "g_config.mode_normal_cfg.tilt_atti_range_0"   # hash 0x95544807, f32 deg

def set_max_tilt_angle(self, deg: float) -> None:
    import struct as _s
    deg = max(5.0, min(40.0, float(deg)))
    self.set_param(self._NORMAL_TILT, _s.pack("<f", deg))

# Empirical WM160 tilt->speed anchors: 20 deg ~8 m/s, 30 deg ~13 m/s, 40 deg ~16-17 m/s.
def set_horizontal_speed(self, mps: float) -> None:
    # invert the (roughly linear over 8..17 m/s) tilt->speed map; clamp to hardware range.
    deg = 20.0 + (float(mps) - 8.0) * (10.0 / 5.0)   # +2 deg per +1 m/s over 8
    self.set_max_tilt_angle(deg)

# "Flight mode" = a tilt-angle preset (no single DUML exists; gear channel not driven).
_MODE_TILT = {"cine": 10.0, "cinematic": 10.0,
              "normal": 20.0, "position": 20.0, "p": 20.0,
              "sport": 30.0, "s": 30.0}
def set_flight_mode(self, name: str) -> None:
    deg = self._MODE_TILT.get(name.strip().lower())
    if deg is None:
        raise ValueError(f"mode must be one of {sorted(self._MODE_TILT)}")
    self.set_max_tilt_angle(deg)
```
Verify each call with `read_param(self._NORMAL_TILT)` and, in flight, the OSD ground speed.

--------------------------------------------------------------------------------
## Citations
- JAR (VA = class path under `scratchpad/msdk/all/`):
  - `dji/midware/data/config/P3/CmdIdFlyc$CmdIdType.class` — 129 cmd ids, no mode/gear setter; `SetParamsByHash`=0xF9, `GetParamsByHash`=0xF8.
  - `dji/midware/data/model/P3/DataFlycFunctionControl$FLYC_COMMAND.class` — FLYC command enum (no mode).
  - `dji/midware/data/model/P3/DataFlycSetJoyStickParams.class` + `$FlycMode.class` — A/P/F control modes, `mode_sw` gear field in the legacy `CmdSet.SPECIAL/JoySitckSetParams` channel emulation.
  - `dji/midware/data/model/P3/DataOsdGetPushCommon.class` + `$RcModeChannel.class` / `$FLYC_STATE.class` — telemetry: `getModeChannel()` (user gear), `getFlycState()` (flying state).
- WM160 live data: `reverse_docs/PARAM_TABLE_WM160.md` (mode_normal_cfg.tilt_atti_range=20.0 RW+EE 0x95544807; mode_sport_cfg_tilt_atti_range=30.0 0x3bf365ce; mode_sport_cfg_vert_vel_up=4.0 0xac320b0d), `params_table.txt`, `flyc_param_infos.json`.
- Write/read protocol & hashing: `reverse_docs/PARAM_WRITE_TRUTH.md`, `reverse_docs/PARAM_WIRE.md`, `param_hash.py`.

### Web cross-check (2026-07-18) — corroborates the jar; sources:
- **WM160 live param dump** (defaults/ranges, `control_mode`=12/8/7, mode_*_cfg blocks, gear_cfg): https://github.com/444A49/minifindings/blob/master/parameters.md and .../README.md
- Mavic Pro flyc_param_infos (per-mode `tilt_atti_range`: normal 25/sport 35/cinematic 25, range 10–60): https://github.com/brett8883/Super-Firmware_Cache/blob/master/MavicPro_Super_Patcher_FC/flyc_param_infos
- FLYC DUML dissector (`COMMAND_GEAR` mapper `0x2dba613c`, control_mode hashes, function control, flyc_state enum): https://github.com/o-gs/dji-firmware-tools/blob/master/comm_dissector/wireshark/dji-dumlv1-flyc.lua
- `atti_range` "directly effects tilt and speed": https://github.com/o-gs/dji-firmware-tools/issues/16
- Phantom-3 `horiz_vel_atti_range` (10/60/45, the older global tilt lever): https://phantompilots.com/threads/phantom-3-flight-controller-parameters.105884/
- No public API to set flight mode (switchless Mini context): https://b4x.com/android/forum/threads/dji-how-to-set-the-flight-mode-to-sport.127769/
- Mini per-mode speeds — Sport 13 / Position 8 / CineSmooth 4 m/s, vert speeds: https://www.heliguy.com/blog/dji-mavic-mini-how-to-use-cinesmooth-mode/ , Mavic Mini User Manual v1.0: https://dl.djicdn.com/downloads/Mavic_Mini/Mavic_Mini_User_Manual_v1.0_en.pdf
- Tilt vs max flight speed (physics): https://mavicpilots.com/threads/tilt-and-maximum-flight-speed.21308/

**Caveats:** DJI's headline spec lists only Sport 13 m/s; 8/4 m/s are manual/community-sourced.
Mini has no `mode_cinematic_cfg` — CineSmooth = `mode_gentle_cfg` (inferred from exact 1.5/1.0
vertical-speed match) and its 4 m/s cap is `rc_scale` 0.25, not a lower tilt ceiling.
