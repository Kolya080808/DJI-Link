# DOMAIN: Firmware Upgrade (WM160 / Mavic Mini 1 = UAV59)

Scope: how DJI Fly (v1.21.4, this decompile) fetches, verifies and pushes firmware to the
**WM160 aircraft (UpgradeModelType `UAV59AC`)** and its **RC (`UAV59RC`)**. Everything below is
filtered to WM160/UAV59. Where logic lives behind the native upgrade engine (`libupgrade_core.so`)
or the app packer it is called out explicitly with the exact class / Frida hook to use.

Evidence base: `unpacked_app_dex/*.dex` (16 DEX, whole app Java), disassembled with `baksmali`;
`DUML_COMMANDS_FULL.md`, `full_table.txt`, `cmdmap.txt`; `decompiled/lib/arm64-v8a/`.

---

## 0. TL;DR / most important finding

**The entire firmware-upgrade engine for WM160 is native.** All of `uav.upgrade.generate.*Manager`
(`FirmwareManager`, `CompositeManager`, `ConfigManager`) are thin `CppProxy` JNI stubs
(`declared native`) that call into **`libupgrade_core.so`** (loaded via `libupgrade_jni.so`).
Server fetch, MD5/signature verification, anti-rollback ("is_down_upgrade"), the DUML `cmd_set`/`cmd_id`
selection and the actual firmware byte-transfer to the aircraft are all decided **inside that .so** —
they are **NOT** statically decidable from Java. The Java layer only supplies parameters
(`UpgradeVersionCommonParam`), receives progress/state callbacks, and drives UI.

The DUML packing classes that *do* exist in Java (`DataCommonTransferFileData`,
`DataCommonTransferFileVerify`, `DataCommonControlUpgrade`, `DataCommonGetPushUpgradeStatus`,
`DataCommonRestartDevice`, `DataCommonTranslate*`) are the **legacy P3 midware** path and give us the
wire layout of the transfer protocol, but for WM160 the same protocol is driven from native code, so
the exact on-wire cmd used at runtime must be confirmed with a live capture.

---

## 1. Product identity — WM160 = UAV59

`Luav/upgrade/UpgradeModelType;` (DEX `classes_0231800c.dex`) — enum, ctor `<init>(String name, int ordinal, int typeCode)`:

| enum | ordinal | typeCode (3rd ctor int) | meaning |
|---|---|---|---|
| `Unknown` | 0 | 0x18? (see note) | — |
| `UAV59AC` | 1 | **0x18 (24)** | **WM160 aircraft** |
| `UAV59RC` | 2 | **0x19 (25)** | **WM160 remote controller** |
| `UAV67AC` | 3 | 0x1a (26) | Mini 2 / other — NOT-WM160 |
| `RCPigeonUAV67VerA` | 4 | 0x1b (27) | NOT-WM160 |
| `RCSparrowUAV67VerA` | 5 | 0x1c (28) | NOT-WM160 |
| `OPR56RC` | 6 | 0x28 (40) | NOT-WM160 |

Mapping from device strings (`UpgradeModelType.getUAVUpgradeModelTypeFromDroneInfo` /
`getUAVUpgradeModelTypeFromRCInfo`):
- Drone string `"UAV59"` → `UAV59AC`  (WM160)
- Drone string `"UAV67"` → `UAV67AC`
- RC string `"UAV59_rc"` → `UAV59RC`  (WM160)
- RC strings `"rcp231"`/`"rcs231"`/`"OPR56"` → Pigeon/Sparrow/OPR56 (NOT-WM160)

So **any firmware path for WM160 is keyed on the token `UAV59`.**

---

## 2. Java class map (who calls what)

Package `uav.upgrade.*` (DEX `classes_0231800c.dex`):

- **`uav/upgrade/UpgradeFirmwareManager`** — Java facade used by the UI.
- **`uav/upgrade/UpgradeCompositeManager`** — Kotlin coroutine wrapper; entry points
  `startSelfUpgrade(info, is_down_upgrade)`, `startUpgrade(...)`,
  `startUpgradeFirmwareByArchivePath(...)`. The literal `"is_down_upgrade"` boolean parameter here is
  the **anti-rollback / allow-downgrade flag** passed down to native.
- **`uav/upgrade/generate/FirmwareManager`** + `FirmwareManager$CppProxy` — **native** engine
  (all methods `declared native`, back native ref). Key native entry points:
  - `CheckFirmwareUpgradeState(Component, cb)` / `CheckFirmwareUpgradeStateWithServer(ComponentTypeComposite, ConfigFileInformation, UpgradeVersionCommonParam, cb)` — version check.
  - `FetchServerList / FetchFirmwareInformation / FetchLastFirmwareInformation / FetchAppVersionList` — server metadata fetch.
  - `StartDownload / StartDownloadMulti / StopDownload*` — firmware download (HTTP, native).
  - `IsFirmwareDownload / IsFirmwareDownloadV2` — cache check.
  - `CheckUpgradableStatus(Component, ErrorsCallback)` — gating (battery, connection, etc.).
  - `GetBatteryThresholdInPercentage(UpgradeComponentProductType)` — returns min battery map.
  - `ParseStdUpgradeErrorCode(str,str)` — decode native error.
  - `SetFirmwareUpgradeStateCallback / SetImageSwitchRequestObserver`.
- **`uav/upgrade/generate/CompositeManager`** (native) — the **push-to-aircraft** stage:
  - `StartUpgrade(ArrayList, ResultCallback)` — push downloaded firmware to device.
  - `StartUpgradeFirmwareByArchivePath(ArrayList, cb)` — install a local `.bin`/archive by path.
  - `StartSelfUpgrade(ComponentInformation, boolean isDown, cb)` — single-component upgrade with downgrade flag.
  - `StartImageSwitch / TriggerImageSwitch / CancelImageSwitchRequest` — A/B image switching.
  - `SetUpgradeStateObserver(CompositeProgressObserver)` / `SetDepressStateObserver(DecompressProgressObserver)`.
- **`uav/upgrade/generate/ConfigManager`** (native) — server-URL / config selection
  (`UpgradeServerUrlMode` Debug/Official/Unknown).

Higher-level component layer (`uav.component.firmwareupgrade.*`, DEX `classes_00b9d00c.dex`):
- `IFirmwareUpdateManager`, `IFirmwareUpgradeService`, `IOTAUpgradeService`, `IUpgradeStateMachine`,
  `ImageSwitchInterface`, `EmptyUpdateManager`.
- Config: `FirmwareProductConfig` (holds a volatile `ModuleConfig`), `FirmwareUpdateConfig`.
- Models: `ModuleConfig`, `UpgradeSubModule`, `Image`, `CSDKImage`, `CSDKModel`, `ImageMatchType`,
  `OTAUpgradeInfo`, `OTAUpgradeInfoState`, `SleepState`, `BatteryLimit`, `DownloadRequest`,
  `DownloadProgressInfo`.
- Errors: `UAVUpdateDeviceError`, `UAVUpdateAppError`, `UpdateBatteryLowError`,
  `NeedVerifyUpdateBatteryLowError`, `UpdateCheckException`, `UpdateProgressException`,
  `UpdateMultiProgressException`, `UpdateErrorRuler`.
- Custom UI behaviors: `uav.component.firmwareupgrade.custom.*` (CheckUpdates/Loading/Result behaviors).
- **`com/uav/productrgnzui/FirmwareUnsupportedActivity`** — screen shown when the connected model has no
  firmware-upgrade support entry.

---

## 3. The upgrade flow (end to end, WM160)

```
1. CONNECT + IDENTIFY   device SN/version → UpgradeModelType.UAV59AC (drone) / UAV59RC (rc)
2. BUILD PARAMS         UpgradeVersionCommonParam { mDroneType, mDroneSn, mDroneVersion,
                        mRcType, mRcSn, mRcVersion, mBattery*, mAppVersion, mPlatform, mCountry }
3. VERSION CHECK        FirmwareManager.CheckFirmwareUpgradeStateWithServer(...)   [native + HTTPS]
                        → FirmwareState { Init, Checking, UpToDate, NeedUpdate, NeedForceUpdate, Unknown }
4. FETCH META           FetchServerList / FetchFirmwareInformation (ConfigFileInformation, urls, md5)
5. DOWNLOAD             FirmwareManager.StartDownload*(...)   [native HTTPS → local cache]
                        DownloadState { Init, Downloading, DownloadSuccess, DownloadFailure }
6. DECOMPRESS           CompositeManager.SetDepressStateObserver
                        DecompressState { Init, Decompressing, DecompressSuccess, DecompressFailed }
7. GATE                 CheckUpgradableStatus → battery ≥ threshold (default 30%), link OK, not flying
8. PUSH / INSTALL       CompositeManager.StartUpgrade / StartSelfUpgrade(info, is_down_upgrade) /
                        StartUpgradeFirmwareByArchivePath   [native → DUML transfer to aircraft]
                        State { Init, Transferring, TransferSuccess, TransferEnd, TransferFailed,
                                Upgrading, UpgradeSuccess, UpgradeFailed }
9. DEVICE UPGRADES      device pushes status; app polls (see §5 UpgradeStep), then reboots device
10. VERIFY / REPORT     device reports new version; app confirms UAV59 == expected version
```

`UpgradeType` (`uav/upgrade/generate/UpgradeType`): `Normal`, `Recover`, `Consistent`, `ImageSwitch`.
- `Recover` = force/rescue re-flash; `Consistent` = firmware-consistency alignment across modules;
- `ImageSwitch` = A/B slot switch (see §6).

`UpdateTotalProcessState` / `UpgradeTotalProcessInformation` aggregate multi-module progress for UI.

---

## 4. DUML transfer protocol (legacy P3 packing classes — the wire layout)

These live in `uav/midware/data/model/P3/` (DEX `classes_0451d00c.dex`). Per `DUML_COMMANDS_FULL.md`
they are **cmd_set 0x00 (general), recv = APP**. This is the classic DJI "common file transfer +
control upgrade" protocol. For WM160 the native engine drives the equivalent; these classes give the
byte format.

### 4.1 File data transfer — cmd_set `0x00`, cmd_id `0x26` (38)
`DataCommonTransferFileData` / `...Extended` / `...RealTimeData`
Request payload:
- `+0` 1B `mCmdType`  (chunk type)
- `+1` 4B u32 LE `mSequence`  (chunk index)
- `+5` NB payload bytes (firmware slice)
- RealTime variant adds `+5` 1B `isLastSequence`, `+6` 2B u16 LE `packSize`.

### 4.2 File verify (MD5) — cmd_set `0x00`, cmd_id `0x26` (38)
`DataCommonTransferFileVerify`
- `+0` 1B `mCmdType`
- `+1` **16B `mVerifyData`**  → **MD5 (128-bit) checksum of the transferred file**.
`mVerifyType` ∈ `ITransferFileObject$CommonTransferVerifyType {a=0, b=1, c=2, d=3}` (obfuscated names;
selects which verification stage/algorithm variant — resolve live).
Timeouts in the class: `0xa` (10) and `0x1388` (5000 ms).

### 4.3 Control upgrade — cmd_set `0x00`, cmd_id `0x41` (65)
`DataCommonControlUpgrade`, payload `+0` 1B `controlCmd`. Enum
`DataCommonControlUpgrade$ControlCmd` (value == ordinal):

| controlCmd | byte |
|---|---|
| `Cancel` | 0x00 |
| `Start` | 0x01 |
| `Pause` | 0x02 |
| `Stop` | 0x03 |
| `StopPush` | 0x04 |
| `Restart` | 0x05 |
| `OTHER` | (sentinel, code 7) |

Also carries target routing: `mReceiveType` (`DeviceType`) + `mReceiveId` — i.e. the app addresses the
control to a specific module (FC/gimbal/camera/RC).

### 4.4 Push upgrade status — cmd_set `0x00`, cmd_id `0x41` (65)
`DataCommonGetPushUpgradeStatus`, payload `+0` 1B `upgradeStep`. Step enum
`DataCommonGetPushUpgradeStatus$UAVUpgradeStep` (value == ordinal):

| step | byte |
|---|---|
| `UserConfirm` | 0x00 |
| `Upgrading` | 0x01 |
| `DataUpgrading` | 0x02 |
| `Verify` | 0x03 |
| `Complete` | 0x04 |
| (sentinel/other) | 0x05 / 0x64 |

Helper `isBatteryFailed()` decodes a battery-related failure inside the push.
`DataCommonGetPushUpgradeStatus$UAVUpgradeStep` is the aircraft variant; there is also a camera variant
`DataCameraGetPushUpgradeStatus$UpgradeStep`.

### 4.5 Encrypted-block transfer (alt path) — cmd_set `0x00`
`DataCommonTranslateData` (cmd_id `0x09`): `+0` 1B `mEncrypt`, `+1` 4B u32 LE `mSequence`,
`+5` 2B u16 LE `mSize`. Completion `DataCommonTranslateComplete` (cmd_id `0x0A`): `+0` 1B `mEncrypt`,
`+1` 16B `mMd5` (conditional) — another MD5-terminated transfer variant.

### 4.6 Restart device — cmd_set `0x00`, cmd_id `0x0B` (11)
`DataCommonRestartDevice`: `+0` 1B `mEncrypt`, `+1` 1B `mRestartType`, `+2` 4B u32 LE `mDelay`.
Used to reboot the aircraft/RC into the new firmware after install.

---

## 5. Firmware-related DUML commands present in the map (WM160 general set)

From `full_table.txt` / `cmdmap.txt` (cmd_set 0x00 = general unless noted). These are **name-only in the
DUML map** (no dedicated app packing class) except where a P3 class is listed above — so their exact
payloads must be confirmed live for WM160:

| cmd_set/cmd_id | name | purpose |
|---|---|---|
| `0x00/0x2A` (42) | `uav_general_general_file_transfer_req` | generic file push (firmware chunks) |
| `0x00/0x72` (114) | `uav_general_set_upgrade_notification_req` | tell device upgrade is starting/ongoing |
| `0x00/0x8C` (140) | `uav_general_download_status_push` / `..._file_download_status_push_rsp` | download-status push |
| `0x00/0x96` (150) | `uav_general_get_enter_force_upgrade_req` | query/enter **forced upgrade** mode |
| `0x00/0xA5` (165) | `uav_general_get_switch_upgrade_bin_req` | **switch active upgrade bin (A/B image)** |
| `0x00/0x61` (97) | `DataCommonSetNewestVersions` | push newest-version list `{plist, product_id}` to device |
| `0x00/0x70` (112) | `DataCommonPushFwAnalytics` | firmware analytics push |
| `0x06/0x79` (121) | `uav_rc_get_get_rc_firmware_info_req` | **read RC (UAV59RC) firmware info** |

NFZ/GEO **database** upgrade is a *separate* subsystem (`uav.upgrade.component.database.*`), not firmware:
- `0x03/0xBB` (187) `uav_fc_get_nfzdb_upgrade_status_query_req`
- `0x03/0xBC` (188) `uav_fc_get_nfzdb_upgrade_result_query_req`
- `0x03/0xBD` (189) `uav_fc_nfz_upgrade_exit_req`
Handled by `DatabaseUpgradeComponent` / `DataUgCompatUtil`; DBs are FlySafe/NFZ data, not module firmware.

> Transport note: DUML frames ride over the AOA/USB link (RC) or Wi‑Fi to the aircraft, same channel as
> the rest of the app's commands. There is **no evidence of a standalone FTP client in Java** — the
> "CFT/FTP-style" bulk transfer is the `0x26`/`0x09` chunked-DUML mechanism above, executed by native
> code for WM160.

---

## 6. Image switch (A/B slots) — WM160 relevance

`ImageType` (`uav/upgrade/generate/ImageType`) enumerates per-product image identifiers. Note the naming
is `OPR94Uav<N>` / `RcImageUav<N>` where `<N>` is the product number:
- Aircraft images present: `OPR94Uav73/75/76B/77/103/110/111/112/120/121/121AirUnit/126/152`.
- RC images present: `RcImageUav137/139/157/158/159/182/183`, plus `SpecialPairing`, `None`.

**There is no `Uav59` entry in `ImageType`.** The `OPR94Uav*`/`RcImageUav*` enum is used by newer
CSDK products that expose A/B image switching (Uav73+ ≈ later platforms). **WM160/UAV59 is a
single-image classic upgrade** driven through `CompositeManager.StartUpgrade` / the P3 transfer protocol,
**not** the `ImageSwitch`/`TriggerImageSwitch` path. Treat `ImageType`, `ImageSwitchRequestReason`
(`NeedSwitchAndNotInstalled`, `SwitchPairedFailureNeedUpgrade`), `ImageSwitchProgress` and
`0x00/0xA5 switch_upgrade_bin` as **NOT-WM160 in practice** unless a live capture proves WM160 uses them.

---

## 7. Verification, anti-rollback, and version check

- **Integrity:** MD5 (16-byte) checksums are the verification primitive visible in Java —
  `DataCommonTransferFileVerify.mVerifyData` (16B) and `DataCommonTranslateComplete.mMd5` (16B). The
  server-side `ConfigFileInformation` / `FirmwareInformation` carry the expected file metadata (md5,
  size, urls) — these are native structs, populated from the HTTPS config response.
- **Signature:** any cryptographic **signature** verification of the firmware image happens inside
  `libupgrade_core.so` and/or on the device; **no signature check is implemented in Java** (only MD5 is
  visible). Not statically decidable — see §9.
- **Anti-rollback / downgrade:** exposed as the boolean `is_down_upgrade` threaded through
  `UpgradeCompositeManager.startSelfUpgrade(info, is_down_upgrade)` →
  `CompositeManager.StartSelfUpgrade(ComponentInformation, boolean, cb)`. When true the engine is told to
  allow installing an **older** firmware. Whether the *device* honors/blocks the downgrade is enforced in
  firmware, not the app.
- **Version check:** `CheckFirmwareUpgradeStateWithServer(...)` returns `FirmwareState`
  `{UpToDate | NeedUpdate | NeedForceUpdate | Checking | Init | Unknown}`. `NeedForceUpdate` +
  `0x00/0x96 get_enter_force_upgrade` = mandatory-upgrade gate that can block normal use until updated.

---

## 8. Gating (what blocks an upgrade) — WM160

- **Battery threshold:** `uav/component/firmwareupgrade/model/BatteryLimit` — default constant
  `e = 0x1e` = **30 %** minimum; per-product map from native
  `FirmwareManager.GetBatteryThresholdInPercentage(UpgradeComponentProductType)`. Below threshold →
  `UpdateBatteryLowError` / `NeedVerifyUpdateBatteryLowError`.
- **Upgradable status:** `CheckUpgradableStatus(Component, ErrorsCallback)` (native) returns a list of
  blocking errors (connection, in-flight, sub-module state).
- **Error taxonomy (native, decoded via `ParseStdUpgradeErrorCode`):**
  - `StdErrorSourceType`: `FwFc, FwGimbal, FwCamera, FwRc, FwBattery Box, FwBeacon, FwVideo, FwGlass,
    FwDongle, FwUpgradeCenter, Server, Network, SwApp, SwCsdk, SwUpgradecore, Unknown, None`.
  - `StdErrorModuleType`: `Upgrade, Video, Unknown, None`.
  - `StdErrorStageType`: fetch/download/transfer stages, e.g. `FetchServerCfgFailed`,
    `FetchServerVersionListFailed`, `DownloadError`, `DownloadCheckError`, `TransferDataError`,
    `DeviceUpgradeStatusInfoRequestError`, `CheckDeviceUpdateFailed`, `DatalinkReverseToUploadError`,
    `DatalinkReverseToNormalError`, etc. (full list in `generate/StdErrorStageType.smali`).
- App-vs-device split: `UAVUpdateAppError` vs `UAVUpdateDeviceError` (`UpdateErrorRuler` decides).
- **`FirmwareUnsupportedActivity`** is shown if the connected model isn't in the supported set — WM160/
  UAV59 **is** supported (has `UAV59AC`/`UAV59RC` model types and the `UAV59` string mapping).

---

## 9. What is NOT statically decidable (needs live capture / Frida)

Because the engine is native (`libupgrade_core.so` + `libupgrade_jni.so`, arm64-v8a), confirm the
following at runtime:

1. **Exact DUML `cmd_set`/`cmd_id` and payload the native engine actually sends to WM160** during
   push/verify/restart (the §4 P3 layout is the legacy reference, not proof of the runtime frame).
   → Hook the DUML send path or sniff USB/AOA. Frida: trace exports of `libupgrade_core.so`;
   also hook the app's DUML sender used by the JNI (breakpoint on the transport `send([B)` in the
   midware once the native call fans out).
2. **Firmware server hostnames / HTTPS endpoints and the config JSON schema** (md5, url, min-version,
   force flag). No fw-download host string is present in the DEX; it is built natively / returned by the
   config API. → Hook `FirmwareManager.FetchServerList / FetchFirmwareInformation` results, or MITM the
   TLS the .so makes (`FetchServerCfg*` stages).
3. **Whether firmware carries a real signature check** beyond MD5, and **where** (native vs device).
   → Hook the verify function inside `libupgrade_core.so`; inspect a downloaded firmware archive.
4. **The `.bin`/archive format** and how `StartUpgradeFirmwareByArchivePath` parses it. → Hook that
   native method with the local path; dump the cached firmware from app storage.
5. **`is_down_upgrade` real effect** — whether the device accepts a downgrade. → Set the flag via
   `UpgradeCompositeManager.startSelfUpgrade(info, true)` and observe device response.
6. **`CommonTransferVerifyType {a,b,c,d}`** semantics (obfuscated) and which the WM160 path uses.

Concrete Frida targets (Java side):
- `Luav/upgrade/generate/FirmwareManager$CppProxy;` — hook `CheckFirmwareUpgradeStateWithServer`,
  `FetchFirmwareInformation`, `StartDownload`, `GetBatteryThresholdInPercentage`.
- `Luav/upgrade/generate/CompositeManager;` — hook `StartUpgrade`, `StartSelfUpgrade`,
  `StartUpgradeFirmwareByArchivePath`, `SetUpgradeStateObserver`.
- `Luav/upgrade/UpgradeCompositeManager;` — hook `startSelfUpgrade` (see the `is_down_upgrade` arg).
- `Luav/midware/data/model/P3/DataCommonControlUpgrade;->start()` and
  `DataCommonTransferFileVerify;->start()` — if the legacy path is exercised, these show the live frame.

---

## 10. WM160 support matrix (summary)

| Capability | WM160 (UAV59) | Evidence |
|---|---|---|
| Aircraft firmware upgrade | **YES** — `UAV59AC`, string `UAV59` | `UpgradeModelType` |
| RC firmware upgrade | **YES** — `UAV59RC`, string `UAV59_rc` | `UpgradeModelType`, `0x06/0x79` rc fw info |
| Classic chunked-DUML transfer + MD5 verify | **YES (protocol path)** | `DataCommonTransferFileData/Verify` `0x00/0x26` |
| Control (start/pause/stop/restart) | YES | `DataCommonControlUpgrade` `0x00/0x41` |
| Force upgrade / mandatory | supported by protocol | `0x00/0x96`, `FirmwareState.NeedForceUpdate` |
| Anti-rollback flag exposed | YES (`is_down_upgrade`) | `UpgradeCompositeManager` / `StartSelfUpgrade` |
| A/B image switch (`ImageType`, `switch_upgrade_bin`) | **NOT-WM160** (no `Uav59` image entry; CSDK/Uav73+ only) | `ImageType` enum, §6 |
| NFZ/GEO database upgrade | separate subsystem (not firmware) | `uav.upgrade.component.database.*`, `0x03/0xBB-BD` |
| Engine location | **native** `libupgrade_core.so` | `*Manager$CppProxy` all `native` |

**Risks / cautions:** (a) only **MD5** integrity is visible in Java — treat firmware authenticity as
device/native-enforced, unverified here; (b) `is_down_upgrade` can request downgrades — device-side
anti-rollback is the only guard and is untested statically; (c) `StartUpgradeFirmwareByArchivePath`
installs a **local file by path** — a powerful primitive worth auditing for WM160 with a crafted archive
(unknown format/signing) via Frida; (d) all server URLs and the true wire frames are native → any
PC-control reimplementation for WM160 must be built from a **live capture**, not from this Java layer.
