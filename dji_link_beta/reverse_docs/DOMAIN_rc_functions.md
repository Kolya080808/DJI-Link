# DOMAIN: rc_functions — Remote-Controller functions for WM160 (Mavic Mini 1)

Scope: pairing/linking to the WM160, RC stick calibration (`0x06/0x03`), custom-button and
gimbal-dial mapping, RC battery/status, RC firmware, and the stick-read / stick-data channel.
Everything is DUML on `cmd_set 0x06` (label `rc`, receiver `DeviceType.RC = 0x06`).

Evidence base:
- `full_table.txt` §`CMD_SET 0x06 (6) [rc]` and `cmdmap.txt` — the *new* protobuf command registry
  (`uav_rc_*` names). These are the names the WM160 firmware answers to.
- `DUML_COMMANDS_FULL.md` §`CMD_SET 0x06 (6) — rc` — builder-verified byte layouts from the app's
  classic P3 DUML classes (`uav.midware.data.model.P3.DataRc*`), plus a "name-only" list.
- Smali disassembled from `unpacked_app_dex/classes_0451d00c.dex`
  (`uav/midware/data/model/P3/DataRc*`, `uav/midware/data/config/P3/CmdIdRc$CmdIdType`).
- `MASTER_REPORT.md`, `FLIGHT_GATING.md`, `isSupport_keys.txt`.

> **Two overlapping numbering worlds on cmd_set 0x06.** The app ships the classic
> `DataRc*` (P3 / DJI-Mobile-SDK) builders whose cmd_ids come from the enum
> `CmdIdRc$CmdIdType` (evidence below), *and* the firmware's newer `uav_rc_*` protobuf registry
> (`full_table.txt`). Where a cmd_id appears in **both**, the byte layout below is the
> authoritative on-wire request the app actually packs. The `uav_rc_*` entries that have **no**
> `DataRc*` class are "name-only": the request travels as a protobuf/native blob behind the packer
> and its byte layout is **not** statically decidable (flagged per-command).

---

## 0. WM160 applicability up front

- WM160 = Mavic Mini 1 = `UAV59` / ProductType `0x3b` (59) (`MASTER_REPORT.md`). Its controller is
  the standard Mavic Mini RC (single-band, GFSK/enhanced-Wi‑Fi link, no OcuSync, no dual-operator,
  no RTK/4G, no vibrating motor). Consequences for this domain:
  - **Supported / relevant:** stick-value read (`0x06/0x01`) + live stick-data push (`0x06/0x05`),
    stick calibration (`0x06/0x03`), RC battery push (`0x06/0x1E`), RC firmware/version, control
    (stick) mode (`0x06/0x19`/`0x1A`), CE/FCC power mode (`0x06/0x20`), pairing/link (`0x06/0x2F`
    frequency, `0x06/0x0C` connect-master), gimbal-dial/custom-button read (`0x06/0x51` push,
    `0x06/0x2E`/`0x2D` custom-function).
  - **NOT‑WM160 (present in app, but for other models):** `0x06/0x3D` `MultiRcPairing` /
    `0x06/0x46` select-target-aircraft / `0x06/0x49` enable-RTK / `0x06/0x4A` enable-4G
    (multi-device / Agriculture / Matrice), `0x06/0x6B` racing-RC vibrating motor (FPV/racing),
    `0x06/0x50` `SetMCU407` (2014-era RC MCU upgrade), `0x06/0x99` follow-focus (Inspire/pro RC dial).
  - `isSupport_keys.txt` shows the capability gates the app itself consults:
    `isSupportGfskPair`, `isSupportSpecialPairing`, `isSupportC2CustomBtn`,
    `isSupportLeftWheelCustomAction`, `isSupportFnWithLeftWheelCustomAction`,
    `isSupportFlightButtonParam`, `isSupportVirtualJoyStick`. **Whether each returns true for
    UAV59 is resolved at runtime** behind the packer — confirm live (Frida) per §7.

---

## 1. What this domain does (flow)

1. **Link / pairing.** RC and aircraft bind on a frequency. App can trigger frequency pairing
   (`0x06/0x2F SetFrequency`) and, on multi-device platforms only, `0x06/0x3D MultiRcPairing`.
   Connect/search-master (`0x06/0x0C`..`0x0E`) manages the master/slave (dual-operator) list.
2. **Stick read.** App reads the RC's channel configuration once via `0x06/0x01 GetChannelParams`
   and the raw hardware min/mid/max via `0x06/0x04 GetHardwareParams`; the **live per-frame stick +
   button state streams as the push `0x06/0x05 GetPushParams`** (§4 — this is the byte stream you
   want for "what are the sticks doing right now").
3. **Calibration.** `0x06/0x03` runs the 8-segment stick calibration; progress reported by
   `DataRcGetFDRcCalibrationState` (`0x06/0xF8`, §5).
4. **Mapping / customization.** Stick mode (Japan/America/China/Custom) via `0x06/0x19`; custom
   buttons and the gimbal (left) dial via `0x06/0x2D`/`0x2E` (classic) or `0x06/0x8D`/`0x8E`
   self-def-key (new); button/dial state read from the `0x06/0x51` push.
5. **Status.** RC battery (`0x06/0x1E` push), connect status (`0x06/0x1F`/`0x57` push),
   firmware/version (general `0x00/0x01`, plus `0x06/0x79` rc-firmware-info), CE/FCC (`0x06/0x21`).
6. **Hand-off to PC** (the project goal): `0x06/0xF1 uav_rc_set_app_to_pc_control` +
   `0x01/0x02 uav_action_virtual_rc_joystick` — see `FLIGHT_GATING.md` and `MASTER_REPORT.md`;
   summarized in §6.

---

## 2. cmd_id map for cmd_set 0x06 (evidence-pinned)

The cmd_id values below are the third `<init>` argument of each `CmdIdRc$CmdIdType` enum constant
(smali `uav/midware/data/config/P3/CmdIdRc$CmdIdType`, decoded). "layout" = builder-verified in
`DUML_COMMANDS_FULL.md` / smali `doPack`; "name-only" = `uav_rc_*` protobuf, no static layout.

| cmd_id | dec | classic class / `uav_rc_*` name | what it is | layout? |
|---|---|---|---|---|
| 0x01 | 1 | `DataRcGetChannelParams` | **read RC channel config** (per-channel name/value/direction) | empty req; rsp parsed §3 |
| 0x02 | 2 | `DataRcSetChannelParams` | write channel config | builder (`getSendData`) |
| 0x03 | 3 | `SetCalibration` / `uav_rc_calibrate_channels` | **stick calibration** | name-only (§5) |
| 0x04 | 4 | `DataRcGetHardwareParams` | raw hardware min/mid/max per channel | empty req; rsp §3 |
| 0x05 | 5 | `DataRcGetPushParams` | **live stick + button push** | push, parsed §4 |
| 0x06 | 6 | `DataRcSetMaster` | set master/slave role | 1B (see §3) |
| 0x07 | 7 | `DataRcGetMaster` | get master role | empty |
| 0x08/09 | 8/9 | `DataRcSet/GetName` | RC name | 6B / empty |
| 0x0A/0B | 10/11 | `DataRcSet/GetPassword` | RC link password | 2B u16 LE / empty |
| 0x0C/0D | 12/13 | `DataRcSet/GetConnectMaster` | connect to a master (dual-op link) | 12B (§3) / empty |
| 0x0E | 14 | `DataRcGetSearchMasters` | list nearby masters | empty |
| 0x0F/10 | 15/16 | `DataRcSet/GetSearchMode` | search on/off | 1B / empty |
| 0x11/12 | 17/18 | `DataRcSet/GetToggle` | machine function switch | 1B / empty |
| 0x13..0x18 | 19..24 | slave join/list/delete/permission | dual-operator mgmt | mixed |
| 0x19/1A | 25/26 | `DataRcSet/GetControlMode` | **stick mode (Jp/Am/Cn/Custom)** | 5B (§3) / empty |
| 0x1B | 27 | `DataRcGetPushGpsInfo` | RC GPS push | push |
| 0x1E | 30 | `DataRcGetPushBatteryInfo` | **RC battery push** | push (§4) |
| 0x1F | 31 | `DataRcGetPushConnectStatus` | RC↔aircraft connect push | push |
| 0x20 | 32 | `DataRcSetPowerMode` | **CE / FCC power mode** | 1B (§3) |
| 0x22 | 34 | `DataRcRequest/AckGimbalCtrPermission` | gimbal control permission | empty / 2B |
| 0x23 | 35 | `uav_rc_request_gimbal_control` | request gimbal control | name-only |
| 0x24/25 | 36/37 | `DataRcSetSimulation`/`GetSimFlyStatus` | flight simulator | 1B / empty |
| 0x26 | 38 | `DataRcSimPushParams` | sim stick push | push |
| 0x29/2A | 41/42 | `DataRcSet/GetSlaveMode` | slave control mode | 5B / empty |
| 0x2B/2C | 43/44 | `DataRcSet/GetGimbalSpeed` | gimbal-dial speed (pitch/roll/yaw) | 3B / empty |
| 0x2D/2E | 45/46 | `SetCustomFuction`/`GetCustomFuction` (`uav_rc_set/get_customized_btn_function`) | **custom-button / gimbal-dial mapping** | name-only |
| 0x2F | 47 | `DataRcSetFrequency` | **frequency pairing** | 1B (§3, §5) |
| 0x31/32 | 49/50 | `DataRcSet/GetRTC` | RC real-time clock | 7B / empty |
| 0x33/34 | 51/52 | `DataRcSet/GetWheelGain` | **gimbal-dial (wheel) gain** | 1B / empty |
| 0x35/36 | 53/54 | `DataRcSet/GetGimbalControlMode` | dial axis = Pitch/Roll/Yaw | 1B (§3) / empty |
| 0x3C | 60 | `CoachMode` | trainer/coach mode | — |
| 0x3D | 61 | `DataRcMultiPairing` (`uav_rc_mutil_device_pair`) | **multi-RC pairing** — NOT‑WM160 | 4B (§5) |
| 0x3F | 63 | `MaterSlaveId` / `DataRcSetAppSpecialControl` | master/slave id; app special control | 1–2B |
| 0x46 | 70 | `uav_rc_mutil_device_select_target_aircraft` | NOT‑WM160 | name-only |
| 0x47 | 71 | `uav_rc_custom_function_control` | custom-function trigger | name-only |
| 0x48 | 72 | `DataRcGetWifiFreqInfo` / `GetRCParam` | Wi‑Fi freq info / RC param | 1B |
| 0x49 | 73 | `DataRcEnableBaseStationRTK` | NOT‑WM160 (RTK) | 2B |
| 0x4A | 74 | `uav_rc_mutil_device_enable_4g` | NOT‑WM160 (4G) | name-only |
| 0x50 | 80 | `DataRcSetMCU407` | NOT‑WM160 (2014-RC MCU fw) | 205B |
| 0x51 | 81 | `DataRcGetPushRcCustomButtonsStatus` | **custom-button / dial state push** | push (§4) |
| 0x53/54 | 83/84 | `DataRcGet/SetRcUnitNLang` | unit + language | empty / 1B |
| 0x56 | 86 | `DataRcGetRcRole` / `GetFDPushConnectStatus` | RC role | empty |
| 0x57 | 87 | `DataRcGetFDPushConnectStatus` | new-link connect push | push |
| 0x58 | 88 | `DataRcSetNewControlMode` (`uav_rc_..new_control`) | new control function | 2B (§3) |
| 0x59 | 89 | `DataRcSetFlightChannel` | set flight channel | 1B |
| 0x79 | 121 | `uav_rc_get_get_rc_firmware_info` | **RC firmware info** | name-only (§5) |
| 0x8C | 140 | `uav_rc_set_app_work_stage` | app work stage | name-only |
| 0x8D/8E | 141/142 | `uav_rc_set/get_self_def_key_list` | **new self-defined key (button) mapping** | name-only (§5) |
| 0x99 | 153 | `DataRcSetFollowFocusInfo` | NOT‑WM160 (pro-RC follow-focus dial) | 1B |
| 0xA1 | 161 | `uav_rc_push_data_sync` | RC data-sync push | name-only |
| 0xAA | 170 | `uav_rc_rocker_control_gimbal_mode` | stick-controls-gimbal mode | name-only |
| 0xE5 | 229 | `uav_rc_get_custom_setting_support_info` | which custom settings supported | name-only |
| 0xF1 | 241 | `uav_rc_set_app_to_pc_control` | **hand control app→PC** | name-only (§6) |
| 0xF8 | 248 | `DataRcGetFDRcCalibrationState` | **stick-cal 8-segment progress** | 1B req (const 0) (§5) |
| 0x6B | 107 | `uav_rc_..RACING_RC_VIBRATING_MOTOR_CTRL` | NOT‑WM160 (racing RC) | name-only |
| 0x72 | 114 | `uav_rc_set_stick_value_lock[_with_ch4_func]` | stick-value lock | name-only |
| 0x74 | 116 | `uav_rc_get_stick_value_lock_status` | stick-lock status | name-only |

`0x1ff` `Other` in the enum is the catch-all sentinel (not a wire cmd).

---

## 3. Stick-read (`0x06/0x01`), hardware (`0x06/0x04`) and setter payloads

### `0x06/0x01` `DataRcGetChannelParams` — read channel configuration
Request: **empty** (0-byte payload). Response is decoded into a `SparseArray<UAVChannel>`.
Per smali `DataRcGetChannelParams.getList()`: the body is read in **3-byte records** (const `0x3`
stride) starting at offset 0; each record fills a `UAVChannel` (fields `name:int`, `value:int`,
`direction:boolean`). So this returns the *configured channel table* (which physical channel maps to
which logical function + its current value/direction), **not** a fast telemetry stream.
Send is built in `start()`: CmdSet `0x06` (`CmdSet.e()`), cmd_id `CmdIdRc$CmdIdType.a`
(=`GetChannelParams`=`0x01`), receiver = `DeviceType.RC`.

> Note on the task's phrasing "stick-read channel `0x06/01`": `0x06/01` is the **channel-config
> read** above. The **per-frame stick values** (aileron/elevator/throttle/rudder + dial + buttons)
> are the **`0x06/05` push** in §4. Read both; `0x05` is the one you poll/subscribe for live control.

### `0x06/0x04` `DataRcGetHardwareParams` — raw hardware calibration values
Request: empty. Response `getList()` reads **2-byte records** (stride `0x2`) from offset 0 into a
`SparseIntArray` — the raw per-channel hardware values (min/mid/max endpoints used by calibration).

### `0x06/0x02` `DataRcSetChannelParams` — write channel config
Builder packs from an internal buffer (`getSendData`); cmd_id `0x02`. Layout is data-driven (mirror
of the `0x01` record table); assert live before writing.

### `0x06/0x19` `DataRcSetControlMode` — stick (control) mode
`doPack`: `_sendData` length **5**, byte `+0` = `ControlMode.value()`; remaining bytes come from an
`arrayList` (custom-mapping payload, present when mode = `Custom`). Enum
`DataRcSetControlMode$ControlMode` (smali):

| name | wire value |
|---|---|
| Japan (Mode 1)   | `0x01` |
| America (Mode 2) | `0x02` |
| China (Mode 3)   | `0x03` |
| Custom           | `0x04` |
| OTHER (sentinel) | `0x64` |

`0x06/0x1A GetControlMode`: empty request.

### `0x06/0x20` `DataRcSetPowerMode` — CE/FCC
1-byte payload = `UAVRcPowerMode.value()`: `CE=0x00`, `FCC=0x01`, `OTHER=0x02` (sentinel `0x64`).
(WM160 obeys regional CE/FCC power; relevant if you tune link range.)

### `0x06/0x35` `DataRcSetGimbalControlMode` — which axis the dial drives
1-byte = `MODE.value()`: `Pitch=0x00`, `Roll=0x01`, `Yaw=0x02` (OTHER sentinel `0x03`; `0x0a`).
`0x06/0x2B DataRcSetGimbalSpeed` = 3 bytes `pitch,roll,yaw` (dial speed).
`0x06/0x33 DataRcSetWheelGain` = 1 byte gain (gimbal-dial sensitivity).

### `0x06/0x0C` `DataRcSetConnectMaster` — link to a master (dual-operator)
12-byte: `+0` 4B id (LE), `+4` 4B id (LE), `+10` 2B u16 password (LE) — per `DUML_COMMANDS_FULL.md`.
Dual-operator is **NOT‑WM160** but the primary-link password path (`0x0A/0x0B`, u16) applies.

---

## 4. Push streams — the live stick / battery / button data

### `0x06/0x05` `DataRcGetPushParams` — **the live stick + control-surface stream**
Fields decoded via `DataBase.get(offset,size,Class)`; offsets are **byte offsets into the payload**,
size in bytes, LE. Verified from smali `DataRcGetPushParams`:

| field (getter) | offset | size | notes |
|---|---:|---:|---|
| `getAileron`  | 0  | 2 | right-stick X (roll)     |
| `getElevator` | 2  | 2 | right-stick Y (pitch)    |
| `getThrottle` | 4  | 2 | left-stick Y (throttle)  |
| `getRudder`   | 6  | 2 | left-stick X (yaw)       |
| `getGyroValue`      | 8  | 2 | gimbal-dial / gyro (variant A) |
| `getWheelOffset`    | 10 | 1 | `& 0x3e` — gimbal wheel delta |
| `getWheelClickStatus`| 10 | 1 | same byte, click bits |
| `getMode`     | 11 | 1 | `& 0x3` — RC mode field |
| buttons byte  | 12 | 1 | `getCustom1..4`, `getPlayback`, `getRecordStatus`, `getShutterStatus`, `getSwitch`, `getPlayBackStatus` (bit flags in byte 12) |
| `getBandWidth`| 13 | 1 | link bandwidth |
| `getRightGyroValueForORP56` | 13 | 2 | dial value (ORP56 RC variant) |
| `getBa..Bf`, `getCendenceCustom3/4` | 16 | 1 | bit flags (byte 16) |
| `getRightGyroValueV1` | 16 | 2 | dial value (V1 RC variant) |
| `getBg,Bh,Ca,Ce,Cm,Cs,getMenu` | 17 | 1 | bit flags (byte 17) |
| `getRightGyroValue` | 20 | 2 | dial value (variant) |
| `getLeftDialValue`  | 22 | 2 | **left dial (gimbal dial)** |
| `getRightDialValue` | 24 | 2 | right dial |

> **RC-model dependence (needs live capture).** The app exposes *multiple* dial/gyro getters at
> different offsets (`getGyroValue`@8, `getRightGyroValueForORP56`@13, `getRightGyroValueV1`@16,
> `getRightGyroValue`@20, `getLeftDialValue`@22, `getRightDialValue`@24) precisely because the byte
> layout of this push differs per RC hardware. **Which offset carries the Mavic Mini RC's gimbal
> dial is not statically decidable** — capture a real `0x06/0x05` frame from the WM160 RC (Frida on
> `DataRcGetPushParams`/`DataBase.get`, or sniff the DUML) and confirm. Sticks (0/2/4/6, 2B each)
> are stable across models. Stick values are unsigned; DJI convention is 364..1024..1684 (mid 1024).

### `0x06/0x1E` `DataRcGetPushBatteryInfo` — RC battery
| field | offset | size |
|---|---:|---:|
| `getBatteryVolume` (mV or raw) | 0 | 4 |
| `getBattery` (percent)         | 4 | 1 |

### `0x06/0x51` `DataRcGetPushRcCustomButtonsStatus` — custom buttons / 5-D
All read from **byte 0** as bit flags (`get(0,1) & mask`):
`isUp`(no mask / bit0), `isDown`(`0x02`), `isPressed`(`0x04`), `isLeft`(`0x08`), `isRight`(`0x10`),
`isC1Pressed`(`0x20`), `isC2Pressed`(`0x40`); `gets()` returns the raw byte. This is the C1/C2
buttons + 5‑D up/down/left/right/press state (WM160 RC has C1/C2-style custom buttons — gate with
`isSupportC2CustomBtn`).

Other pushes: `0x06/0x1B GpsInfo`, `0x06/0x1F`/`0x57 ConnectStatus`, `0x06/0x42`/`0x98 FollowFocus`
(NOT‑WM160), `0x06/0x3E MultiRcPairingStatus` (NOT‑WM160), `0x06/0xAB FlowControl`.

---

## 5. Calibration, pairing, firmware

### Stick calibration — `0x06/0x03`
- Trigger: `0x06/0x03 uav_rc_calibrate_channels_req` / enum `CmdIdRc$CmdIdType.SetCalibration`
  (=`0x03`). **Request byte layout is name-only** (no `DataRcSetCalibration` builder in the app;
  DJI Fly drives it through KeyManager key `KeyRcCalibrateChannels`, string present in the dex).
  It is not needed for PC virtual-stick flight (`FLIGHT_GATING.md` §B).
- Progress read-back: `0x06/0xF8 DataRcGetFDRcCalibrationState`. Request = 1 byte const `0x00`.
  Response getters (smali): `getSegmentNumber()` and per-segment fill state
  `getA/B/C/D/E/F/G/HSegmentFilledUpState()` — an **8-segment (A–H)** stick-travel calibration; each
  segment's filled/limit state is read at stepped offsets (9,13,17,21,25,29 … 4-byte stride).
  Calibration is complete when all 8 segments report filled.
- Related FC/gating strings (`FLIGHT_GATING.md`): OSD reports
  `REMOTE_CONTROLLER_NEED_CALIBRATION` / `_MIDDLE_LARGE` / `_MAPPING_EXCEPTION` at `0x09/+0x33` —
  that's how you detect the RC needs calibration before flight.

### Pairing / linking
- **WM160 path — frequency pairing:** `0x06/0x2F DataRcSetFrequency`. `doPack`: 1 byte =
  `FreqMode.value()`. Enum `DataRcSetFrequency$FreqMode`:
  `Current=0`, `Enter=1`, `Cancel=2`, `MasterEnter=5`, `SlaveEnter=6`, `SubEnter=7` (OTHER `0x0a`).
  Frequency-code enum `FreqCcode` also present. To start a re-link you send `Enter`.
- Password link (single link): `0x06/0x0A SetPassword` (2B u16 LE), `0x06/0x0C SetConnectMaster`.
- **NOT‑WM160 — multi-device pairing:** `0x06/0x3D DataRcMultiPairing`. `doPack`: 4 bytes
  `a,b,c,d`. Sub-enums: `PairAction{GET_PAIR_STATE=0, ENTER_PAIRING=1, EXIT_PAIRING=2,
  GET_CP_STATE=3, UNKNOWN=0xff}`, `PairMode{AGRICULTURE=0, OTHER=…}`,
  `PairState{UNPAIRED=0, PAIRING=1, PAIRED=2, UNKNOWN=0xff}`, plus `PairTarget`, `SDRPairState`.
  Status push `0x06/0x3E`. This is Agriculture/Matrice multi-operator — not the Mavic Mini.
- Capability gates the app checks before choosing a pairing UI: `isSupportGfskPair`,
  `isSupportSpecialPairing` (`isSupport_keys.txt`). WM160's link is GFSK/enhanced-Wi‑Fi, so expect
  `isSupportGfskPair` to drive it — confirm live.

### RC firmware / version
- `0x06/0x79 uav_rc_get_get_rc_firmware_info_req` — dedicated RC firmware-info command
  (**name-only**, protobuf; no `DataRc*` builder → layout not static).
- General version path also applies: `0x00/0x01 uav_general_get_get_version` addressed to the RC
  device returns the RC firmware/loader version (see the general/version domain).

### Custom-button & gimbal-dial mapping (two generations)
- **Classic:** `0x06/0x2D SetCustomFuction` / `0x06/0x2E GetCustomFuction`
  (`uav_rc_set/get_customized_btn_function`). **Name-only** — the mapping table is a protobuf blob;
  layout not static.
- **New:** `0x06/0x8D uav_rc_set_self_def_key_list` / `0x06/0x8E get_self_def_key_list`, with
  `0x06/0xE5 get_custom_setting_support_info` advertising which keys are remappable, and
  `0x06/0x47 custom_function_control` to fire a mapped function. All **name-only**.
- Dial specifics: axis via `0x06/0x35` (§3), speed `0x06/0x2B`, gain `0x06/0x33`; live dial value
  in the `0x06/0x05` push (§4). App gates: `isSupportLeftWheelCustomAction`,
  `isSupportFnWithLeftWheelCustomAction`, `isSupportFlightButtonParam`.

---

## 6. Relation to the PC-control goal (WM160)

The RC-function commands above read/configure the *physical* RC. To make the **PC** the controller
you don't calibrate/pair — you inject virtual sticks and take control away from the app/RC
(cross-refs, documented fully in `MASTER_REPORT.md` / `FLIGHT_GATING.md`):
- `0x06/0xF1 uav_rc_set_app_to_pc_control` — hand control app→PC (name-only; try payload `01`).
- `0x01/0x02 uav_action_virtual_rc_joystick` and/or the FLYC joystick (`DataFlycJoystick`, FLYC
  `0x8E`, 17B: flag + 4×f32 LE roll/pitch/yaw/throttle) — the actual stick injection.
- `isSupportVirtualJoyStick` gates it; `MASTER_REPORT.md` states MSDK virtual stick **works on the
  Mavic Mini** (supported since SDK 4.13). The `0x06/0x05` push (§4) is still useful as the *echo*
  of what the physical RC is doing, e.g. for a takeover/override handoff or a safety fallback.

---

## 7. What must be confirmed live (Frida / capture)

1. **`0x06/0x05` exact layout for the Mavic Mini RC** — which dial/gyro offset is real (§4).
   Hook `uav.midware.data.model.P3.DataRcGetPushParams` getters, or `DataBase.get(I,I,Class)`, or
   sniff the raw DUML `55 … 06 05 …` frames. Confirm stick range/mid.
2. **`0x06/0x03` calibration request bytes** — name-only; hook the send when pressing "Calibrate
   RC" in DJI Fly, or hook `KeyRcCalibrateChannels`.
3. **`0x06/0x79` RC firmware, `0x06/0x2D/0x2E`, `0x06/0x8D/0x8E/0xE5/0x47`** custom-button/self-def
   payloads — all protobuf/name-only; capture live.
4. **Capability flags for UAV59** — `isSupportGfskPair`, `isSupportSpecialPairing`,
   `isSupportC2CustomBtn`, `isSupportLeftWheelCustomAction`, `isSupportVirtualJoyStick` — resolve at
   runtime behind the packer; read them on a live WM160 connection.
5. **`0x06/0xF1` app→PC control** payload/effect — verify it actually yields control on WM160.

All cmd_id/enum/offset values above are from static evidence (`full_table.txt`, `cmdmap.txt`,
`cmds.json`, `DUML_COMMANDS_FULL.md`, and smali of `classes_0451d00c.dex`); the "name-only" and
"needs live capture" flags mark exactly what static analysis cannot pin.
