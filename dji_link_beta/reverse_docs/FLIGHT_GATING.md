# FLIGHT_GATING — what a PC ground-station must do to fly a Mavic Mini 1 (WM160)

Evidence-based reverse of DJI Fly `dji.go.v5` v1.21.4 (`reverse_docs/unpacked_app_dex/*.dex`,
16 DEX, 128 k classes) + `flyc_param_infos.json` (687 FC params) + `reverse_docs/full_table.txt`
(DUML command table) + native-lib findings in `FINDINGS.md`. Every claim cites its source.

## How the citations work (DUML build layer)

The app's DUML builders are the `uav/midware/data/model/P3/Data*` classes. Each has a `doPack()`
(payload bytes) and a `start()` that sets sender/receiver/cmd_set/cmd_id. I resolved the obfuscated
enum letters from `uav/midware/data/config/P3/CmdSet.smali` and `CmdIdFlyc$CmdIdType.smali`
(disassembled with baksmali). Confirmed enum values:

- `CmdSet` (arg-3 of `<init>` = the cmd_set byte):
  `a=0x00 COMMON, b=0x01 SPECIAL, c=0x02 CAMERA, d=0x03 FLYC, e=0x04 GIMBAL, f=0x05 CENTER,
  g=0x06 RC, h=0x07 WIFI, i=0x08 DM368, j=0x09 OSD, k=0x0A EYE, l=0x0B SIMULATOR,
  n=0x0D SMARTBATTERY, o=0x11 ADS_B` (source: `CmdSet.smali <clinit>`).
- `DeviceType`: APP=0x0a (sender), FLYC=0x03, CAMERA=0x01, GIMBAL=0x04, RC=0x06 — matches the DUML
  address table in `MASTER_REPORT.md §2.2`.
- Frame layout, CRCs, addressing: `MASTER_REPORT.md §2.2` / `duml.py` (already implemented).

All commands below: **sender = 0x0a (APP/PC), cmd_type attr = 0x40 for a req-with-ack** (from
`DataConfig$CMDTYPE.a` / `NEEDACK.a` in each `start()`), payloads little-endian.

---

## A. LOGIN / ACCOUNT — is it required for motors/takeoff?

### The gate is real but it is a FC-firmware ACTIVATION gate, not a per-flight login.

**(a) HARD gate in the aircraft firmware — activation.** The FC's own "cannot take off" reason enum
contains `FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED` (strings in all DEX; sibling of the other
`FC_CANNOT_TAKE_OFF_*` codes). This reason is reported by the FC in the OSD push at **byte +0x33**
(the motor-start-refusal byte, `telemetry.py`/`diag_codes.py`, `MASTER_REPORT.md §6`). So the FC
firmware itself refuses to start motors on an un-activated aircraft — the PC cannot bypass this in
software. Corroborated by `error_account_user_not_activated_313`, `home_account_not_activated`.

**Activation is a ONE-TIME cloud procedure, not needed per flight.** The flow:

1. App queries FC activation state — `DataFlycActiveStatus` → `cmd_set 0x00 (CmdSet.a) / CmdIdCommon`
   (it uses the COMMON set, timeout const `0x3e8`=1000 ms). Also `DataFlycGetPushActiveRequest`
   (FC → app "please activate").
2. If un-activated, the app performs cloud activation (this is where a **logged-in DJI account +
   internet** are required) via `0x00/0x32 uav_general_activate_device_req`
   (`full_table.txt` 0x00/0x32).
3. App reports the result back to the FC: **`DataFlycSetActiveResult` → `cmd_set 0x03 / cmd_id 0x62`
   (CmdIdFlyc.U), receiver FLYC**, 44-byte payload:
   `[0..3] UAVActivationState u32 | [4..7] u32 | [8..11] u32 | [12..43] 32-byte string`
   (`doPack`: three `BytesUtil.z(I)` u32 + one `BytesUtil.r0(String)` 32-byte).
   `UAVActivationState` enum: `Success=0, NoNetwork=1, InvalidId=2, FailedForNet=3, OTHER=100`.

Once the FC records activation it **persists on the aircraft** — the app's `getIsActivated`/
`isActivated` reads FC state, it does not re-authenticate each flight.

**(b) SOFT gate the app imposes on itself.** `ActivationModelImpl.needActivateDevices`
(`com/uav/flymodel/generated/impl/business/activation/ActivationModelImpl`), the
`showNotActivateRemindDlg` / `hideNotActivateRemindDlg` dialog, `homepage_drone_not_activated_dialogue_*`,
and `in fpv DRONE_NOT_ACTIVATED got, exit fpv` — the app blocks its OWN flight UI until activated.
A PC ground-station simply does not run this check.

**(c) Login needed only for GEO/NFZ unlock** — licence-based, tied to the drone SN, signed by DJI
certs (`FINDINGS.md §8`, `libFRCorkscrew`). Not required for motor start on an activated WM160
(<250 g → mostly advisory zones).

### Bottom line for the PC
- If the WM160 is **already activated** (any prior use with the app), **no account login is needed to
  start motors / take off.** No DUML "login" is sent per flight — there is none.
- The only account-gated event is the **one-time activation** (0x00/0x32 + 0x03/0x62). If your
  aircraft is factory-fresh, you must activate once with the real app/account online.
- **Runtime check to settle it on your unit:** read OSD byte +0x33; if it is the code that maps to
  `DRONE_NOT_ACTIVATED`, activation is missing. (`diag_codes.py` `MOTOR_NOT_START`.)

---

## B. CALIBRATION

DJI Fly v5 drives calibration through KeyManager keys, so the low-level DUML is partly behind the
generated key layer. What is statically pinned:

### Compass / IMU calibration (FLYC) — via the function-control command
The FC function-control command `0x03/0x2A` (`DataFlycFunctionControl`, cmd_id `CmdIdFlyc.p=0x2A`,
1 payload byte = the `FLYC_COMMAND` enum) contains a calibration trigger:
`FLYC_COMMAND.Calibration = 0x09`, plus `MASS_CENTER_CALI=0x36`, `EXIT_MASS_CENTER_CALI=0x37`,
`OAR_PANEL_CALI=0x2F`, `DropCalibration=0x15` (full enum in §C table below).
So: **send `55 … 03 2A 09`** to start the FC calibration routine. Payload layout of 0x03/2A:
`[0] = FLYC_COMMAND` (a 2-byte variant `[cmd][extra]` also exists in `doPack`).

KeyManager keys that map onto this (strings): `Flight.FlightSensor.Compass.StartCompassCalibration`,
`Flight.FlightSensor.Compass.StopCompassCalibration`, `Flight.SelfCalibration.CaliStatus.calibrationInfo`,
`KeyTimelapseCompassCalibrationStatus`. IMU cali UI: `IMUCalibrationVM`,
`com.uav.productconfig.imucalibration.generate`, `fpv_setting_safe_IMU_calibration_btn`,
`IMUCalibrationShell undefined stage!`.

**Progress / completion / failure come back as FC pushes**, not as the ACK:
- `DataFlycGetPushCheckStatus` — pre-flight sensor check status.
- `DataFlycGetPushMassCenterCaliStatus` — mass-center cali status.
- `DataFlycGetPushFlycInstallError` — install/orientation error.
- During cali the FC reports `FC_CANNOT_TAKE_OFF_COMPASS_CALIBRATING` / `_IMU_CALIBRATING` /
  `_ESC_CALIBRATING` at OSD +0x33 (so you can see "calibration in progress" live).

**How to know calibration is REQUIRED:** OSD +0x33 returns
`IMU_NEED_CALIBRATION` (value 5 → code 30055), `COMPASS_ERROR` (value 1), `COMPASS_CALIBRATING`
(value 8), `IMU_PREHEATING` (value 7) etc. — the exact value→text chain is already in
`diag_codes.py` (`MOTOR_FAIL_NAME`, `MOTOR_NOT_START`). Also the `IMUFailureReason` /
`isCompassCalibrationNecessary...` push.

### Gimbal calibration — fully pinned
`DataGimbalAutoCalibration` → **`cmd_set 0x04 (GIMBAL) / cmd_id 0x08` (CmdIdGimbal.d), receiver
GIMBAL (0x04)**. Status back via `DataGimbalGetPushAutoCalibrationStatus`.
(Advanced gimbal cali is also exposed in the app's hidden Developer Options, `FINDINGS.md §7`.)

### RC calibration
`0x06/0x03 uav_rc_calibrate_channels_req` (`full_table.txt`); state via
`DataRcGetFDRcCalibrationState`. Not needed for PC virtual-stick flight.

**Undecidable statically:** whether `Compass.StartCompassCalibration` emits `0x03/2A/09` or a distinct
key-only DUML. **Frida hook** `uav/midware/data/manager/P3/DataBase->start(...)` (or the native
`native_rcDataDeal`/DUML send in `libsdk_jni`) while pressing "Calibrate compass" in the app settles
it in one capture.

---

## C. FLIGHT MODES (Normal / Cine / Sport)

**There is NO single clean "set flight mode" DUML in the midware layer.** On WM160 the mode is a
combination of (1) the RC's physical gear switch and (2) a set of FC control-gain parameters. Evidence:

- Reported in telemetry: key `Flight.FlightOSD.FlightMode.Value` and `KeyFlightMode`; enum
  `uav/component/device/flightmode/UAVFlightMode`. In the OSD push the mode is **byte +0x1e & 0x7F**
  (`FLYC_STATE` in `diag_codes.py`: e.g. 6=GPS_ATTI, 17=JOYSTICK). This is the "how it is reported"
  answer.
- Selecting Normal/Sport/Cine changes **control-gain FC params** (all in `flyc_param_infos.json`,
  hash-writable via 0x03/0xF9 — see §E), e.g.:
  `mode_sport_cfg_vert_vel_up_0` [999], `g_config.control.vert_up_vel_0` [318] def5/max10,
  `g_config.control.horiz_vel_atti_range_0` [312], and the app's
  `KeyNormalModeMaxHorizontalSpeedRange` / `KeySportModeAdaptiveSpeedControlOnFC` /
  `v1ControlGainSettingMaxHorizontalSpeedInSportMode` (strings). `isSupportGearsSwitch` = RC gear.
- Cine = a "CineSmooth" gain profile (lower max speeds + softer expo), not a distinct FC state.

**For a PC ground-station this mostly does not matter:** you fly with virtual sticks (§H), which put
the FC in `JOYSTICK` mode (FLYC_STATE 17) regardless of the Normal/Sport gear. If you want
Cine-style smoothness, lower `input_pitch_limit`/`input_roll_limit`/`input_yaw_rate_limit`
(params 363–365) or just scale your stick output. If you want Sport-level top speed you must raise
the control-gain params (0x03/0xF9, hashed).

**Undecidable statically:** whether the app has a dedicated "sport toggle" DUML behind
`KeySportMode...`. Frida hook the KeyManager `setValue` for `KeyFlightMode` to confirm — but the
gain-param evidence strongly indicates there is no clean single command on the Mini.

---

## D. HOME POINT + RTH ALTITUDE

### Two equivalent ways to set/refresh home.

**1. Via function-control `0x03/0x2A`** (`DataFlycFunctionControl`, 1-byte payload = FLYC_COMMAND):
- `HOMEPOINT_NOW = 0x03` → set home to the **aircraft's current position** ("refresh home").
- `HOMEPOINT_HOT = 0x04` → home = hot point.
- `HOMEPOINT_LOC = 0x05` → home = RC/operator location.
- `DynamicHomePointOpen = 0x0F` / `DynamicHomePointClose = 0x10` → dynamic home follow.
  So `55 … 03 2A 03` = "set home here now".

**2. Via the dedicated set-home command `0x03/0x31`** (`DataFlycSetHomePoint`, cmd_id `CmdIdFlyc.w=0x31`,
receiver FLYC) — lets you set an **arbitrary lat/lon**. 18-byte payload (`doPack`, verified):
```
[0]      home_type   u8   (HOMETYPE enum, default RC)
[1..8]   latitude    f64 LE   (RADIANS — BytesUtil.x(D), same convention as OSD lat/lon)
[9..16]  longitude   f64 LE   (RADIANS)
[17]     interval    u8
```
Units confirmed radians: home/drone lat/lon are radians in telemetry (`MASTER_REPORT.md §5`),
and `BytesUtil.x(D)` = `doubleToLongBits`→little-endian bytes.

### RTH altitude — this is a parameter, not a command.
`go_home.fixed_go_home_altitude` param [212] def=20, **min 20, max 500** (metres)
(`flyc_param_infos.json`). Set it with the hash param-write `0x03/0xF9` (§E) — there is no dedicated
non-hash "set RTH altitude" command. Related params: `go_home.go_home_method_0` [211],
`go_home.go_home_heading_option_0` [213], `advanced_function.one_key_go_home_enabled_0` [201],
`go_home.avoid_enable_0` [1380].

### Read back
- Home coords: from the OSD push (home lat/lon f64 radians @ offsets 0x00/0x08, `MASTER_REPORT.md §5`).
- RTH altitude & limits: `DataFlycGetLimits 0x03/0x2E` / `DataFlycGetParamsByHash 0x03/0xF8`.
- Fail-safe/RTH behaviour: `0x03/0x3B set_fail_safe_action` / `0x03/0x3C get_fail_safe_action`
  (`full_table.txt`; `DataFlycSetFsAction`/`GetFsAction`).

---

## E. MAX ALTITUDE / DISTANCE / SPEED — is the hash avoidable?

### DEFINITIVE ANSWER
- **Max altitude — YES, settable WITHOUT the hash.**
- **Max distance (radius) — YES, settable WITHOUT the hash.**
- **Max speed — NO clean non-hash command; needs the hashed param write (or a flight-mode gain).**

### The non-hash command: `DataFlycSetLimits` = `0x03/0x2D`
`DataFlycSetLimits` → **`cmd_set 0x03 (FLYC) / cmd_id 0x2D` (CmdIdFlyc.s), receiver FLYC**.
3-byte payload (`doPack`, verified):
```
[0]     mode   u8   (DataFlycGetLimits$MODE:  High=1, Far=2, Low=3, OTHER=0x64)
[1..2]  value  u16 LE   (BytesUtil.n0 = little-endian; plain metres, no hash)
```
- `mode=High (1)` → **max flight height in metres** (`value`). FC clamps to
  `flying_limit.max_height` range **15…500** (param [236], def 120).
- `mode=Far  (2)` → **max radius/distance in metres**. FC range **15…5000** (param [235] max_radius).
- `mode=Low  (3)` → min height.
Read-back: `DataFlycGetLimits 0x03/0x2E` (CmdIdFlyc.t), 1-byte payload `[mode]`.

So to raise the ceiling to DJI's own 500 m cap: `55 … 03 2D  01  F4 01` (mode High, value 500).
No hash needed. (This is the legacy P3 "set limits" command; the modern app also mirrors it through
`KeyLimitMaxFlightHeightInMeter`. The one residual risk — whether WM160 firmware still honours 0x2D
vs demanding the hashed `flying_limit.max_height` write — is a HW check; the app ships 0x2D, so it is
the intended non-hash path. Confirm by sending 0x2D then reading back with 0x2E.)

### The generic param path is hash-based (unavoidable for arbitrary params)
Resolved from the builders:
- `0x03/0xF7` `get_cfg_item_info_by_hash` — `DataFlycGetParamInfoByHash` (CmdIdFlyc.Rc).
- `0x03/0xF8` `read_hash_param` — read value by hash (CmdIdFlyc.ad).
- `0x03/0xF9` `set_write_hash_param` — **`DataFlycSetParams` (CmdIdFlyc.fd)** — even the generic
  "SetParams" writes by hash.
- `0x03/0xFA` `reset_cfg_item_by_hash` — `DataFlycResetParams` (CmdIdFlyc.id).

There is **no name-based (plain-string) param write** and **no index-based param write** on WM160 —
only `...ByIndex`/`...ByHash` *push/read* variants exist (`DataFlycGetPushParamsByIndex`,
`DataFlycGetPushParamsByHash`); the write is hash-only. So for **speed** (which has no dedicated
command) you must use 0x03/0xF9 with the hash of e.g. `g_config.control.vert_up_vel_0`,
`serial_api_cfg.input_*_limit`, or the sport-mode gain params.

### Is the hash truly unavoidable for speed?
The hash is computed from the param name and is generated behind the packer (`MASTER_REPORT.md §7`) —
**not obtainable statically**. TWO ways to get it without solving the algorithm:
1. **Read the FC config file** — the aircraft can return a param table that includes the name↔hash
   mapping; the app has `DataCommonGetCfgFile` (`0x00` common) and `0x03/0xF7 get_cfg_item_info_by_hash`.
   A dump of the FC's own config gives you (name→hash) for every param.
2. **Frida** hook of `DataFlycSetParams.start()` / the native DUML writer while changing "Max Altitude"
   or a speed slider in the app → capture the 4-byte hash on the wire, then reuse it.
Either yields the live (name→hash) table; after that all 687 params (including speed/gains) are
writable via 0x03/0xF9.

### Relevant params (units, `flyc_param_infos.json`)
```
[236] flying_limit.max_height        def120 min15  max500     ← also set via 0x2D High
[235] flying_limit.max_radius        def30  min15  max5000    ← also set via 0x2D Far
[207] advanced_function.radius_limit_enabled  def0 (0=no distance limit)
[205] advanced_function.height_limit_enabled  def1 min1 max2  (CANNOT be 0 → height limit always on)
[212] go_home.fixed_go_home_altitude def20  min20  max500     (RTH alt, hash-write)
[318] control.vert_up_vel def5 max10 / [319] vert_down_vel def4 max10  (climb/descend speed, gains)
[363] serial_api_cfg.input_pitch_limit def3500 max6000  \
[364] serial_api_cfg.input_roll_limit  def3500 max6000   } virtual-stick input clamps
[365] serial_api_cfg.input_yaw_rate_limit def15000 max15000 |
[366] serial_api_cfg.input_vertical_velocity_limit def600 max600 /
[362] serial_api_cfg.advance_function_enable def0 min0 max1  ← possible external-control gate (§H)
```

---

## F. WEAK-GPS / ATTI takeoff and the "cannot take off" gates

### The weak-GPS-in-dark block is a togglable FC setting, exposed as a KEY (not in the 687-param table)
The FC refuses takeoff in low light without GPS: reason `FC_CANNOT_TAKE_OFF_DARK_NEED_GPS` /
`DARK_NEED_GPS` / `FC_ONLY_SUPPORT_ATTI_MODE` (OSD +0x33). The override is
**`DarkNoGpsLockEnable`** (strings: `KeyDarkNoGpsLockEnable`, key
`Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn`, impl
`com/uav/flymodel/generated/impl/flight/flylimit/FlyLimitSettingsModelImpl$darkNoGPSLockOn`, with an
unlock flow `fpv_basic_flight_cannot_fly_dark_no_gps_unlock_success_toast`).

**Correction to our prior note (`diag_codes.py`):** `DarkNoGpsLockEnable` is **NOT** one of the 687
entries in `flyc_param_infos.json` (a `/dark/` scan returns nothing). It is a KeyManager-backed FC
setting. So "write DarkNoGpsLockEnable=false via hashed param" is not confirmed — the actual write is
whatever DUML the `darkNoGPSLockOn` key emits. Most likely it is either the hashed param write
(0x03/0xF9 on a name we don't have) or a dedicated FLYC setting write.
**This is the exact thing to settle with Frida:** hook `DataBase.start()` while toggling the "Fly in
low-light without GPS" switch in the app's Safety settings → capture the cmd_id + payload.

There is no evidence of a separate non-param "force ATTI" command; ATTI is entered automatically by
the FC when GPS is lost (FLYC_STATE 1/ATTI), and virtual-stick flight in ATTI works but drifts.

### The full "cannot take off" gate list (FC firmware, reported at OSD +0x33)
Complete `FC_CANNOT_TAKE_OFF_*` enum extracted (strings, all DEX) — the ones a PC must handle:
`DRONE_NOT_ACTIVATED` (§A), `DARK_NEED_GPS` (this section), `COMPASS_CALIBRATING`/`COMPASS_ERROR`/
`COMPASS_DEAD`, `IMU_CALIBRATING`/`IMU_INITING`/`IMU_WARMING`/`IMU_BIAS_TOO_LARGE`,
`GYROSCOPE_DEAD`/`GYRO_BIAS_TOO_LARGE`, `ACCELEROMETER_DEAD`, `BAROMETER_DEAD/NEGATIVE/NOISE`,
`SERIOUS_LOW_POWER`/`SERIOUS_LOW_VOLTAGE`/`BATTERY_*`, `DEVICE_LOCKED`, `FORCE_DISABLE` (your own
0x03/0xFE), `IN_FLY_LIMIT_ZONE`, `NO_ATTI_DATA`, `LARGE_TILT`/`ATTITUDE_LIMIT`,
`GPS_SIGNATURE_INVALID`, `ESC_*`, `GIMBAL_CALIBRATING`, `HARDWARE_LOCK_MOTOR`, `APP_NOT_ALLOW`,
`REMOTE_CONTROLLER_NEED_CALIBRATION`/`_MIDDLE_LARGE`/`_MAPPING_EXCEPTION`,
`NAVIGATION_SYSTEM_DISCONNECTED`, `DRONE_UPGRADING`, `FIRST_WARNING`. The numeric value→text mapping
for byte +0x33 is already in `diag_codes.py` (`MOTOR_NOT_START`, 96 codes). Read this byte to know
exactly why motors won't spin.

### To take off with no/weak GPS
1. Ensure not in dark-lock (toggle `DarkNoGpsLockEnable`, above); 2. accept ATTI drift;
3. `novice_cfg.novice_func_enabled_0` [343] must be 0 (novice mode forces GPS — reason
`NO_GPS_AND_NOVICE`, +0x33 value 10). `isSupportStartWithoutGPS` / `KeyIsQuickShotSupportStartWithoutGPS`
confirm the Mini supports GPS-less start.

---

## G. MISSING FEATURES vs `drone.py`

Model gating first: WM160 = UAV59, ProductType 0x3b. `supportNavigationMode=false` for UAV59
(`FINDINGS.md`, `MASTER_REPORT.md §8`) ⇒ anything needing vision/nav is rejected by the aircraft.

| Feature | On WM160? | How to trigger / evidence |
|---|---|---|
| **QuickShots (Dronie/Rocket/Circle/Helix/Boomerang)** | **YES (GPS-based)** | Handled by `com/uav/flymodel/handwrite/camera/quickshot/v1/*`; keys `Camera.QuickShot.QuickShotControl.StartTriggerable/StopTriggerable`, config keys `QuickShotConfig.RocketModeHeight/ScrewModeRadius/RotateFlightSpeed/FlightDirection`. Runs on the FLYC via GPS (`Camera.QuickShot.IsQuickShotMclByFly`, `FlyMcl.IsDroneUsingGpsAssistant`) — NOT vision. DUML surface = vision set **0x0A**: `set_common_ctrl 0x0A/0x27`, `set_action_cmd 0x0A/0x4A`. Exact trigger byte is behind the key layer → Frida on `DataBase.start()` while launching a QuickShot. `drone.py` lacks all of these. |
| **MasterShot** | check | `isSupportMasterShot`; `mastershot_set_param 0x0A/0xF6`, `multi_target_mastershot 0x0A/0xF9`. Uses tracking → likely NOT on Mini 1 (no vision). Verify via `isSupport` at runtime. |
| **Panorama** | **YES** | `isSupportCapturePanorama`, `isSupportPanoStatic/PanoZoomStatic`; `uav_camera_set_pano_mode 0x02/0x6E`; vision `set_pano_control 0x0A/0x3E`; `DataEyeSetPanoramaEnabled`, `free_pano_cap_area_info 0x0A/0x1B`. `drone.py` lacks it. |
| **Timelapse / Hyperlapse** | **YES (`isSupportHyperLapse`)** | Camera `set_timelapse_para 0x02/0x4A`; vision timelapse suite `0x0A/0x74,0x76,0x78,0x7A,0x7B,0x7C`; `DataEyeSetTimeLapseAction`/`SetTimeLapseSubMode`. `drone.py` lacks it. |
| **CineSmooth** | **YES** | A gain profile, not a command — see §C (lower input limits / gains). |
| **Tripod mode** | likely NO on Mini 1 | MSDK marks tripod as an "if supported" feature; no Mini-specific tripod DUML found. Selectable speeds are gains (§C). |
| **Beginner / Novice mode** | **YES** | `novice_cfg.novice_func_enabled_0` [343] (0x03/0xF9 hashed), caps height 30 m/radius 30 m ([344]/[345]). |
| **Geofence radius (max distance)** | **YES** | `0x03/0x2D Far` (§E) + `advanced_function.radius_limit_enabled` [207]. |
| **RTH settings** | **YES** | RTH alt param [212]; `set_fail_safe_action 0x03/0x3B`; home cmds §D. `drone.py` has RTH trigger only. |
| **Precision landing** | check | `DataEyeGetPushPreciseLandingEnergy` exists (downward vision). `PRECISION_TAKE_OFF=0x22` in FLYC_COMMAND (0x03/2A). Downward-vision only → probably works; verify. |
| **POI / Orbit (Point of Interest)** | **NO (needs tracking/vision)** | `DataEyeSetPOIAction/SetPOIParams/SetPOIInitialTarget`, vision `poi_init_target 0x0A/0xC1`, `poi_set_param 0x0A/0xC4`. Vision-based ⇒ rejected on UAV59. |
| **ActiveTrack / Follow-Me / Spotlight** | **NOT SUPPORTED on Mini 1** | No obstacle/tracking sensors; `supportNavigationMode=false`. Vision tracking cmds exist (`set_tracking_select 0x0A/0x20`, `SetTrackingTarget 0x0A/0x94`, `DataFlycStartFollowMeWithInfo`) but the aircraft rejects them. MSDK v4 docs concur: Mini not a supported product for ActiveTrack (see §H sources). **Confirmed NOT supported.** |
| **Waypoint / WPMZ missions** | **NOT SUPPORTED** | `0x22 fc2` / `libwpmz_jni` exist but WM160 has no waypoint hardware; MSDK release notes + DJI forum staff confirm no Waypoint on Mini. `full_table.txt` 0x22/0xAB start_wpmz will be rejected. |
| **Obstacle avoidance** | **NO** | No sensors (`avoid_obstacle_enable` params exist but no hardware). |
| **Find-my-drone / LED / beeper** | YES | `0x00/0x12 find_uav`; `0x03/0xBC-0xBE fmu led`. `drone.py` lacks it. |

Camera items `drone.py` already has (photo/record/mode/zoom/ISO/EV/WB/format/codec) are correct per
`CMD_TABLE.txt`; missing camera extras: metering `0x02/0x22`, focus `0x02/0x24/0x30/0x32`,
sharpness/contrast/saturation `0x02/0x38/0x3A/0x3C`, AE-lock `0x02/0x68`, pano `0x02/0x6E`,
format-SD `0x02/0x72`.

---

## H. VIRTUAL STICK — which command, and the preconditions

### Three stick encodings exist in the app; pick per the reconciliation below.
1. **`0x01/0x0A` special-TLV** (`uav_special_SPECIAL_TLV_CMD`) — 4×11-bit packed, our `drone.py`
   `set_sticks`. This is the RC-emulation channel the native `libGroudStation native_rcDataDeal`
   obfuscates (`FINDINGS.md §6`).
2. **`0x01/0x02` mobile-RC joystick** (`uav_action_virtual_rc_joystick`) — 13-byte, 4×11-bit + flags.
3. **`0x03/0x8E` FLYC joystick** (`DataFlycJoystick`, cmd_id `CmdIdFlyc.v2=0x8E`, receiver FLYC) —
   NEW finding. 17-byte payload (`doPack`, verified):
   ```
   [0]      flag    u8
   [1..4]   roll    f32 LE (BytesUtil.y = floatToIntBits→LE)
   [5..8]   pitch   f32 LE
   [9..12]  yaw     f32 LE
   [13..16] throttle f32 LE
   ```
   This is the "advanced/float" joystick (physical-unit values, matching MSDK's
   angle/velocity semantics) and is referenced only from its own class — likely the
   handheld/legacy API path.

### Preconditions in the app evidence
- Control authority: `0x49/0x80 uav_sdk_get_or_release_control_auth` (1 byte 1=take/0=release) —
  `full_table.txt` (the ONLY command in set 0x49). Our `request_control`.
- Ground-station mode: `0x03/0x80 uav_fc_set_ground_station_on_off` (1 byte) — `full_table.txt`.
- Arbitration if ignored: `0x19/0x40 lock_right_of_control`, `0x19/0x41 preempt_right_of_control`,
  `0x19/0x46 set_task_occupy_control`; `0x06/0xF1 uav_rc_set_app_to_pc_control`.
- Possible FC gate: `serial_api_cfg.advance_function_enable` [362] def0 (0x03/0xF9 hashed) —
  unverified whether the FC needs =1 before it accepts external sticks.

### Reconciliation with DJI MSDK v4 public docs (web-verified)
From developer.dji.com (FlightController component guide + `DJIFlightController`/
`DJIVirtualStickFlightControlData` reference):
- **Mavic Mini virtual stick is officially supported, added in MSDK 4.13 (27 Jul 2020, fw 01.00.0500)**
  — our app-side "since 4.13" note is correct.
  (https://developer.dji.com/mobile-sdk-v4/downloads/, forum thread-221359)
- Documented preconditions: **`setVirtualStickModeEnabled(true)` first**; no waypoint/hotpoint/
  follow-me mission running; flight-orientation mode = AircraftHeading; terrain-follow & tripod
  disabled if supported. `setVirtualStickAdvancedModeEnabled` is **optional** (adds wind
  compensation), not required.
- Default control modes (reset on FC reconnect): Roll/Pitch **Angle** (±30°) or Velocity (±15 m/s);
  Yaw **Angle** (±180°) or AngularVelocity (deg/s, ~±100 unverified); Vertical **Velocity** (~±4 m/s)
  or Position (m); coordinate system **Ground**. → this maps directly onto the `0x03/0x8E` float
  joystick's roll/pitch/yaw/throttle f32 fields.
- **Send at 5–25 Hz; if you stop, the aircraft treats the link as broken and hovers** (verbatim
  MSDK docs). Matches our ~10–25 Hz plan.
- No documented RC "P-mode" requirement (the Mini RC has no mode switch).
- MSDK confirms Mini does **NOT** support Waypoint / ActiveTrack / obstacle avoidance (release notes +
  DJI forum staff; the developer.dji.com feature matrix is stale/pre-Mini, so those rest on release
  notes, not the matrix).

### Which command to actually send — and what is undecidable
`setVirtualStickModeEnabled(true)` in MSDK ≈ our **`0x03/0x80` ground-station on** (the FC "listen to
external control" toggle), and `sendVirtualStickFlightControlData` is one of the three encodings
above. The MSDK's physical-unit (angle/velocity) model matches the **`0x03/0x8E` float joystick**
byte-for-byte, which is the strongest single candidate for the modern path; the **`0x01/0x0A` TLV**
is the RC-emulation path. **Which one the WM160 FC actually accepts is genuinely undecidable
statically** and is the #1 HW open item.

**Frida to settle it (one capture):** hook `uav/midware/data/manager/P3/DataBase->start(...)` AND the
native DUML writer in `libsdk_jni` (the `native_rcDataDeal` / send symbol from `libGroudStation`);
push the sticks in the app with the aircraft connected; observe which cmd_set/cmd_id carries the
4-channel data at 5–25 Hz and whether `0x03/0x80` + `0x49/0x80` (and possibly
`serial_api_cfg.advance_function_enable=1`) precede it. That single trace resolves H, and the
DarkNoGps (§F) and calibration (§B) key mappings if you toggle those in the same session.

### Recommended PC sequence (implement, then confirm the stick variant on HW)
```
1. 0x49/0x80  01            request control auth              (ACK)
   (if ignored) 0x06/F1 01  RC→PC   and/or  0x19/41 01 preempt
2. 0x03/0x80  01            ground-station / external-control ON   (ACK)
   (optional) 0x03/0xF9 write serial_api_cfg.advance_function_enable = 1  (hashed)
3. 0x03/0x2A  01            AUTO_FLY (takeoff / start motors)
   -- OR 0x03/2A 07 START_MOTOR to just arm --
4. stream sticks @ 5–25 Hz  (0x01/0x0A special-TLV  OR  0x03/0x8E float  — confirm on HW)
5. panic:  0x03/2A 02 land  |  0x03/2A 06 GOHOME  |  0x03/0xFE 01 00 force-disable motors
6. 0x49/0x80  00            release control
```

---

## Appendix — `FLYC_COMMAND` enum (payload byte of `0x03/0x2A`), fully resolved
From `DataFlycFunctionControl$FLYC_COMMAND.smali`:
```
0x01 AUTO_FLY(takeoff)   0x02 AUTO_LANDING       0x03 HOMEPOINT_NOW      0x04 HOMEPOINT_HOT
0x05 HOMEPOINT_LOC       0x06 GOHOME(RTH)        0x07 START_MOTOR        0x08 STOP_MOTOR
0x09 Calibration         0x0A DeformProtecClose  0x0B DeformProtecOpen   0x0C DropGohome(cancel RTH)
0x0D DropTakeOff(cancel) 0x0E DropLanding(cancel)0x0F DynamicHomePointOpen 0x10 DynamicHomePointClose
0x11 FollowFunctionOpen  0x12 FollowFunctionClose 0x13 IOCOpen            0x14 IOCClose
0x15 DropCalibration     0x16 PackMode           0x17 UnPackMode         0x18 EnterManualMode
0x1E ForceLanding        0x1F ForceLanding2      0x22 PRECISION_TAKE_OFF  0x2F OAR_PANEL_CALI
0x36 MASS_CENTER_CALI    0x37 EXIT_MASS_CENTER_CALI                       0x64 OTHER
```
This corrects/extends `drone.py`: `confirm_land=0x1E` is actually `ForceLanding`; arming is
`START_MOTOR=0x07` / `STOP_MOTOR=0x08`; set-home-now is `0x03`.

## Appendix — command quick-reference (all sender=0x0a)
```
0x00/0x32  activate_device            (one-time, needs account+internet)
0x03/0x62  SetActiveResult            [state u32][u32][u32][str32]  (report activation to FC)
0x03/0x2A  FunctionControl            [FLYC_COMMAND u8]  (takeoff/land/rth/home/motors/cali)
0x03/0x31  SetHomePoint               [type u8][lat f64 rad LE][lon f64 rad LE][interval u8]
0x03/0x2D  SetLimits                  [mode u8:High1/Far2/Low3][value u16 LE m]   ← non-hash alt/dist
0x03/0x2E  GetLimits                  [mode u8]
0x03/0xF7  GetParamInfoByHash         (hash)          0x03/0xF8 read-by-hash
0x03/0xF9  SetParams / write-by-hash  (hash+value)    0x03/0xFA reset-by-hash
0x03/0x3B  SetFailSafeAction / 0x3C get             (RTH/failsafe behaviour)
0x03/0x80  ground-station on/off      [u8]
0x03/0x8E  FLYC joystick (float)      [flag u8][roll,pitch,yaw,throttle f32 LE]   ← stick candidate 3
0x03/0xFE  motor force-disable        [flag u8][mode u8]
0x01/0x0A  special-TLV sticks         (4×11-bit)      ← stick candidate 1 (RC emulation)
0x01/0x02  mobile-RC joystick         (13 B)          ← stick candidate 2
0x04/0x08  GimbalAutoCalibration      (gimbal cali)
0x06/0x03  rc_calibrate_channels
0x49/0x80  control auth               [u8 1=take/0=release]
0x19/0x40  lock / 0x19/0x41 preempt / 0x19/0x46 task-occupy   right-of-control
0x0A/…     vision set: pano 0x3E, action 0x4A, common_ctrl 0x27, timelapse 0x74.., poi 0xC1/0xC4,
           mastershot 0xF6, tracking 0x20/0x94  (QuickShot/pano/timelapse surface; Mini gating per §G)
```

## Open items that require a live capture (Frida hook target named)
1. **Stick variant** WM160 accepts (0x01/0x0A vs 0x03/0x8E) + whether ground-station/control-auth/
   `serial_api_cfg.advance_function_enable` gate it — hook `DataBase.start()` + `libsdk_jni` DUML send.
2. **Param name→hash** table for speed/gains/RTH-alt — hook `DataFlycSetParams.start()`, or dump the
   FC config file (`DataCommonGetCfgFile` / 0x03/0xF7).
3. **DarkNoGpsLock** actual DUML (§F) and **compass/IMU cali** DUML (§B) — hook `DataBase.start()`
   while toggling those switches in Safety settings.
4. Whether **0x03/0x2D** limits are honoured by WM160 firmware vs the hashed `flying_limit.max_height`
   — send 0x2D then read back 0x2E.
```
