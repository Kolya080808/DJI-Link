# DOMAIN: logs_sim_record — Logs, Black-box, Simulator & Diagnostics (WM160 / Mavic Mini 1 = UAV59)

Scope: flight-record / flight-log pull-and-replay path, the built-in **simulator** (DUML cmd_set `0x0B`),
the **black-box** commands (cmd_set `0x01` special `0x82/0x83/0x84`), and the firmware **system-diagnostics
exec** (cmd_set `0x59`). Everything filtered to **WM160 = UAV59 = Mavic Mini 1**.

Evidence is cited to disassembled smali (baksmali of `unpacked_app_dex/*.dex`), `full_table.txt`,
`cmdmap.txt`, `DUML_COMMANDS_FULL.md`, and `isSupport_keys.txt`. Where a value cannot be resolved
statically (native packer / keyvalue → DUML mapping in `.so`), it is flagged with the exact Frida hook.

> **TL;DR / most important finding:** The app **does not build black-box (`0x01/0x82-84`) or
> system-diag (`0x59`) DUML frames at all** — those symbols exist only in the aircraft's firmware
> symbol table (`full_table.txt`), with **no packing class in any DEX**. What the WM160 app actually
> uses is (a) the **simulator** cmd_set `0x0B` (enum `CmdIdSimulator$CmdIdType`, 21 wire IDs), and
> (b) a **flight-record file-transfer RPC** over FLYC `0x03/0xD7` (`DataFlycGetPushFlightRecord`).
> Two pre-existing doc values are **wrong** and corrected below (SetGetWind = `0x07` not `0x04`;
> flight-record RPC = `0x03/0xD7` not `0x03/0xCF`).

---

## 0. Command-set map for this domain

| cmd_set | dec | app enum class | in-app builder? | domain role |
|---|---|---|---|---|
| `0x0B` | 11 | `uav/midware/data/config/P3/CmdIdSimulator` (`CmdSet;->l` = `SIMULATOR`) | **YES** (partial) | built-in flight **simulator** |
| `0x01` | 1 | `uav/midware/data/config/P3/CmdIdSpecial` | **NO** for black-box | special/control; black-box `0x82-84` are firmware-only |
| `0x03` | 3 | `CmdIdFlyc` (`CmdSet;->d` = `FLYC`) | **YES** | flight-**record** RPC file transfer (`0xD7`) |
| `0x59` | 89 | *(none — no `CmdIdDiag`)* | **NO** | firmware system-diag exec; **not in app** |

`CmdSet` enum ordinal→name is verified in `out/uav/midware/data/config/P3/CmdSet.smali`
(`COMMON,SPECIAL,CAMERA,FLYC,GIMBAL,CENTER,RC,WIFI,DM368,OSD,EYE,SIMULATOR,BATTERY,…`), so field
`l` (ordinal 11) = `SIMULATOR` = wire `0x0B`, field `d` (ordinal 3) = `FLYC` = wire `0x03`.

---

## 1. SIMULATOR — DUML cmd_set 0x0B

### 1.1 Full command enum (authoritative, from app)

Source: `unpacked_app_dex/classes_016b200c.dex` →
`uav/midware/data/config/P3/CmdIdSimulator$CmdIdType.smali`. The enum ctor is
`(<name>, <ordinal>, <cmd_id> [,bool,Class])`; names taken from the in-order `const-string`, cmd_id
from the constructor int. **These are the app's own names** and differ from the firmware
`full_table.txt` names (`uav_simulator_get_sim_scan/para/command`).

| enum field | name | cmd_id (hex) | dec | app builder class | direction |
|---|---|---|---|---|---|
| `a` | `GetPushConnectHeartPacket` | `0x01` | 1 | `DataSimulatorGetPushConnectHeartPacket` | app→FLYC + FLYC→app push |
| `b` | `RequestMainControllerParams` | `0x02` | 2 | *(no builder)* | app→FLYC |
| `c` | `GetPushMainControllerReturnParams` | `0x03` | 3 | `DataSimulatorGetPushMainControllerReturnParams` (unpack-only) | FLYC→app push |
| `d` | `SimulateFlightCommend` | `0x04` | 4 | *(no P3 builder — see §1.4)* | app→FLYC **start/stop** |
| `e` | `GetPushFlightStatusParams` | `0x06` | 6 | `DataSimulatorGetPushFlightStatusParams` (unpack-only) | FLYC→app push |
| `f` | `SetGetWind` | `0x07` | 7 | `DataSimulatorSetGetWind` | app→FLYC |
| `g` | `SetGetArea` | `0x08` | 8 | *(no builder)* | app→FLYC |
| `h` | `SetGetAirParams` | `0x09` | 9 | *(no builder)* | app→FLYC |
| `i` | `ForceMoment` | `0x0A` | 10 | — | app→FLYC |
| `j` | `SetGetTemperature` | `0x0B` | 11 | — | app→FLYC |
| `k` | `SetGetGravity` | `0x0C` | 12 | — | app→FLYC |
| `l` | `CrashShutDown` | `0x0D` | 13 | — | app→FLYC |
| `m` | `CtrlMotor` | `0x0E` | 14 | — | app→FLYC |
| `n` | `Momentum` | `0x0F` | 15 | — | app→FLYC |
| `o` | `SetGetArmLength` | `0x10` | 16 | — | app→FLYC |
| `p` | `SetGetMassInertia` | `0x11` | 17 | — | app→FLYC |
| `q` | `SetGetMotorSetting` | `0x12` | 18 | — | app→FLYC |
| `r` | `SetGetBatterySetting` | `0x13` | 19 | — | app→FLYC |
| `s` | `GetFrequency` | `0x14` | 20 | — | app→FLYC |
| `t` | `ResetAll` | `0xFF` | 255 | — | app→FLYC |
| `u` | `Other` | `0x1FF` | 511 | — | sentinel |

> Firmware `full_table.txt` only exposes symbols for `0x0B/0x01` (`get_sim_scan`), `0x0B/0x02`
> (`get_sim_para`), `0x0B/0x04` (`get_sim_command`). The app enum is **richer** than the firmware
> symbol dump; the extra IDs (`0x06`–`0x14`) are real physics/tuning sub-commands used by the
> simulator model, not visible in the stripped firmware table.

### 1.2 Verified payloads (app builders)

**`SetGetWind` — `0x0B/0x07`** (7-byte body).
Source: `uav/midware/data/model/P3/DataSimulatorSetGetWind.smali` — `pack()` uses
`CmdSet;->l` (`0x0B`) and `CmdIdSimulator$CmdIdType;->f` (`SetGetWind` = `0x07`).

```
+0  2B  u16 LE  mWindSpeedX
+2  2B  u16 LE  mWindSpeedY
+4  2B  u16 LE  mWindSpeedZ
+6  1B          mFlag        ; default 1 (ack); setAckFlag(bool) toggles
```

> **CORRECTION to `DUML_COMMANDS_FULL.md` line 724**, which lists `DataSimulatorSetGetWind` at
> `cmd_id 0x04`. That is **wrong**: `0x04` is `SimulateFlightCommend`; the wind builder resolves
> `CmdIdType;->f` = `SetGetWind` = **`0x07`**. The 7-byte layout itself is correct.

**`GetPushConnectHeartPacket` — `0x0B/0x01`.**
Source: `DataSimulatorGetPushConnectHeartPacket.smali` — `pack()` sets `Pack.m = CmdSet.l (0x0B)`,
`Pack.n = CmdIdSimulator$CmdIdType.a (0x01)`. One int field `flag`. This is the connection
heartbeat that keeps the sim session alive.

**Push-only (parsed, never built):** `DataSimulatorGetPushMainControllerReturnParams` (`0x03`) and
`DataSimulatorGetPushFlightStatusParams` (`0x06`) have no instance fields and only `unpack` logic —
they decode aircraft→app simulator telemetry (attitude / flight status) during a sim run.

### 1.3 SDK key-value abstraction (what UI actually calls)

Source: `classes_0451d00c.dex`. The app UI never touches `CmdIdSimulator` directly; it goes through
SDK keys that the **native packer** lowers to cmd_set `0x0B`:

- `UAVFlightControllerControlKey`: `StartSimulator`, `StopSimulator`
  (`.../key/flightcontroller/control/UAVFlightControllerControlKey.smali`).
- `UAVFlightControllerStateKey`: `IsSimulatorStarted`, `SimulatorState`
  (`.../key/flightcontroller/state/UAVFlightControllerStateKey.smali`).
- `SimulatorInitializationSettings` value fields: `latitude:Double`, `longitude:Double`,
  `satelliteCount:Integer` (`.../value/flightcontroller/SimulatorInitializationSettings.smali`).
- `SimulatorState` value fields: `areMotorsOn, isFlying, pitch, roll, yaw, positionX, positionY,
  positionZ, location` (`.../value/flightcontroller/SimulatorState.smali`).
- flymodel v1 impls: `com/uav/flymodel/generated/impl/flight/simulator/SimulatorModelImpl`,
  `SimulatorInfoModelImpl` (exposes `isSimulatorStarted`, `simulatorState`).

So the PC-control equivalent of "start the sim at lat/lon with N sats" =
`StartSimulator` key with a `SimulatorInitializationSettings`. Statically the exact `0x0B/0x04`
`SimulateFlightCommend` byte layout that this lowers to is **not resolvable** (it is emitted in the
native midware, no P3 builder). See §1.4.

### 1.4 The external "com.fly.simulator" app

Source: `uav/publics/utils/SimulatorAppUtil.smali` (+ `$Companion`). The in-app "simulator" entry
point can instead **launch a separate Android app** `com.fly.simulator` /
`com.fly.simulator.SimulatorActivity` (broadcast actions `ACTION_SIMULATOR_RUNNING`,
`SIMULATOR_ACTION`, notifications `SimulatorAppDidLaunchedNotification`), with a web-install
fallback `https://www.dji.com/downloads/djiapp/dji-simulator`. That standalone app is the one that
drives the aircraft over `0x0B`; it is not part of this Fly APK's code. **NOT-WM160-specific** —
this is a generic DJI simulator launcher.

### 1.5 WM160 support

No `isSupportSimulator`-style gate exists (`isSupport_keys.txt` has nothing for simulator). The
`StartSimulator`/`SimulatorState` keys are model-generic FlightController keys, so nothing in the
app blocks WM160. **Whether WM160 firmware actually accepts cmd_set `0x0B` is undecidable
statically** — Mini-class aircraft historically expose a reduced sim. **Needs a live check**:
send `0x0B/0x01` heartbeat or drive `StartSimulator`, watch for a `0x0B/0x03`/`0x06` push.

---

## 2. FLIGHT RECORD / FLIGHT LOG

### 2.1 The real download path — FLYC RPC `0x03/0xD7`

Source: `CmdIdFlyc$CmdIdType.smali` — enum entry `"GetPushFlightRecord"`, ordinal `0x64` (100),
**cmd_id `0xD7` (215)**, class `DataFlycGetPushFlightRecord`. `DataFlycGetPushFlightRecord.pack()`
uses `CmdSet;->d` (`0x03` FLYC) + `CmdIdFlyc;->V8` (= `GetPushFlightRecord` = `0xD7`).

> **CORRECTION to `DUML_COMMANDS_FULL.md` line 366**, which tags `DataFlycGetPushFlightRecord` as
> `0xCF (207)`. The enum field it packs (`V8`) is constructed with `0xD7`, so the actual wire
> cmd is **`0x03/0xD7` (215)**, matching `full_table.txt` `0x03/0xD7 uav_fc_recorder_rpc`. The
> paired firmware RPC channel `0x03/0x8F (143) uav_fc_recorder_rpc` has no app builder.

This is a **file-transfer RPC**, not a single request. Source
`DataFlycGetPushFlightRecord.smali` + `$CmdType.smali`:

Sub-op enum `DataFlycGetPushFlightRecord$CmdType` (`<name>, ordinal, data`):

| name | ordinal | `data` byte |
|---|---|---|
| `CreateFile` | 0 | `0x01` |
| `Write` | 1 | `0x02` |
| `Read` | 2 | `0x03` |
| `WriteConfig` | 3 | `0x04` |
| `AppRequest` | 4 | `0x07` |

Instance state carried in the frame: `mCmdType` (sub-op above), `mReceType`, `mReceId`, `mSeq`,
`mSessionIndex`, plus `result`. Observed constant combos in the builder: `(mReceType=3,mReceId=0)`,
`(mReceType=3,mReceId=6,mSeq=0)`, `(mReceType=8,mReceId=1,mSeq=0)`. The exact byte-offsets of the
composed body are **conditional / session-driven and best captured live** (the pack method branches
on `mCmdType.data` and writes `mSessionIndex` at a variable offset). **Frida hook:**
`uav.midware.data.model.P3.DataFlycGetPushFlightRecord->pack(...)` and dump `DataBase._sendData`.

`UAVFlightLogPackManager` (`classes_016b200c.dex`,
`uav/midware/data/manager/P3/UAVFlightLogPackManager.smali`) is the manager that sequences these
Create/Write/Read RPC ops into a downloaded flight-log file. `uav/flightrecord/jni/` callbacks
(`JNIFileEventCallback`, `JNIRecoveryEventCallback`) signal per-file progress from native.

### 2.2 High-level flight-record component (download + cloud upload)

Source: `classes_03a5700c.dex`, package `uav/component/flightrecord/`. This is the UI/service layer
that orchestrates §2.1 downloads and uploads to DJI cloud.

- `IFlightRecordService` (Rx interface, methods obfuscated) + `IFlightRecordComponent`.
- File-state listener: `IFlightRecordService$OnFlightRecordFileStateChangedListener`.
- Sync trigger value: `uav/sdk/keyvalue/value/product/DownloadCommonFileSyncMsg` (from
  `UAVProductKey`) — the key-value message that kicks a common-file (flight-record) sync.

**Enums with wire/string values:**

`DownloadState` (`uav/component/flightrecord/DownloadState.smali`):
`DOWNLOADING, DOWNLOAD_FAILED, DOWNLOAD_SUCCESS, DOWNLOAD_NO_NEED, UNKNOWN`.

`DownloadFailReason` (`DownloadFailReason.smali`):
`DOWNLOAD_LIST_EMPTY, NETWORK_ERROR, STORAGE_FULL, SERVER_ERROR, NO_SDCARD, NOT_SUPPORT, UNKNOWN`.

`FrUploadState` (`uav/component/flightrecord/model/`): sealed states
`Idle, Uploading, Success, Failed, Cancel`.

**Cloud upload / user-log side** (`classes_016b200c.dex`, `com/uav/service/userlog/…`):
`UserLogServiceImpl` handles `startDiagnosis`, `startListenSelfDiagnosisProgress`;
`LogUploadFlightRecordModel`, `BaseSelectedInfo$FlightRecordInfo`, and the picker UI
`select/flightrecord/FlightRecordSelectActivity/…ViewModel/…Repository`. Cloud sync models
`uav/component/flightrecordui/model/SyncModeRecordInfo` (`Data.Page`, `Data.Record`, `Result`) and
`SyncModeUserInfo`. Endpoint URLs are **not string-literals in these DEX** (built at runtime by the
network layer / behind config); capture with a network hook if the exact REST path is needed.

**Capability key:** `isSupportClearFlightRecord` is present in `isSupport_keys.txt` — WM160 exposes a
"clear flight record on aircraft" capability (queried per-model at runtime).

### 2.3 Black-box `0x01/0x82-0x84` — firmware-only, NOT in app

`full_table.txt` / `cmdmap.txt` list:
- `0x01/0x82 (130)` `uav_special_control_blackbox_folder_req`
- `0x01/0x83 (131)` `uav_special_get_get_blackbox_info_req`
- `0x01/0x84 (132)` `uav_special_set_set_blackbox_info_req`

But the app's `CmdIdSpecial$CmdIdType` enum contains **only** `Control (0x01)`,
`JoySitckSetParams (0x02)`, `NewControl (0x03)`, `LockRcControl (0xF0)`, `Other (0x1FF)` — **no
black-box entries** (`CmdIdSpecial$CmdIdType.smali`). There is **no P3 model** that packs `0x82/83/84`.

**Conclusion:** black-box folder/info commands are **not issued by the Fly app for any model,
including WM160.** They are service/factory-tool or firmware-internal. To use them you must craft the
DUML frames yourself; layouts are **not recoverable from this APK** (name-only). WM160: **NOT-app-supported.**

Related but different: `uav/sdk/keyvalue/value/batterybox/BlackBox*` (`BlackBoxLogCountMsg`,
`BlackBoxLogDataParam`, `BlackBoxTargetType`) — these are **smart-battery/charger black-box** log
values, unrelated to the FLYC flight black-box, and are NOT-WM160 flight-record features.

---

## 3. SYSTEM DIAGNOSTICS — cmd_set 0x59 (firmware) vs HMS (app)

### 3.1 cmd_set `0x59` — NOT implemented in the app

`full_table.txt` `CMD_SET 0x59 (89) [diag]`:

| cmd | dec | req symbol |
|---|---|---|
| `0x01` | 1 | `uav_diag_sys_diag_mode_switch_req` |
| `0x02` | 2 | `uav_diag_get_sys_diag_capability_req` |
| `0x03` | 3 | `uav_diag_get_sys_diag_keep_alive_req` |
| `0x04` | 4 | `uav_diag_sys_diag_execute_req` |
| `0x05` | 5 | `uav_diag_sys_diag_terminate_req` |
| `0x07` | 7 | `uav_diag_get_diag_result_cmd` |

There is **no `CmdIdDiag` config class**, **no `CmdSet` enum entry** for `0x59`, and **no P3 model**
that references these (`grep sys_diag/SysDiag/CmdIdDiag` over `classes_016b200c` + `classes_0451d00c`
= empty). This "diagnostic exec" mode is a **firmware / DJI-Assistant / factory** interface. WM160:
the Fly app **cannot** drive `0x59` — you would send frames manually (layouts name-only, not in APK).

### 3.2 What the app calls "diagnostics" (HMS) — a different system

The app's diagnostics is **health-management (HMS)**, delivered via **key-value + push**, NOT cmd_set
`0x59`:
- `uav/sdk/diagnostics/UAVDiagnosticsManager` + JNI `uav/sdk/diagnostics/jni/DiagnosticsManager`.
- value objects `uav/sdk/keyvalue/value/diagnostic/`: `Diagnostic`, `DiagnosticCode`,
  `DiagnosticComponentType`, `DiagnosticLevel`, `DiagnosticType`, `DisplaySceneType`,
  `HMSDiagnostic`, `HMSDetailDiagnostic`.
- FC self-diagnostic keys: `UAVFlightControllerControlSelfDiagnosticKey`,
  `UAVFlightControllerStateSelfDiagnosticKey`; values `SelfDiagnosticAction`,
  `SelfDiagnosticActionRequestInfo`.
- `UserLogServiceImpl.startDiagnosis()` (self-diagnosis progress) drives the log-upload/HMS UI.

This HMS layer is generic and applies to WM160, but it is **out of the `0x59` wire scope** — treat it
as a separate telemetry/keyvalue channel, not a DUML exec interface.

---

## 4. WM160 support summary

| Feature | WM160 status | Evidence |
|---|---|---|
| Simulator cmd_set `0x0B` (enum, wind, heartbeat, telemetry pushes) | **App-supported, generic** — firmware acceptance unverified | `CmdIdSimulator`, `DataSimulator*`, `StartSimulator` key |
| Simulator start/stop (`0x0B/0x04 SimulateFlightCommend`) | **App-supported via native keyvalue**; exact bytes need live capture | `UAVFlightControllerControlKey.StartSimulator/StopSimulator` (no P3 builder) |
| External `com.fly.simulator` launch | Generic launcher, not WM160-specific | `SimulatorAppUtil` |
| Flight-record download RPC `0x03/0xD7` | **App-supported** | `DataFlycGetPushFlightRecord`, `UAVFlightLogPackManager` |
| Flight-record component (download state, cloud upload) | **App-supported** | `uav/component/flightrecord/*`, `userlog/*` |
| Clear flight record | **Capability key present** | `isSupportClearFlightRecord` |
| Black-box `0x01/0x82-0x84` | **NOT app-supported (any model)** | absent from `CmdIdSpecial$CmdIdType` |
| System-diag exec `0x59` | **NOT app-supported (any model)** | no `CmdIdDiag`/`CmdSet`/model |
| HMS self-diagnostics (keyvalue) | App-supported, generic | `UAVDiagnosticsManager`, `diagnostic/*` values |

---

## 5. What still needs a live capture / Frida

1. **`0x0B/0x04 SimulateFlightCommend` start/stop byte layout** — emitted by the native midware from
   the `StartSimulator`/`StopSimulator` keys; no Java builder. Hook the key set or the native pack.
   Java-side hook: `uav.midware.data.model.P3.DataSimulatorSetGetWind->pack` for the wind reference,
   but start/stop must be sniffed on the wire (USB/AOA DUML) or via the native `.so`.
2. **Whether WM160 firmware accepts cmd_set `0x0B` at all** — Mini-class may reject or reduce it. Send
   `0x0B/0x01` heartbeat / drive `StartSimulator`, observe `0x0B/0x03` or `0x0B/0x06` push replies.
3. **Full `DataFlycGetPushFlightRecord` (0x03/0xD7) RPC body** — session/offset fields are computed
   per sub-op. Hook `uav.midware.data.model.P3.DataFlycGetPushFlightRecord->pack(...)` and dump
   `uav.midware.data.manager.P3.DataBase._sendData`; also hook `UAVFlightLogPackManager` to see the
   Create→Write/Read sequence and file naming.
4. **Cloud upload REST endpoints** — `SyncModeRecordInfo`/`LogUploadFlightRecordModel` URLs are built
   at runtime, not literals. Capture with an OkHttp/network hook if the endpoint is required.
5. **Black-box `0x82-84` and diag `0x59` payloads** — unrecoverable from this APK (name-only firmware
   symbols); require firmware RE or a factory-tool capture.
