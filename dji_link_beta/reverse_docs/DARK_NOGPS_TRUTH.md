# DARK / NO-GPS TAKEOFF — VERIFIED FROM SCRATCH (WM160 / DJI Fly v1.21.4)

Re-derived directly from the 16 app DEX (baksmali) + apktool `res/`. Prior `reverse_docs`
were **not** trusted; where they were wrong it is called out. Every claim below cites a
class/method/string.

---

## VERDICT (read this first)

**Dark + no-GPS takeoff is UNLOCKABLE, not a hard block. The app itself exposes a user-facing
"Unlock" button for it.** But your two writes were both wrong:

1. **Wrong value/polarity.** The app UNLOCKS by writing the flag **FALSE (0)**, not TRUE (1).
   You wrote `DarkNoGpsLockEnable = 1`, which *enables* the lock — i.e. you re-armed the very
   restriction you were trying to remove. To clear the lock the value must be **0 / FALSE**.
2. **Wrong name for the 0x03/0xF9 path.** `DarkNoGpsLockEnable` is the **KeyValue-SDK key name**,
   not a flyc/`g_config` parameter string. The actual flyc parameter is named
   **`fc_dark_need_gps_0`**. Hashing the literal `"DarkNoGpsLockEnable"` and shipping it via
   `0x03/0xF9 SetParamsByHash` matches no flyc param → silent no-op regardless of value.

It is a **soft flag**, not a physics wall — but the "physics" is real: the app's own dialog
warns the aircraft will take off in **ATTI mode (no hover / it will drift)**, because the Mini's
only positioning sensor is the downward VPS and in the dark it is blind. So "unlockable" here
means "you can force an ATTI-only takeoff that the FC otherwise refuses," not "you get position
hold in the dark."

---

## 1. What `DarkNoGpsLock` / `DarkNoGPSLockOn` / `DarkNoGpsLockEnable` actually is

It is category **(a) — a user-facing setting the app exposes**, surfaced as an "Unlock" button
on the pre-flight checklist. It is *also* internally a flight-controller KeyValue key. There are
three names for one thing:

| Name | Where | Role |
|---|---|---|
| `DarkNoGpsLockEnable` | `uav/sdk/keyvalue/key/UAVFlightControllerKey` field **`J4`** (const-string, line ~20952); `uav/sdk/keyvalue/key/flightcontroller/setting/flightsafety/UAVFlightControllerSettingFlightSafetyKey` field `c` (line 227) | The FC KeyValue key. **Get/Set, Boolean** (`UAVKeyInfoGS`, `BooleanConverter`). ComponentType.c = **FLIGHTCONTROLLER (4)**, SubComponentType.g. |
| `Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn` | `com/uav/flymodel/generated/impl/flight/flylimit/FlyLimitSettingsModelImpl$darkNoGPSLockOn$2->a()` (const-string, line ~97) | The FlyModel path/label wrapping the key as a `ToggleFlySubject`. |
| `fc_dark_need_gps_0` | `uav/midware/data/params/P3/ParamCfgName` field **`Y`** (line 60) | The **flyc/`g_config` parameter string** (snake_case, no `g_config.` prefix — like sibling `forbid_side_fly_0`). This is what 0xF9 would hash. |

Mapping proof (model → FC key, **no inversion**):
- `FlyLimitSettingsModelImpl$darkNoGPSLockOn$2$1` is a function-ref to
  `V1FlyLimitSettingsKt.v1FlyLimitSettingsDarkNoGPSLockOn()` (`= b()`).
- `V1FlyLimitSettingsKt.b()` (lines 183-224): `V1ExtKt.A(Boolean.FALSE, UAVFlightControllerKey.J4)`
  → a `V1FlySubject` over key `J4` ("DarkNoGpsLockEnable"), default **FALSE**.
- `ToggleFlySubject.Q()` (lines 293-343) forwards the set value **unchanged** to the underlying
  `FlySubject.m(value)`. So `model.set(x)` writes FC key `DarkNoGpsLockEnable = x` verbatim.

The app **both reads and writes** it: it is read/observed for UI state and written by the Unlock
action (below). It is **not** engineering-mode-gated and **not** a write-only internal flag.

## 2. What the app does at the dark-no-GPS state — it OFFERS an override, it does not hard-refuse

FC pushes `MotorStartFailedCause = DARK_NEED_GPS`. Verified value:
`DataOsdGetPushCommon$MotorStartFailedCause` `<clinit>` line 5397 — `const/16 v2, 0x93` with
name `"DARK_NEED_GPS"` → **147 decimal = 0x93**. Confirmed exactly your empirical 147.

This maps to HMS/diagnostic code **`0x761f`**:
`com/uav/diagnostic/config/module/FlightControllerCodesKt` line 1054-1057 registers `0x761f` →
`configFlightController$146`, whose builder (line 100/113) sets messages
`fpv_checklist_cannot_fly_dark_no_gps` + capsule `fpv_capsule_cannot_fly_dark_no_gps` at Red level.

The full user-clearable path (all in `com/uav/component/fpv/widget/checklist/selfcheck/`):

1. `SelfCheckMetaMap` (lines 2179-2230) attaches a `SelfCheckActionModel` to code **`0x761f`**
   with button label `fpv_checklist_cannot_fly_dark_no_gps_unlock_btn` ("Unlock") and consumer `r0`.
2. Tap Unlock → `r0.accept` → posts BulletinBoard event **`"set_unlock_dark_need_gps"`**.
3. `SelfCheckVM.d2()` (line 5225→5233) receives it → `g2()`.
4. `SelfCheckVM.g2()` (lines 5774-5875) shows the warning dialog:
   title `fpv_checklist_cannot_fly_dark_no_gps_dialogue_title`, body
   `..._dialogue_content` ("...Aircraft will enter Attitude mode after takeoff and will be unable
   to hover... ensure no person or obstacle within 2 m..."), OK = `..._dialogue_ok_btn` ("Agree"),
   Cancel just dismisses.
5. "Agree" → listener `e2` → `SelfCheckVM.M0` → `G1` → **`h2()`**.
6. `SelfCheckVM.h2()` (lines 6333-6407): `...getFlyLimitSettings().getDarkNoGPSLockOn()` then
   **`.m(Boolean.FALSE)`** — i.e. **sets DarkNoGpsLockEnable = FALSE** — subscribes result `y0`.
7. `SelfCheckVM.I1()` (lines 2404-2472): on `FlyResult.f()==true` shows
   `..._unlock_success_toast` ("Takeoff restrictions unlocked. Fly with caution"); else
   `..._unlock_failed_toast`.

So: the app does **not** refuse app-side. It routes to a confirm dialog and, on Agree, writes the
flag **FALSE** and reports "restrictions unlocked." That "success" semantic is the authoritative
proof that **FALSE = unlocked / TRUE = locked**.

## 3. The exact override mechanism (and why yours failed)

- **Param semantics:** to clear the dark lock, set the flag to **0 / FALSE** (you sent 1 / TRUE).
- **Correct string to hash for the flyc-param (`0x03/0xF9 SetParamsByHash`, `DataFlycSetParams`)
  path:** **`fc_dark_need_gps_0`** (`ParamCfgName.Y`), value **0**. NOT `"DarkNoGpsLockEnable"`.
  (`0x03/0xF9` per `PARAM_WIRE.md`: `DataFlycSetParams`, cmd_set FLYC 0x03, cmd_id 0xF9, payload
  = `hash(name)` u32-LE + value; the FC firmware raised 147 itself, so it owns this param.)
- **What the DJI Fly app actually uses:** the KeyValue-SDK Get/Set on key `J4`
  ("DarkNoGpsLockEnable", Boolean, ComponentType FLIGHTCONTROLLER). The write is dispatched by
  the generic KeyValue→transport layer (much of it native), which I could **not** pin to a single
  DUML cmd_id purely from smali. **Skeptical caveat:** I have *not* proven that the app's Unlock
  literally emits `0x03/0xF9 fc_dark_need_gps_0`; it may use a KeyValue/ability command instead.
  What is proven: (name=DarkNoGpsLockEnable, boolean, set FALSE) goes to the FLIGHTCONTROLLER over
  the link and returns a `FlyResult`. **Recommendation:** Frida-hook the KeyValue set (or sniff the
  link) while tapping the in-app Unlock once to capture the exact cmd_set/cmd_id/payload, rather
  than guessing the hash. If you stay on the 0xF9 route, use `fc_dark_need_gps_0 = 0`.
- Yes, it is sent **over the radio link** to the aircraft FC (it is a flight-controller key/param;
  the result comes back as a `FlyResult`).

**Correction to prior docs:** `CAMERA_AND_NOGPS.md` and `FLIGHT_GATING.md` told you to
"write `DarkNoGpsLockEnable = true`" and `PARAM_HASH.md` hashed the literal `"DarkNoGpsLockEnable"`
(0x59fb7ca9). **Both are wrong**: the value must be **false/0**, and the flyc param string is
**`fc_dark_need_gps_0`**, not the KeyValue key name.

## 4. Is it a hard physics/safety block? — Soft flag, real consequence

**Soft flag, app-overridable — NOT a hard block.** The app ships the override and the FC honors
`DarkNoGpsLockEnable=FALSE` (that is the entire point of the Unlock feature). But the underlying
reason is physical: the Mini/WM160 has only a **downward VPS** and **no GPS-free horizontal hold
without it**. In the dark the VPS is blind → the FC can't hold position → it defaults to
**ATTI** and, as a safety gate, refuses motor start (147) *unless* you clear the flag. The app's
own dialog spells this out: after unlocking it "will enter Attitude mode after takeoff and will be
unable to hover." So clearing the flag doesn't buy you position hold — it buys you a drifting,
ATTI-only takeoff. Consistent with your hardware result: **lights ON → VPS sees ground → position
hold works → no dark lock → arms fine; lights OFF → VPS blind → 147.**

## 5. Which takeoff gates a WM160 owner can actually clear from the UI vs hard/physics

Actionable checklist items that carry a user button (from `SelfCheckMetaMap` button labels):

| Gate | App action | Clearable? |
|---|---|---|
| Dark + no-GPS (147 / 0x761f) | "Unlock" → writes `DarkNoGpsLockEnable=FALSE` (ATTI warning) | **Yes (soft)** — value **0** |
| Compass error/needs cal | `compass_calibration_btn` → calibration flow | Yes (procedure) |
| Aircraft in low-power mode | `..._low_power_consumption_mode_disable_btn` | Yes (toggle) |
| Not logged in / real-name / China verify / eSIM activate | login / `real_name_btn` / `go_verify` / `activate` | Yes (account/activation) |
| US-mode takeoff | `us_mode_takeoff_switch_btn` | Yes (region toggle) |
| Vision positioning | `vision_position_btn_close_btn` (turn VPS off) | Yes (toggle) |

Hard / firmware / physics — **no UI override**, no button attached (other
`MotorStartFailedCause` values in `DataOsdGetPushCommon$MotorStartFailedCause`):
`GPS_DISCONNECT`, `GPS_ABNORMAL`, `GPS_SIGN_INVALID`, `LOST_GPS_IN_POR_A_ERROR`, IMU/gyro/
accelerometer errors, ESC/motor faults, battery-auth/low-voltage, NFZ/GEO (separate unlock
server flow, not a checklist toggle), etc. These are cleared by fixing the condition, not by a flag.

---

### Evidence index (paths under `reverse_docs/scratch_dis/<dex>/`, from `unpacked_app_dex/*.dex`)
- `classes_0451d00c/uav/sdk/keyvalue/key/UAVFlightControllerKey.smali` (J4, "DarkNoGpsLockEnable")
- `classes_0451d00c/uav/sdk/keyvalue/key/flightcontroller/setting/flightsafety/UAVFlightControllerSettingFlightSafetyKey.smali` (GS Boolean, ComponentType.c=FLIGHTCONTROLLER, SubComponentType.g)
- `classes_0451d00c/uav/midware/data/params/P3/ParamCfgName.smali` (Y = `fc_dark_need_gps_0`)
- `classes_0451d00c/uav/midware/data/model/P3/DataOsdGetPushCommon$MotorStartFailedCause.smali` (DARK_NEED_GPS = 0x93 = 147)
- `classes_08fe100c/com/uav/flymodel/handwrite/flight/flylimit/v1/V1FlyLimitSettingsKt.smali` (b(): default FALSE, key J4)
- `classes_08fe100c/com/uav/flymodel/internal/observable/ToggleFlySubject.smali` (Q(): no inversion)
- `classes_08fe100c/com/uav/flymodel/generated/impl/flight/flylimit/FlyLimitSettingsModelImpl$darkNoGPSLockOn$2*.smali`
- `classes_0855200c/com/uav/diagnostic/config/module/FlightControllerCodesKt.smali` (0x761f) + `...$configFlightController$146.smali`
- `classes_07a5000c/com/uav/component/fpv/widget/checklist/selfcheck/SelfCheckVM.smali` (g2 dialog, h2 write FALSE, I1 toasts)
- `classes_07a5000c/com/uav/component/fpv/widget/checklist/selfcheck/SelfCheckMetaMap.smali` (0x761f → Unlock button → r0 → "set_unlock_dark_need_gps")
- `res/values/strings.xml` (dialog/toast text, ids in `res/values/public.xml`)
