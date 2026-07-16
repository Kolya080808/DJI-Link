# CAMERA_AND_NOGPS.md — WM160 (Mavic Mini 1) camera exposure + no-GPS takeoff

Evidence-based reversing of DJI Fly v1.21.4 (fully unpacked). Every numeric value is cited to the
class / smali `<clinit>` / enum init that defines it. Sources:
`decompiled/` (apktool) and `reverse_docs/unpacked_app_dex/` (16 DEX, disassembled here with
`baksmali`), plus `cmdmap.txt` / `CMD_TABLE.txt`.

Device (receiver) addresses — confirmed from `uav/midware/data/config/P3/DeviceType.<clinit>`
(dex `classes_016b200c`): `CAMERA=0x01`, `APP=0x02`, `FLYC=0x03`, `GIMBAL=0x04`.
So `drone.py`'s `DEV_CAMERA=0x01 / DEV_FC=0x03 / DEV_GIMBAL=0x04` are all correct — the receiver was
never the problem. Every legacy camera DUML builder (`DataCameraSetIso.start()`,
`DataCameraSetExposureMode.start()`) sets `Pack.f = APP(0x02)` (sender) and `Pack.h = CAMERA(0x01)`
(receiver), `CmdSet.c` (= camera 0x02). Confirmed.

The camera cmd_ids in `drone.py` are all correct — verified against
`uav/midware/data/config/P3/CmdIdCamera$CmdIdType.<clinit>` (dex `classes_016b200c`):
`SetMode=0x10`, `SetExposureMode=0x1E`, `SetIso=0x2A`, `SetWhiteBalance=0x2C`,
`SetExposureCompensation=0x2E`. **The bug is the PAYLOAD (raw human number instead of the enum
index) and the missing exposure-mode precondition — NOT the cmd_id or the receiver.**

The authoritative wire enums live in `uav/sdk/keyvalue/value/camera/*` (dex `classes_0451d00c`). Each
is a Java enum whose constructor is `<init>(String name, int ordinal, int value)` and whose body is
`iput p3, ...->value:I` — i.e. **the 3rd int is the on-wire `value`, independent of the ordinal.**
These match the canonical DJI DUML camera enums exactly.

---

## GOAL 1 — make ISO / EV / exposure actually work

### (a) 0x02/0x1E `set_camera_exposure_mode` — and the MANUAL precondition

Enum `uav/sdk/keyvalue/value/camera/CameraExposureMode.<clinit>` (dex `classes_0451d00c`):

| Mode | wire `value` |
|---|---|
| `PROGRAM` (full auto) | **0x01** |
| `SHUTTER_PRIORITY` | **0x02** |
| `APERTURE_PRIORITY` | **0x03** |
| `MANUAL` | **0x04** |
| `UNKNOWN` | 0xFF |

Payload layout — legacy builder `uav/midware/data/model/P3/DataCameraSetExposureMode.doPack()`
(dex `classes_0451d00c`): `_sendData = new byte[2]; _sendData[0] = expMode;` and **only if
`expMode == 6`** (a legacy "scene" sub-mode that WM160 does not use) a 2nd `sceneMode` byte is
appended. For WM160 you send a **single byte**:

```
set MANUAL exposure:  cmd_set=0x02 cmd_id=0x1E  receiver=0x01(CAMERA)  payload = [0x04]
set full-AUTO:        cmd_set=0x02 cmd_id=0x1E  receiver=0x01(CAMERA)  payload = [0x01]
```

**Is MANUAL required before ISO / EV take effect? YES — this is exactly why your ISO/EV commands did
nothing.** The app gates ISO and EV on exposure mode:

- **ISO is only settable in a manual/priority mode.** Class
  `com/uav/flymodel/handwrite/camera/exposure/v1/V1ISOStopKt$v1ISOStopSettable$1` combines the
  current `CameraExposureMode` with "ISO not AUTO" to decide `isoSettable`. In `PROGRAM` the camera
  owns ISO and a manual ISO write is ignored. Related gates:
  `com/uav/key/ext/camera/ExposureModeSwitchableState`, string `ISOSettingSettable`,
  key `Camera.Exposure.ISOAuto.settable`. → **To set ISO you MUST first send exposure mode = MANUAL
  (0x04).** (On WM160 the camera has a fixed aperture, so the practical manual mode is MANUAL=0x04;
  SHUTTER_PRIORITY=0x02 also frees ISO on cameras that expose it.)

- **EV compensation is only settable in the NON-manual (auto/semi-auto) modes.**
  `com/uav/flymodel/handwrite/camera/exposure/v1/V1ExposureCompensationKt$v1ExposureCompensationIsInEvMode$1.a(...)`
  returns "in EV mode" only when NOT fully manual (it explicitly branches on
  `CameraExposureMode.PROGRAM` and on ISO/shutter/aperture being AUTO). Key
  `Camera.Exposure.ExposureCompensation.isInEvMode`. → **Setting EV while in MANUAL does nothing;
  put the camera in PROGRAM (0x01) first, then send EV.** ISO-manual and EV-comp are mutually
  exclusive by exposure mode — you cannot have both at once, same as on the real app.

### (b) 0x02/0x2A `set_camera_iso_para` — ISO enum + payload

Enum `uav/sdk/keyvalue/value/camera/CameraISO.<clinit>` (dex `classes_0451d00c`), `value:I`:

| ISO | wire `value` | | ISO | wire `value` |
|---|---|---|---|---|
| `ISO_AUTO` | **0x00** | | `ISO_1600` | **0x07** |
| `ISO_50` | 0x02 | | `ISO_3200` | **0x08** |
| `ISO_100` | **0x03** | | `ISO_6400` | 0x09 |
| `ISO_200` | **0x04** | | `ISO_12800` | 0x0A |
| `ISO_400` | **0x05** | | `ISO_125` | 0x15 |
| `ISO_800` | **0x06** | | `ISO_FIXED` | 0xFF |

(WM160 native range is ISO 100–3200 for photo / 100–3200 video; use AUTO/100/200/400/800/1600/3200.)

Payload layout — `uav/midware/data/model/P3/DataCameraSetIso.doPack()` (dex `classes_0451d00c`):
```
_sendData = new byte[1];
_sendData[0] = (type << 7) | absValue;   // type: 0 = absolute, 1 = relative  → bit7 is the "relative" flag
```
For a normal absolute ISO write `type=0`, so the byte is just the enum value:

```
ISO 400:  cmd_set=0x02 cmd_id=0x2A receiver=0x01  payload = [0x05]
ISO AUTO: cmd_set=0x02 cmd_id=0x2A receiver=0x01  payload = [0x00]
```
**Your bug:** you were sending the literal `400` (0x0190 truncated to 0x90) or `100`. Send **0x05**
for ISO 400, etc. And it only takes effect after exposure mode = MANUAL (see (a)).
(`DataCameraSetIso` uses `CmdIdCamera.G = SetIso = 0x2A` — confirms this 1-byte format IS the
0x02/0x2A command.)

### (c) 0x02/0x2E `set_camera_exposure_compensation` — EV enum + payload

Enum `uav/sdk/keyvalue/value/camera/CameraExposureCompensation.<clinit>` (dex `classes_0451d00c`),
`value:I`. **0 EV = 0x10, and every 1/3-EV step = +1** (your hypothesis was correct):

| EV | wire | EV | wire | EV | wire |
|---|---|---|---|---|---|
| −3.0 | **0x07** | −1.0 | 0x0D | +1.0 | 0x13 |
| −2.7 | 0x08 | −0.7 | 0x0E | +1.3 | 0x14 |
| −2.3 | 0x09 | −0.3 | 0x0F | +1.7 | 0x15 |
| −2.0 | **0x0A** | **0.0** | **0x10** | +2.0 | **0x16** |
| −1.7 | 0x0B | +0.3 | 0x11 | +2.7 | 0x18 |
| −1.3 | 0x0C | +0.7 | 0x12 | +3.0 | **0x19** |

(Full table runs `NEG_5P0EV=0x01 … NEG_0EV=0x10 … POS_5P0EV=0x1F`, `FIXED=0xFF`. WM160 UI range is
−3.0..+3.0 → 0x07..0x19.) Payload = **1 byte = the value**:
```
0 EV:  cmd_set=0x02 cmd_id=0x2E receiver=0x01  payload = [0x10]
+1EV:  payload = [0x13]      −1EV: payload = [0x0D]
```
No dedicated legacy `DataCameraSet...ExposureCompensation` builder exists in the DEX (the modern app
drives 0x2E through the key-value layer), but the 1-byte index is the DUML wire format and the enum
above is authoritative. EV only applies in a non-MANUAL mode — see (a).

### (d) 0x02/0x2C `set_camera_white_balance` — WB enum + payload

Mode enum `uav/sdk/keyvalue/value/camera/CameraWhiteBalanceMode.<clinit>` (dex `classes_0451d00c`),
`value:I`:

| Mode | wire | Mode | wire |
|---|---|---|---|
| `AUTO` | **0x00** | `INDOOR_FLUORESCENT` | 0x05 |
| `SUNNY` | 0x01 | `MANUAL` (custom Kelvin) | **0x06** |
| `CLOUDY` | 0x02 | `NATURAL` | 0x07 |
| `WATER_SURFACE` | 0x03 | `UNDERWATER` | 0x08 |
| `INDOOR_INCANDESCENT` | 0x04 | | |

Composite value type `uav/sdk/keyvalue/value/camera/CameraWhiteBalance` has fields
`mode:CameraWhiteBalanceMode` + `colorTemperature:Integer`. The colour-temperature is passed as a
**raw integer** (no scaling — see
`com/uav/flymodel/handwrite/camera/whitebalance/v1/V1ColorTemperatureKt$v1ColorTemperatureValue$2.a(J)`
which just does `CameraWhiteBalance.setColorTemperature(Integer.valueOf(rawValue))`; the custom range
is exposed via `Camera.WhiteBalance.ColorTemperature.latestAvailableRange`).

Practical payload: `[mode]` for the presets, and `[0x06, colorTempByte]` for custom. **The exact
custom colour-temp byte encoding could NOT be pinned statically** (it flows through the native
key-value codec, no legacy `DataCameraSetWhiteBalance` DUML class exists — only `DataCameraSetWbArea`
for spot-WB). For AUTO just send `payload = [0x00]`, which is all you need on WM160.
→ **Frida:** to capture the custom-Kelvin byte, hook the key-value write of `KeyWhiteBalance`
(`uav/sdk/keyvalue/value/camera/CameraWhiteBalance`) or the raw 0x02/0x2C packet while dragging the
colour-temp slider.

### (e) GIMBAL RECENTER — the correct command for WM160

**Your 0x04/0x4C guess was the RIGHT cmd_id — but the payload was empty/wrong, so nothing happened.**
`cmdmap.txt`: `cmd_set 4, cmd_id 76 (0x4C) = uav_gimbal_set_work_mode_and_return_center`.
(Note the collision that likely misled you: `0x02/0x4C` = `uav_camera_set_video_out_para`, a *camera*
command — but `0x04/0x4C` on the *gimbal* cmd_set is the recenter.)

Builder `uav/midware/data/model/P3/DataGimbalNewResetAndSetMode` (dex `classes_0451d00c`):
- `start()` → sender `APP(0x02)`, receiver `GIMBAL(0x04)`, `CmdSet.e` (gimbal 0x04),
  `CmdIdGimbal.G`. And `CmdIdGimbal$CmdIdType.G = "ResetAndSetMode" = 0x4C` (dex `classes_016b200c`,
  `<clinit>`). ⇒ **cmd_set=0x04, cmd_id=0x4C, receiver=0x04(GIMBAL).**
- `doPack()` builds a **2-byte** payload `[workMode, resetCmd]`:
  - `byte[0]` = `DataGimbalControl$MODE.value()`. Enum values (dex `classes_0451d00c`):
    `YawNoFollow=0x00, FPV=0x01, YawFollow=0x02, OTHER=0xFE`. Default = `OTHER=0xFE` = "keep current
    work mode".
  - `byte[1]` = reset command: **`0x00` = set-mode-only (no reset)**, **`0x01` = recenter (reset
    pitch/yaw to home)**, **`0x03` = reset yaw+pitch** (when `validBothYawAndPitch`, which also forces
    `byte[0]=0xFE`).

So the working recenter is:
```
recenter (keep mode):     cmd_set=0x04 cmd_id=0x4C receiver=0x04  payload = [0xFE, 0x01]
recenter yaw+pitch:       cmd_set=0x04 cmd_id=0x4C receiver=0x04  payload = [0xFE, 0x03]
```
`byte[1]` is literally the high-level `GimbalResetCommand` code —
`uav/sdk/keyvalue/value/gimbal/GimbalResetCommand.<clinit>` (dex `classes_0451d00c`):
`RECENTER=0x01, SELFIE=0x02, PITCH_YAW=0x03, ONLY_PITCH=0x04, ONLY_ROLL=0x05, ONLY_YAW=0x06`.
In the live app the recenter goes through action-key `UAVGimbalKey.d` with `GimbalResetCommand.RECENTER`
(see `com/uav/flymodel/handwrite/gimbal/recenteraction/v1/V1RecenterActionKt$v1RecenterActionReset$1$1`
→ `RxCSDK.F0(UAVGimbalKey.d, GimbalResetCommand)`), which the native layer serializes to exactly the
0x04/0x4C bytes above. **Start with `[0xFE, 0x01]`.**

**Do NOT use 0x04/0x13 for recenter** — `cmd_set 4, cmd_id 19 (0x13) =
uav_gimbal_reset_default_params` (`DataGimbalResetUserParams`) resets user *calibration/tuning*
params, it is not a recenter. Recenter IS a distinct command (0x04/0x4C), not "set angle to 0",
though commanding pitch 0 via 0x04/0x14 is a valid fallback for framing since Mini yaw follows the
aircraft.

---

## GOAL 2 — fly WITHOUT GPS (ATTI takeoff) / the dark-no-GPS block

### What the "allow takeoff without GPS in low light" toggle actually is

Traced end-to-end:
1. Key `Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn`, impl
   `com/uav/flymodel/generated/impl/flight/flylimit/FlyLimitSettingsModelImpl$darkNoGPSLockOn$2`
   (dex `classes_08fe100c`) → builds a `ToggleFlySubject` bound to
   `V1FlyLimitSettingsKt.v1FlyLimitSettingsDarkNoGPSLockOn()` (method `b()`).
2. `com/uav/flymodel/handwrite/flight/flylimit/v1/V1FlyLimitSettingsKt.b()` (dex `classes_08fe100c`):
   ```
   default = Boolean.FALSE
   key     = UAVFlightControllerKey.J4   ("KeyDarkNoGpsLockEnable")
   → V1ExtKt.A(default, key)             // key-value read/write bridge
   ```
3. `UAVFlightControllerKey.J4` definition (`uav/sdk/keyvalue/key/UAVFlightControllerKey.<clinit>`,
   dex `classes_0451d00c`):
   ```
   new UAVKeyInfo(ComponentType.FLIGHTCONTROLLER.b(), SubComponentType.b(),
                  "DarkNoGpsLockEnable", SingleValueConverter.BooleanConverter)
   ```
   `ComponentType.FLIGHTCONTROLLER = 4` (`uav/sdk/keyvalue/key/ComponentType.<clinit>`). Default value
   is `false`; enabling takeoff-without-GPS ⇒ write **`true`** (a single boolean byte 0x01).

**So it is NOT a dedicated FC DUML command and NOT a 0x03/0x2A `FunctionControl` sub-code.** There is
no `DataFlyc*` DUML class for dark/no-GPS anywhere in the 16 DEX (grep of every `DataFlyc*` name).
It is a **flight-controller CONFIG/PARAMETER boolean** named **`DarkNoGpsLockEnable`**, written through
the key-value framework.

### The concrete DUML: hashed FC-parameter write, cmd_set 0x03 / cmd_id 0xF9

The FC-parameter write path is `uav/midware/data/model/P3/DataFlycSetParams` (dex `classes_0451d00c`):
- `start()` → sender `APP(0x02)`, receiver `FLYC(0x03)`, `CmdSet.d` (FC 0x03), and it uses
  `CmdIdFlyc.fd` for the modern path or `CmdIdFlyc.ac` for the legacy one.
- `CmdIdFlyc$CmdIdType.<clinit>` (dex `classes_016b200c`):
  **`fd = "SetParamsByHash" = 0xF9`**, `ac = "SetParamsByIndex" = 0xF2`,
  `Rc = "GetParamInfoByHash" = 0xF7`, `ad = "GetParamsByHash" = 0xF8`,
  `ae = "SetMotorForceDisable" = 0xFE` (your existing 0x03/0xFE).
- `doPack()`: for the modern ("isNew") path the payload per parameter is
  **`hash (4 bytes, little-endian via BytesUtil.o0(J))` followed by the value bytes**
  (here 1 byte boolean). The hash comes from `ParamInfo.hash`, looked up **by parameter NAME string**
  via `UAVFlycParamInfoManager.read(name)`.

```
enable no-GPS takeoff:  cmd_set=0x03 cmd_id=0xF9 receiver=0x03(FLYC)
                        payload = [ hash32("<param name>") LE (4 bytes) , 0x01 ]
```

### The one thing that must be confirmed with Frida (and why)

`UAVFlycParamInfoManager` fills its name→ParamInfo(hash) map from a **bundled table** (a static
`HashMap`, populated from the app's param json). `DarkNoGpsLockEnable` is **NOT** among the 687
entries of `flyc_param_infos.json` (confirmed earlier in `FLIGHT_GATING.md §F`). Therefore:

- The write does **not** flow through the legacy `DataFlycSetParams` builder (it has no bundled hash
  for this name); it flows through the **native key-value/"cyber" transport** (`RxCSDK` →
  `V1ExtKt.A`), which computes/knows the hash internally and emits the same
  **0x03/0xF9 SetParamsByHash** packet (that is the only FC "set boolean config" DUML the firmware
  exposes for un-tabled params).
- **What to capture:** hook while toggling the app's Safety → "Enable/Allow takeoff without GPS in
  low light" switch, and read the outgoing 0x03/0xF9 payload — you need the **exact 4-byte hash** and
  the boolean value. Concretely, hook one of:
  - `uav.midware.data.manager.P3.DataBase.start()` / the P3 send path (raw DUML — cleanest: filter
    `cmd_set==0x03 && cmd_id==0xF9`), **or**
  - `com.uav.rx.csdk.RxCSDK` set-value entry (Frida on the method that takes `UAVFlightControllerKey.J4`
    / key name `"DarkNoGpsLockEnable"`), **or**
  - `uav.midware.util.BytesUtil.o0(J)` to log the `J` hash right before it is serialized.
- **Name string to hash** (feed these to your MITM matcher): key-value name **`DarkNoGpsLockEnable`**,
  full key path `Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn`, alias `KeyDarkNoGpsLockEnable`. The
  FC-internal param name may differ slightly (DJI FC configs are typically dotted, e.g.
  `g_config.xxx...`); the Frida capture of the 4-byte hash removes all ambiguity. Once you have the
  hash, you can replay `[hash4LE, 0x01]` on 0x03/0xF9 directly — no app needed.

There is **no non-hash path** for this setting (no dedicated command, no function-control sub-code);
the only alternatives the firmware offers for a config boolean are 0xF9 by-hash or 0xF2 by-index, and
the app uses by-hash.

### The other FC_CANNOT_TAKE_OFF_* gates (reported at OSD byte +0x33)

Full enum already extracted in `FLIGHT_GATING.md §F` (`FC_CANNOT_TAKE_OFF_*`, decoded in
`diag_codes.py`, 96 codes). Which block motors when GPS is weak, and how to clear each:

| Gate | Clearable? | How |
|---|---|---|
| `DARK_NEED_GPS` / `FC_ONLY_SUPPORT_ATTI_MODE` (dark + weak GPS) | **YES, by setting** | Write `DarkNoGpsLockEnable = true` via 0x03/0xF9 (this section) |
| `NO_GPS_AND_NOVICE` (+0x33 value 10) | **YES, by param** | Novice/beginner mode forces GPS — set `novice_cfg.novice_func_enabled_0` [343] = 0 via 0x03/0xF9 hashed param (this one IS in the 687 table) |
| `IN_FLY_LIMIT_ZONE` / GEO no-fly | partial | Requires unlock/geo; ATTI drift still allowed outside zones |
| `COMPASS_CALIBRATING` / `COMPASS_ERROR` / `COMPASS_DEAD` | cal-clearable / hard | Run compass cali (`FLIGHT_GATING.md §B`); `_DEAD` is firmware-hard (hardware) |
| `IMU_CALIBRATING` / `IMU_INITING` / `IMU_WARMING` / `IMU_BIAS_TOO_LARGE` | wait / cal | Warming & initing self-clear; bias needs IMU cali; not GPS-related |
| `GYROSCOPE_DEAD` / `GYRO_BIAS_TOO_LARGE` / `ACCELEROMETER_DEAD` / `BAROMETER_*` | hard | Sensor hardware — not clearable by command |
| `SERIOUS_LOW_POWER` / `SERIOUS_LOW_VOLTAGE` / `BATTERY_*` | condition | Charge/battery — not a command |
| `NO_ATTI_DATA` / `LARGE_TILT` / `ATTITUDE_LIMIT` | condition | Level the aircraft, wait for atti convergence |
| `GPS_SIGNATURE_INVALID` | hard-ish | GPS module integrity; ATTI still possible once dark-lock cleared |
| `DEVICE_LOCKED` / `HARDWARE_LOCK_MOTOR` | hard | Firmware/security lock |
| `FORCE_DISABLE` | **YES, by you** | Your own 0x03/0xFE `SetMotorForceDisable` — send disable=off |
| `DRONE_NOT_ACTIVATED` | activation | See `FLIGHT_GATING.md §A` |
| `GIMBAL_CALIBRATING` / `ESC_*` / `NAVIGATION_SYSTEM_DISCONNECTED` / `DRONE_UPGRADING` | wait/cal/hard | Transient or hardware |
| `REMOTE_CONTROLLER_NEED_CALIBRATION` / `_MIDDLE_LARGE` / `_MAPPING_EXCEPTION` | cal | RC stick calibration (not relevant to a PC-stick setup that sends neutral) |

### Bottom line for ATTI/no-GPS takeoff on WM160
1. Ensure NOT novice: `novice_cfg.novice_func_enabled_0` [343] = 0 (0x03/0xF9, param IS in table).
2. Clear dark-lock: `DarkNoGpsLockEnable = true` (0x03/0xF9, hash captured via Frida per above).
3. There is **no separate "force ATTI" command** — the FC enters ATTI automatically when GPS is lost
   (`FLYC_STATE = 1/ATTI`). Virtual-stick / joystick flight works in ATTI but drifts.
   `isSupportStartWithoutGPS` / `KeyIsQuickShotSupportStartWithoutGPS` confirm the Mini allows
   GPS-less start once the gates above are cleared.

Reference for your motor/takeoff commands (`DataFlycFunctionControl`, cmd_set 0x03 /
**cmd_id 0x2A = FunctionControl**, 1-byte `FLYC_COMMAND.value`, dex `classes_0451d00c`/`016b200c`):
`AUTO_LANDING=0x02, GOHOME=0x06, START_MOTOR=0x07, STOP_MOTOR=0x08, ForceLanding=0x1E,
PRECISION_TAKE_OFF=0x22` — confirms your `force_land` uses 0x1E correctly.
