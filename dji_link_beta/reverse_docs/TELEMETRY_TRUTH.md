# TELEMETRY_TRUTH — WM160 (Mavic Mini 1) flight telemetry, re-verified from scratch

DJI Fly v1.21.4. Every offset below is taken **directly from the app's own byte-level
DUML parser** (`uav/midware/data/model/P3/DataOsdGetPushCommon` and
`DataSmartBatteryGetPushDynamicData`, both baksmali'd from the 16 app DEX) and
cross-checked against the native `libsdk_jni.so` decoder table (`TELEMETRY_TABLE.txt`)
**and** against the real captured frames. Prior docs were not trusted.

`DataBase.get(offset, size, Class)` (class `uav/midware/data/manager/P3/DataBase`)
indexes `offset` **directly into the DUML payload** (`_recData`), i.e. the bytes *after*
the 11-byte DUML header, before the 2-byte CRC. `Short`=`<h` (s16), `Integer`=`<i`/`<I`,
`Double`=`<d`, `Byte`=`<b`. So our `telemetry.py` offsets, which index `pkt.payload`,
are on the same origin — **confirmed by the battery frame decoding byte-perfect (below),
which proves there is no header/framing shift.**

---

## 0. HEADLINE — altitude vs climb rate (the bug)

| Quantity | cmd | offset | type | scale | unit | source |
|---|---|---|---|---|---|---|
| **ALTITUDE (relative/baro height)** | 0x03/0x43 | **0x10** | s16 | ×0.1 | metres | `DataOsdGetPushCommon.getHeight()` |
| **CLIMB RATE (vertical velocity, Z)** | 0x03/0x43 | **0x16** | s16 | ×0.1 | m/s | `DataOsdGetPushCommon.getZSpeed()` |

These are two **different** fields, 6 bytes apart, in the **same** 0x03/0x43 OSD push.
Altitude is the one that grows monotonically as the aircraft climbs and holds when it
levels off; Z-velocity spikes during the climb and returns to ~0 in hover — that is the
"looks like climb rate" signature.

**`telemetry.py` already reads both at the correct offsets** (`alt = s16@0x10`,
`vz = s16@0x16`). The offsets are NOT the bug. If the HUD shows climb-rate behaviour in
the altitude slot, the swap is in the **consumer/HUD wiring** (it is displaying
`state.vz` where it means `state.altitude_m`, or vice-versa), not in the parser. Fix the
display mapping: altitude ← `altitude_m` (from 0x10), climb ← `vz` (from 0x16).

Why the ground captures can't show it: on the ground with no GPS/VPS lock the FC leaves
height=0 and vz=0, so the 0x43 frame is all-zero and the two fields are indistinguishable
until the aircraft actually flies.

Which OSD id carries height for WM160: **0x03/0x43** (`DataOsdGetPushCommon`, the classic
"OSD general" push). The float-bearing 0x03/0x44, 0x03/0x67, 0x03/0x51 frames are **not**
the height/velocity source (see §5).

---

## 1. FLIGHT OSD — cmd_set 0x03 / cmd_id 0x43  (`DataOsdGetPushCommon`)

Payload origin = first byte of longitude. Verified length in capture = 55 B.

| Field | offset | type | scale/mask | unit | app getter |
|---|---|---|---|---|---|
| longitude | 0x00 | f64 | radians→deg | ° | `getLongitude()` (double@0, ×) |
| latitude | 0x08 | f64 | radians→deg | ° | `getLatitude()` (double@8, ×) |
| **relative height (ALTITUDE)** | **0x10** | **s16** | ×0.1 | **m** | `getHeight()` |
| velocity X (vgx / north) | 0x12 | s16 | ×0.1 | m/s | `getXSpeed()` |
| velocity Y (vgy / east) | 0x14 | s16 | ×0.1 | m/s | `getYSpeed()` |
| **velocity Z (vgz / CLIMB RATE)** | **0x16** | **s16** | ×0.1 | m/s | `getZSpeed()` |
| pitch | 0x18 | s16 | ×0.1 | ° | `getPitch()` |
| roll | 0x1a | s16 | ×0.1 | ° | `getRoll()` |
| yaw / heading | 0x1c | s16 | ×0.1 | ° | `getYaw()` |
| flight mode (flyc_state) | 0x1e | u8 | `& 0x7f` | enum | `getFlycState()` (masks bit7 = `~0x80`) |
| gohome status | 0x20 | u32 | `>>5 & 0x7` | enum | `getGohomeStatus()` |
| is_flying | 0x20 | u32 | `& 0x0e` | bool | native `IsFlying` |
| motors on | 0x20 | u32 | `bit 3` | bool | native `AreMotorsOn` |
| GPS signal level | 0x20 | u32 | `>>0x12 & 0xf` | 0..5 | `getGpsLevel()` |
| voltage warning | 0x20 | u32 | `& 0x600 >>9` | enum | `getVoltageWarning()` |
| satellite count | 0x24 | **u8** | — | count | `getGpsNum()` — **re-verified from MSDK jar bytecode: `DataBase.get(0x24, size=1, Short.class)` → the SIZE arg is `iconst_1` = ONE byte. The `Short` is only the return-boxing class; it does NOT mean a 2-byte read. Prior "u16" claim here was WRONG.** |
| flight action | 0x25 | u8 | — | enum | `getFlightAction()` (size=1 @0x25) |
| **motor start-fail cause** | **0x26** | **u8** | `& 0x7f` (bit7 = "no-start action") | enum | `getMotorFailedCause()` (size=1 @0x26; on flycVersion≥26 it returns the FULL byte, no mask — our `&0x7f` stays safe for the codes we surface) |
| GPS-mode failure reason | 0x27 | u8 | `& 0xf` | enum | native `GPSModeFailureReason` |
| battery % (FC copy) | 0x28 | u32 | field | % | `getBattery()` |
| VPS/ultrasonic height (swave) | 0x29 | **s8** | ×0.1 | m | `getSwaveHeight()` — **re-verified: `DataBase.get(0x29, size=1, Short.class)` = ONE signed byte, NOT s16. Prior "s16" claim was WRONG (a 2-byte read would swallow flyTime@0x2a).** |
| flight time | 0x2a | u16 | ×1 | s | `getFlyTime()` / native `FlightTimeInSeconds` |
| motor revolution | 0x2c | s16 | — | — | `getMotorRevolution()` |
| IMU init-fail reason | 0x31 | u8 | — | enum | native `IMUFailureReason` |

**flyc_state enum (byte@0x1e & 0x7f), WM160 values — CODE = the 3rd ctor arg
(`FLYC_STATE(String,int ordinal,int CODE)`), which is what `FLYC_STATE.find(int)` matches.
Re-verified field-by-field from the MSDK jar static initializer. NOTE: code is NOT a dense
0..N sequence — it MATCHES the ordinal only up to 17, then SKIPS values (18, 20, 21, 22,
34, 40, 42, 44, 45, 47, 48 are UNUSED codes):**
```
0 Manual          1 Atti           2 Atti_CL        3 Atti_Hover     4 Hover
5 GPS_Blake       6 GPS_Atti       7 GPS_CL         8 GPS_HomeLock   9 GPS_HotPoint
10 AssistedTakeoff 11 AutoTakeoff  12 AutoLanding   13 AttiLanding   14 NaviGo
15 GoHome         16 ClickGo       17 Joystick      19 Cinematic     23 Atti_Limited
24 NaviSubMode_Draw 25 NaviMissionFollow 26 NaviSubMode_Tracking 27 NaviSubMode_Pointing
28 PANO           29 Farming       30 FPV           31 SPORT         32 NOVICE
33 FORCE_LANDING  35 TERRAIN_TRACKING 36 PALM_CONTROL 37 QUICK_SHOT  38 TRIPOD_GPS
39 TRACK_HEADLOCK 41 ENGINE_START  43 DETOUR        46 TIME_LAPSE    49 OMNI_MOVING
50 POI_WITH_VISION 51 SMART_TRACK  52 LOST_POWER_FORCE_LANDING       100 OTHER
```
(`telemetry.py`'s FLYC_STATE dict is a dense 0..40 sequence — CORRECT for codes 0–17
but WRONG for every code ≥18: it has 18=Cinematic (real 18 is UNUSED; Cinematic=19),
19=Atti_Limited (real 23), … everything shifts. For the WM160's day-to-day modes
(GPS_Atti=6, AutoTakeoff=11, AutoLanding=12, GoHome=15, Joystick=17) it is fine; the
misnaming only shows for the exotic high-code modes. Replace the dict with the codes above,
mapping unknown codes to "?N".)

---

## 2. HORIZONTAL / VERTICAL SPEED

Ground-frame velocity vector is at **0x12/0x14/0x16** (s16 ×0.1 m/s), body/ground frame
X, Y, Z. Horizontal speed = `hypot(vx, vy)`; climb rate = `vz` (0x16). Native lib exposes
the same triple twice: `Velocity` (@0x12/0x14/0x16) and `VelocityFromAircraftCoordinate`
(@0x12/0x14/0x16 + yaw@0x1c) — both ×0.1.

---

## 3. BATTERY — smart-battery dynamic, cmd_set 0x0D / cmd_id 0x02
`DataSmartBatteryGetPushDynamicData` — offsets are `dataOffset + N`; `dataOffset = 0` in
the WM160 capture, so N == payload offset. **Verified byte-perfect against the real 138-B
frame** `00 661f0000 38fdffff 10080000 6c070000 2c01 03 5c ...`:

| Field | offset | type | scale | unit | value in capture |
|---|---|---|---|---|---|
| index | 0x00 | u8 | — | — | 0 |
| **voltage** | **0x01** | u32 | ×1 | mV | 0x1f66 = 8038 mV |
| **current** | **0x05** | s32 | ×1 | mA | 0xfffffd38 = −712 mA (discharge) |
| **full capacity** | **0x09** | u32 | ×1 | mAh | 0x0810 = 2064 |
| **remain capacity** | **0x0D** | u32 | ×1 | mAh | 0x076c = 1900 |
| **temperature** | **0x11** | s16 | ×0.1 | °C | 0x012c = 300 → **30.0 °C** |
| cell size | 0x13 | u8 | — | count | — |
| **remaining %** | **0x14** | u8 | ×1 | % | 0x5c = **92** (=1900/2064 ✓) |
| status bits | 0x15 | u64 | flags | — | — |
| SOH state | 0x19 | u8 | — | % | — |
| cycle count/limit | 0x1a | s8 | — | — | — |
| version | 0x1d | u8 | — | — | — |
| heat state | 0x20 | u16 | — | enum | — |

Per-cell mV = **cmd_set 0x0D / cmd_id 0x03** (`get_cell_voltage`,
`DataSmartBatteryGetPushCellVoltage`): capture `00 02 b40f b20f 0000 0000` →
cell1 0x0fb4 = 4020 mV, cell2 0x0fb2 = 4018 mV (u16 LE per cell, starting ~offset 0x02).

`telemetry.py` battery offsets (V@0x01, I@0x05, full@0x09, rem@0x0D, %@0x14) are all
**correct**; it is only **missing temperature (s16@0x11 ×0.1 °C)** and current sign
handling (current is **signed** s32, negative = discharge — read `<i`, not `<I`).

---

## 4. REMAINING FLIGHT TIME  (this is the important "where does it come from")

**It is NOT in the 0x0D/0x02 smart-battery frame.** It is a **computed FC value** pushed
from the flight controller in a separate battery-assessment push, `u16` **seconds** at
**offset 0x00**:

| Field | source push (native class) | VA | offset | type | unit |
|---|---|---|---|---|---|
| **RemainingFlightTime** | `uav_fc_func_mcu_battery_capacity_gohome_landing_to_app_push` | 0x2bed874 | **0x00** | u16 | **seconds** |
| RemainingFlightTimeFromBatteryOsd | `uav_fc_electricity_push` | 0x2bed998 | 0x00 | u16 | seconds |
| GoHomeAssessment (same push) | …battery_capacity_gohome_landing… | 0x2bed4a4 | 0x00:u16 rem-time, 0x02:u16 gohome-elec, 0x04:u16 land-elec, 0x06:u8, 0x07:u8 | — | — |
| GoHomeAssessmentFromBatteryOSD | `uav_fc_electricity_push` | 0x2bed668 | 0x00:u16 … 0x0a:f32 … 0x16:u8 0x17:u8 | — | — |

Both are FC (**cmd_set 0x03**) keyed pub/sub pushes, not in the static routing table; the
first u16 of the push is the estimated remaining flight time in **seconds** (the number
the HUD shows as "xx:xx" / minutes). The app surfaces it via key `KeyRemainingFlightTime`
/ `GoHomeAssessment.getRemainingFlightTime()` and
`getEstimatedRemainingFlightTime()`. **Action:** to display remaining flight time you must
subscribe to / parse this FC battery-assessment push (u16 seconds @0x00); it will never
appear in 0x0D/0x02.

Battery-warning thresholds live in `uav_fc_electricity_push`:
`LowBatteryWarningThreshold` @0x1b:u16 `&0x7f`, `SeriousLowBatteryWarningThreshold` @…
(percent), plus `RequireGoHome*/RequireLanding*` bit-flags in the 0x43 OSD word @0x12/0x20.

---

## 5. The float-bearing 0x03/0x44, 0x03/0x67, 0x03/0x51 frames — NOT height

Decoded from the captures they carry `float32` values, not the s16 flight state:
- **0x44** (len 78): `f32@0x04 = 10.526` repeated at @0x0C — a paired/duplicated sensor
  value, plus `f32@0x10 ≈ 1833`. Non-zero on the ground ⇒ **not** height/velocity (those
  are 0 on the ground). Do not parse as OSD.
- **0x67** (len 16): `f32@0x0C = 100.0` — a limit/percentage-style scalar, not height.
- **0x51** (len 30): pair of `f32` (`…4535a59a…`) — not the flight OSD.

Height and velocity for WM160 come **only** from the s16 fields of **0x03/0x43**.

---

## 6. What `telemetry.py` gets wrong (corrected)

| Item | current | correct |
|---|---|---|
| altitude offset | s16@0x10 ×0.1 | **correct — keep** |
| climb/vz offset | s16@0x16 ×0.1 | **correct — keep** (the HUD swap is downstream) |
| **satellites** | **u16@0x24** (current telemetry.py) | **u8@0x24** — `getGpsNum` size arg is `iconst_1` = 1 byte. Read `p[0x24]`, NOT `<H`. (See satellites root-cause note below.) |
| VPS/swave height | s16@0x29 (current telemetry.py) | **s8@0x29** ×0.1 — `getSwaveHeight` size arg = 1 byte. Read one signed byte, NOT `<h`. |
| motor-fail cause | u8@0x33 | **u8@0x26 & 0x7f** (0x33 is wrong; 0x26 is `getMotorFailedCause`) |
| flight-mode names | dense 0..40 dict, wrong ≥18 | non-dense codes in §1 (17=Joystick, 19=Cinematic, 38=TRIPOD_GPS, 100=OTHER) |
| battery current | u32@0x05 | s32@0x05 (signed; negative = discharge) |
| battery temperature | (missing) | add s16@0x11 ×0.1 °C |
| remaining flight time | (missing / assumed battery) | FC battery-assessment push, u16 seconds @0x00 (cmd_set 0x03) — separate push, see §4 |
| gohome status | (missing) | u32@0x20 `>>5 & 0x7` |
| VPS height | (missing) | s16@0x29 ×0.1 m (`getSwaveHeight`, useful indoors/no-GPS) |

---

## 7. SATELLITES root-cause — why the flight showed sats=0 (2026-07 re-verify)

`getGpsNum()` in the MSDK jar is a flat, unversioned read: `DataBase.get(0x24, 1, Short)`.
**Offset 0x24 is CONFIRMED correct** — it is *not* at a different offset. The only decode
error is **width**: telemetry.py reads `<H` (2 bytes); DJI reads 1 byte. **Fix width to u8.**

Honest caveat: the u16→u8 width change **does not, by itself, turn a `0` reading into a
non-zero one.** A little-endian `<H` at 0x24 = `p[0x24] | (p[0x25]<<8)`, whose low byte is
exactly the u8 value; so u16 reads `0` only when the real u8 sat byte is also `0`. The
width bug's true failure mode is the opposite: when `flightAction` (byte 0x25) is non-zero,
the u16 read **inflates** satellites by multiples of 256 — it never zeroes them.

So a genuine `sats=0` during a "GPS" flight is a **pre-lock frame**, not an offset bug. The
real captured 0x43 frame in `telemetry_dump.txt` proves this:
`flyc_state@0x1e = 6 (GPS_Atti)` **yet** `gpsLevel(word@0x20>>18&0xf)=0` and
`p[0x24]=0`. The FC advertises the GPS_Atti *mode* before the fix count / gps level have
populated, so "mode says GPS but sats 0" is fully reproducible on the ground with no bug.
**To confirm sats land in byte 0x24 in flight, capture one 0x43 frame after a real 3D lock
(gpsLevel ≥ 3) and check `p[0x24]` is the HUD sat count.** All evidence says it will be.

Sources: `DataOsdGetPushCommon.smali`, `DataSmartBatteryGetPushDynamicData.smali`,
`DataBase.smali` (all from app DEX `classes_016b200c/0451d00c`); native table
`TELEMETRY_TABLE.txt`; live WM160 captures.
