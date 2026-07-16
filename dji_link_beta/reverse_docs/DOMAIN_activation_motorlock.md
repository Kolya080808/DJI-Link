# DOMAIN: activation_motorlock (WM160 / Mavic Mini 1 / UAV59)

Scope: device **activation** (factory-fresh -> usable), **account/device binding & anti-theft**, and the
**motor-lock** mechanisms that keep motors from starting. Everything below is filtered to WM160 (=UAV59).
Evidence is cited to `full_table.txt`, `cmdmap.txt`, `cmds.json`, `all_classes.txt`, the 16-DEX dump under
`unpacked_app_dex/`, and the sibling docs `FLIGHT_GATING.md` / `DUML_COMMANDS_FULL.md`. Values not present in
those sources are marked NEEDS-CAPTURE with the exact class/hook to confirm.

---

## 0. TL;DR

- **Activation is a ONE-TIME cloud procedure**, persisted in the FC. It is **not** per-flight and **not** a
  login. An already-activated WM160 needs **no account and no internet** to arm/fly. (`FLIGHT_GATING.md:38-53,66-68`.)
- Wire path: app reads FC active state -> if un-activated, app runs cloud activation
  (`0x00/0x32 uav_general_activate_device_req`, native module + DJI server) -> app writes the result back to
  the FC with **`0x03/0x62 DataFlycSetActiveResult`**. (`full_table.txt:14`; `cmdmap.txt:46`;
  `DUML_COMMANDS_FULL.md:325`; `FLIGHT_GATING.md:426-427`.)
- The **hard gate lives in FC firmware**: reason enum `FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED`
  (strings in DEX). The PC cannot bypass an un-activated aircraft in software. (`FLIGHT_GATING.md:31-36`.)
- **`uav.component.motorlock` is NOT the anti-theft lock.** It is the app/FC "lock the motors" gate tied to
  **forced firmware / NFZ-database upgrades** and hardware faults. (DEX strings, §4.)
- **True anti-theft "secure binding / lock-uav" DUML commands exist in the protocol enum table but are NOT
  wired into this app's Java** -> they are for higher-end/enterprise models, **NOT-WM160** in practice. (§5.)

---

## 1. What "activation" is and when it is enforced

Activation registers the aircraft's serial with a DJI account on DJI's cloud once, and the FC stores an
"activated" flag in its own non-volatile config. Enforcement:

- **In the aircraft (hard):** the FC's own "cannot take off" reason set contains
  `FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED` (string present across DEX; sibling of the other
  `FC_CANNOT_TAKE_OFF_*` reasons). The FC firmware refuses to spin motors when its stored flag says
  un-activated. Corroborated by strings `error_account_user_not_activated_313`, `home_account_not_activated`,
  `fpv_checklist_takeoff_failure_drone_not_activated`, `fpv_capsule_takeoff_failure_drone_not_activated`.
  (`FLIGHT_GATING.md:31-36`; DEX string grep.)
- **In the app (soft, self-imposed):** the app blocks its own flight UI until it observes the activated state.
  Classes/strings: `ActivationModelImpl.needActivateDevices`
  (`com/uav/flymodel/generated/impl/business/activation/ActivationModelImpl`), the mainpage
  `ActivateInfoDataSource` / `IActivateInfoDataSource` / `ActivateState`
  (`classes_00b9d00c.dex`), `HandleActivateSuccessUseCase`,
  `GetHomeActivateRemindDialogStateChangeObservableUseCase`,
  `com/uav/component/fpv/activate/FpvActivateGate`, and strings
  `homepage_drone_not_activated_dialogue_content/yes_btn`, `in fpv DRONE_NOT_ACTIVATED got, exit fpv`.
  (`all_classes.txt` activate rows; DEX strings; `FLIGHT_GATING.md:55-58`.)

**Frequency:** ONE-TIME. Once the FC records activation it persists on the aircraft; the app's
`getIsActivated` / `isActivated` reads FC state, it does not re-authenticate per flight. The only
account+internet-gated event is the initial activation. (`FLIGHT_GATING.md:38-53,66-68`.)

**Does an already-activated WM160 need anything?** No. A previously-used (activated) Mini 1 can be armed and
flown with the PC alone -- no DJI login, no cloud round-trip. If the unit is factory-fresh (or FC config was
wiped), it must be activated **once** with the genuine app + a logged-in DJI account online.
(`FLIGHT_GATING.md:66-71`.)

---

## 2. The activation flow (classes)

Static call chain, app side:

1. **Read FC active state.** Push/query model `DataFlycActiveStatus`
   (`Luav/midware/data/model/P3/DataFlycActiveStatus;`, `classes_0451d00c.dex`) and the FC->app "please
   activate" request `DataFlycGetPushActiveRequest`
   (`Luav/midware/data/model/P3/DataFlycGetPushActiveRequest;`). Both derive from the generic
   `DataAbstractGetPushActiveStatus` (has nested enums `$TYPE` and `$UAVActiveVersion` / `UAVActiveVersion`;
   also present in `classes_09b2900c.dex`). Field `activeStatus` (`getActiveStatus` / `mActiveStatus`).
   `FLIGHT_GATING.md:40-42` records this query runs on the COMMON set (`CmdSet.a` / `CmdIdCommon`, timeout
   const `0x3e8` = 1000 ms). Exact cmd_set/cmd_id of the status push is NEEDS-CAPTURE (see §3 note).
2. **Cloud activation (account + internet).** Native module `Luav/activate/UAVActivateManager;` over JNI
   `Luav/activate/jni/JNIActivate;` with callbacks `JNIActivateStateInfoCallback`,
   `JNIActivateModuleInfoCallback`, `JNIActivateConnectionStateCallback`, `JNIActivateDataBufferCallback`
   (and de-activate variant `JNIDeActivateDataBufferCallback`) -- all in `classes_03a5700c.dex`
   (`all_classes.txt:44813-44846,50827-50833`). Higher-level orchestration:
   `com/uav/activate/DeviceDataManager`, `com/uav/activate/UAVDeviceManagerServiceImpl`,
   terms/precaution UI (`ActivateTermsActivity`, `PrecautionActivity`). The DUML side of this step is
   `0x00/0x32 uav_general_activate_device_req` / `_rsp`.
3. **Report result to FC.** `DataFlycSetActiveResult` -> **`cmd_set 0x03 / cmd_id 0x62 (98)`** (§3).

Value/key surface used by the SDK layer: `Luav/sdk/keyvalue/value/activate/ActivateStateMsg;`,
`ActivateUsedInfo`, `ActivateVersion` (`classes_03a5700c.dex`), observer entrypoint
`registerActivateStateObserver` (string: `java registerActivateStateObserver data invalid`).

> The heavy lifting of step 2 (crypto handshake, the actual HTTPS to DJI's activation host) is inside the
> **native lib behind the packer** -- not statically visible in Java. To capture it live, hook
> `Luav/activate/jni/JNIActivate;` methods and `UAVActivateManager` with Frida, and/or the
> `ActivateStateInfoCallback.onActiveStateInfo` path.

---

## 3. Exact DUML commands (wire values)

All are little-endian. `cmd_set` first, then `cmd_id`.

### 3.1 Activate device (cloud/app <-> device) -- `0x00/0x32`
- `full_table.txt:14`: `0x00/0x32 (50) req=uav_general_activate_device_req rsp=uav_general_activate_device_rsp`
- `cmdmap.txt:46`: `set=0, id=50, uav_general_activate_device_req / _rsp`
- Payload body is opaque in the static app (built/consumed by the native activate module). NEEDS-CAPTURE via
  the JNI hook in §2. This is the account+internet step.

### 3.2 Deactivate device -- `0x00/0x36`
- `full_table.txt:16`: `0x00/0x36 (54) req=uav_general_deactivate_device_req rsp=uav_general_deactivate_device_rsp`
- `cmdmap.txt:48`: `set=0, id=54`. Reverse of activation (factory/RMA). Not needed for normal WM160 use.

### 3.3 Report activation result to FC -- `0x03/0x62` (THE one you must send)
- Class: `Luav/midware/data/model/P3/DataFlycSetActiveResult;` (`classes_0451d00c.dex`) with nested enum
  `DataFlycSetActiveResult$UAVActivationState`.
- `DUML_COMMANDS_FULL.md:325`:
  `0x62 | 98 | DataFlycSetActiveResult | FLYC | len=44B` with layout:
  - `+0`  4B u32 LE  `activationState`  (the `UAVActivationState` enum, §3.4)
  - `+4`  4B u32 LE  `appId`
  - `+8`  4B u32 LE  `appLevel`
  - `+12` 32B string `appLevel`  (32-byte fixed field; the auto-labeler mis-tagged the name -- it is the
    trailing 32-byte blob/string, total payload 44 bytes)
- `FLIGHT_GATING.md:46-50` gives the same as:
  `[0..3] UAVActivationState u32 | [4..7] u32 | [8..11] u32 | [12..43] 32-byte string`.

### 3.4 `UAVActivationState` enum (wire values)
From `FLIGHT_GATING.md:50` (source: `DataFlycSetActiveResult$UAVActivationState`):

| Name | Value |
|------|-------|
| `Success` | 0 |
| `NoNetwork` | 1 |
| `InvalidId` | 2 |
| `FailedForNet` | 3 |
| `OTHER` | 100 |

To arm on a genuinely-activated aircraft you report `Success (0)`.

> Note on `DataFlycActiveStatus` cmd_id: `DUML_COMMANDS_FULL.md` enumerates the *other* product families'
> active-status models (`DataEagleActiveStatus 0x26`, `DataGlassActiveStatus 0x26`) but not the FLYC one's
> id explicitly; `FLIGHT_GATING.md:40` places the FLYC active-state read on the COMMON set. Treat the exact
> FLYC status cmd_id as NEEDS-CAPTURE -- confirm by hooking `DataFlycActiveStatus` / `DataFlycGetPushActiveRequest`
> pack/unpack.

---

## 4. `uav.component.motorlock` -- what the motor-lock actually is (NOT anti-theft)

Package `Luav/component/motorlock/` (`classes_03a5700c.dex`, `all_classes.txt:51611-51650+`). Public surface:
`IMotorLockService`, `MotorLockService`, `LockChangeListener`, `MotorLockException`,
`model/LockKey` + `LockKeyImp`, `PresetLockMotorModule`, `LockMotorServiceLog`.

Evidence of purpose (DEX strings, `classes_03a5700c.dex`):
- `firmware-upgrade need force upgrade, request lock motor.`
- `Database-upgrade need-force-upgrade, request lock motor.`
- `firmware-upgrade no-need force upgrade, request unlock motor.`
- `Database-upgrade not-need-force-upgrade, request unlock motor.`
- `firmware-upgrade no-need force upgrade, request unlock UpgradeLock.`
- `reportFirmwareForceUpdateUnlockMotor` / `reportFirmwareForceUpdateUnlockGlass`
- `SUPPORT_UNLOCK_FLYC_PROTOCOL_VERSION`, `unlock motors failure. FlightController connect=`
- Checklist strings: `fpv_checklist_takeoff_failure_hardware_lock_motor`,
  `fpv_checklist_app_general_lock_drone`, `..._lock_drone_need_network`, `..._lock_drone_no_internet`.

**Interpretation:** `motorlock` is the app-managed gate that *locks* motor start when the aircraft has a
pending **forced firmware or NFZ/geo database upgrade** (or a hardware fault), and *unlocks* once that
condition clears. `DefaultLockKey{key='...}` (`LockKeyImp`) is the token exchanged to release the lock. This
is a maintenance/compliance lock, **not** a stolen-device / account-ownership lock.

Related but separate components pulled in by the same DEX:
- **Flight-restriction / NFZ self-unlock**: `Luav/component/flightrestrict/unlock/model/*`
  (`LicenseType`, `FlyfrbLicenseV3Info`, `WhiteListLicense`, `CircleUnlockAreaLicense`,
  `PentagonUnlockAreaLicense`, `AccountStateBeforeUnlock`), native `Luav/fscore/jni/unlock/JNIFSUnlockManager`,
  `Luav/jni/flightrestrict/callback/SpecialUnlockCallback`, and `uav/component/licenseunlock/*`. Strings:
  `COUNTRY_UNLOCK`, `CIRCLE_UNLOCK_AREA`, `ALLOW_TO_UNLOCK`, `ACCOUNT_NOT_VERIFY`,
  `fpv_setting_safe_unlock_nfz_log_in_dialogue_tite`. This is the geofence license unlock, account-gated only
  when unlocking a real NFZ -- see `FLIGHT_GATING.md` for WM160 NFZ handling.
- **`flyMotorPowerAbnormalLockSwitch`** / `fpv_checklist_abnormal_motor_power` /
  `fpv_basic_flight_abnormal_motor_power_zero_error_dialogue_content`: a *fault* lock (ESC/motor power
  abnormal), unrelated to activation.

### 4.1 FC-side motor force-disable DUML
- `full_table.txt:213` / `cmdmap.txt:298`:
  `0x03/0xFE (254) req=uav_fc_set_set_motor_force_disable_flag_req rsp=..._rsp`
- App model: `Luav/midware/data/model/P3/DataFlycSetMotorForceDisable;` (string `SetMotorForceDisable`).
- This is the low-level "force-disable motors" flag the FC honors. Payload not fully typed statically ->
  NEEDS-CAPTURE by hooking `DataFlycSetMotorForceDisable`. (WM160-plausible; it is a generic FLYC command.)

---

## 5. Account/device binding & true anti-theft

Two distinct things share the word "bind":

### 5.1 DJI Care binding (insurance) -- present, WM160-applicable but optional
HTTP host `https://api.djiservice.org/api/device-manager/` (staging `https://stag-dsapi.dbeta.me/api/device-manager/`).
Endpoints (DEX strings):
- `/api/device-manager/care/bind-account`
- `/api/device-manager/care/unbind-account`
- `/api/device-manager/care/bind-sn-list`
- `/api/device-manager/care/device-detail`
- DJI-Care task/query: `/api/v1/djicare/gen_task`, `/api/v1/djicare/query`, `/api/v1/djicare/result`,
  `/api/v1/djicare/v2/query`, `/api/v1/djicare/sn_product_center/query`, `/api/v1/djicare/selfcare/encrypt`.
- Phone binding: `apis/apprest/v2/phone/binding`; UI: `com/uav/device/manager/care/bind/BindDroneActivity`,
  `com/uav/activate/care/UAVCareActivity`, value `DeviceListModel$BindInfo/CareInfo`,
  `Luav/sdk/keyvalue/value/product/BindDeviceType`, `DeviceBindingInfoMsg`, `UnmatchedBindInfo`,
  key strings `KeyQueryAccountBindInfo get result=`, `KeyCareUnmatchedBindInfo`.

This is **insurance/warranty binding, not a motor gate**. It does not stop an activated WM160 from flying and
is not required for PC control.

### 5.2 Secure binding / lock-uav (real anti-theft) -- NOT-WM160 in this app
The protocol enum table lists anti-theft/secure-binding commands, but **none of them have Java callers in this
app** (grep of `all_classes.txt` for `SecureBinding|LockUav|DeviceUserBind|get_lock_uav|secure_device_user`
returns nothing):
- `0x00/0xD5 (213)  uav_general_get_lock_uav_req / _rsp`  (`full_table.txt:44`, `cmdmap.txt:27`)
- `0x00/0xE5 (229)  uav_general_get_secure_binding_req / _rsp`  (`full_table.txt:49`, `cmdmap.txt:32-33`)
- `0x00/0xE6 (230)  uav_general_get_secure_device_user_bind_req / _rsp`  (`full_table.txt:51`, `cmdmap.txt:34`)
- Related enterprise control-lock: `0x19/0x40 (64) uav_extend_lock_right_of_control_req / _rsp`
  (`full_table.txt:434`, `cmdmap.txt:138`) and access-locker `0x00/0x74 (116)`
  `uav_general_accesslocker_v1_...` (`full_table.txt:26`). Error strings `ACCESS_LOCKER_V1_VERSION_ERR`,
  the `PROTOTYPE_MANAGER_*` auth/encrypt errors, and `VerifyDeviceResult` exist but belong to the
  secure-binding / prototype-auth path.
- `0x00/0x12 (18) uav_general_find_uav_req / _rsp` (`cmdmap.txt:25`) -- the "find my drone" beeper, adjacent
  anti-theft utility.

**Conclusion:** true account-ownership anti-theft (secure binding / lock-uav) is a protocol capability of the
platform but is **not exercised for WM160** here -- it targets enterprise/newer consumer models. Whether a
WM160 FC even answers `0x00/0xE5`/`0x00/0xD5` is undecidable statically; if you care, probe those cmd_ids
against a real WM160 and watch for a valid vs. unsupported-command response. NEEDS-CAPTURE.

---

## 6. WM160 support matrix

| Item | Cmd / class | WM160 |
|------|-------------|-------|
| Cloud activate | `0x00/0x32` + native `JNIActivate`/`UAVActivateManager` | SUPPORTED (one-time, account+internet) |
| Deactivate | `0x00/0x36` | SUPPORTED (rare; RMA/factory) |
| Report activation to FC | `0x03/0x62 DataFlycSetActiveResult` (44B, enum §3.4) | SUPPORTED / REQUIRED after activation |
| Read active state | `DataFlycActiveStatus` / `DataFlycGetPushActiveRequest` (COMMON set) | SUPPORTED (cmd_id NEEDS-CAPTURE) |
| Motor-lock (force-upgrade / fault gate) | `uav.component.motorlock` | SUPPORTED (app/FC maintenance lock) |
| FC force-disable motors flag | `0x03/0xFE DataFlycSetMotorForceDisable` | PLAUSIBLE (generic FLYC; NEEDS-CAPTURE) |
| NFZ / geo self-unlock | `flightrestrict/unlock`, `licenseunlock`, `JNIFSUnlockManager` | SUPPORTED (account-gated per real NFZ) |
| DJI Care bind/unbind | `device-manager/care/*` HTTP | SUPPORTED but optional (insurance, not a gate) |
| Secure binding / lock-uav (anti-theft) | `0x00/0xE5`, `0x00/0xD5`, `0x00/0xE6` | NOT wired in app -> NOT-WM160 in practice |
| Find-my-drone beeper | `0x00/0x12 uav_general_find_uav` | PLAUSIBLE (present in table; NEEDS-CAPTURE) |

---

## 7. Practical guidance for the PC-control project

- If your WM160 was ever used with the genuine app + account, it is already activated: **do nothing** for
  activation, and motors will arm (subject to the other gates in `FLIGHT_GATING.md`). No login/internet.
- If motors refuse and diagnostics show `DRONE_NOT_ACTIVATED` (`FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED`,
  `MOTOR_NOT_START`), the FC's stored flag is un-activated -> you must run the **one-time genuine-app cloud
  activation** first; the PC cannot forge it (crypto is in the native lib + DJI server).
- Once activated, to satisfy any app-parity report you would send `0x03/0x62 DataFlycSetActiveResult` with
  `activationState=Success(0)`; but a normally-activated FC already holds its own flag, so this is only needed
  if you replicate the full app handshake.
- `uav.component.motorlock` is a maintenance/compliance lock (forced FW/DB upgrade, hardware fault), not an
  account lock -- if it engages, resolve the upgrade/fault, not credentials.

---

## 8. Live-capture / Frida checklist

- `Luav/activate/jni/JNIActivate;` (all methods) + `Luav/activate/UAVActivateManager;` +
  `ActivateStateInfoCallback.onActiveStateInfo` -> capture the real `0x00/0x32` payload and the activation
  HTTPS host (packed in native).
- `Luav/midware/data/model/P3/DataFlycSetActiveResult;` pack() -> confirm the exact 44-byte `0x03/0x62` bytes
  and `appId`/`appLevel` values.
- `DataFlycActiveStatus` / `DataFlycGetPushActiveRequest` (un)pack -> confirm the FLYC active-status cmd_id.
- `DataFlycSetMotorForceDisable` -> confirm `0x03/0xFE` payload.
- Probe `0x00/0xE5` / `0x00/0xD5` against a live WM160 to settle whether secure-binding is answered at all.
