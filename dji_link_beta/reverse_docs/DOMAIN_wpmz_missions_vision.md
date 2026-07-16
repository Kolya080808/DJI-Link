# DOMAIN: wpmz_missions_vision — Waypoints, Missions & Vision-Tracking on the Mavic Mini 1 (WM160)

Scope: DJI Fly `dji.go.v5` v1.21.4, fully unpacked (16 DEX = `reverse_docs/unpacked_app_dex/`,
smali under `decompiled/`). Everything below is filtered to **WM160 = Mavic Mini 1 = UAV59 /
ProductType 0x3b (59)**. Evidence is cited to a class / smali / enum / `full_table.txt` line /
`DUML_COMMANDS_FULL.md` line. Where a value is only decidable at runtime (behind the aircraft's
capability push or the Bangcle packer), it is marked **[LIVE-ONLY]** with the exact Frida hook.

---

## 0. TL;DR verdict for WM160

| Feature | WM160? | Evidence / mechanism |
|---|---|---|
| **Waypoints / WPMZ (WaypointV2/V3, KMZ/WPML)** | ❌ **REJECTED** | Gated by dynamic FC key `KeyIsWaypointSupport` (`WaypointService.getWaypointSupportObservable`, smali line 2331–2349). WM160 firmware does not report it. `MASTER_REPORT.md §` "waypoint/WPMZ (Mini will reject)". |
| **Legacy WaypointV1 (DataFlycUploadWayPointMission)** | ❌ **REJECTED** | Builders exist (`0x03/0x82…0x87`, `DUML_COMMANDS_FULL.md` L331–336) but FC has no waypoint engine. |
| **ActiveTrack / Follow / Spotlight / POI (SmartEye)** | ❌ **REJECTED** | Gated by `KeyIsSmartEyeSupport` / `KeyIsMultiTargetSupport`. `MASTER_REPORT.md §`: "ActiveTrack/Follow-Me/tracking (no sensors)". WM160 has only a downward VPS (`ERROR_CODES.md` L51). |
| **QuickShots (Dronie/Circle/Helix/Rocket/Boomerang)** | ⚠️ **PARTIAL** | `MASTER_REPORT.md §`: "QuickShots (Mini partially)". Uses the vision track-select box (`0x0A/0x20`) internally. |
| **Video-SEI AI tracking metadata (dvtm / `TrackingInfo`)** | ❌ not emitted by WM160 | `com/lcti/mlace/proto/DvtmLibSdkProto` — SmartEye-only pipeline; **[LIVE-ONLY]** to confirm WM160 never sets it. |

**Single most important finding:** waypoint and tracking support are **not** a static per-model
table in this app — they are **dynamic capability keys the aircraft pushes** (`KeyIsWaypointSupport`,
`KeyIsSmartEyeSupport`, `KeyIsMultiTargetSupport`, `KeyIsFixedSpeedNavigationSupport`). The whole
`uav/sdk/wpmz` WPML SDK and `com/uav/waypoint` UI ship in the APK for every drone; WM160 simply
never flips those keys on, so the UI is hidden and the FC rejects the DUML. A PC ground-station
therefore cannot "unlock" these by sending the app's commands — the refusal is in FC firmware.

---

## 1. WPMZ / Waypoints

### 1.1 Two distinct waypoint stacks are present in the app

1. **Legacy WaypointV1** — the old FLYC-native waypoint mission (`cmd_set 0x03`), builders
   `DataFlycUploadWayPointMissionMsg` etc. Superseded; kept for old aircraft.
2. **WPMZ = WaypointV2/V3** — the modern **KMZ/WPML** file-based mission engine (`cmd_set 0x22`,
   FC2), driven by the `uav/sdk/wpmz` SDK and the `com/uav/waypoint` component.

`grep` over the DEX: `84×"WaypointV3"`, `7×"WaypointV2"`, `1×"WAYPOINT_V2"`. So the live UI is
WaypointV3 (KMZ). None of it is reachable on WM160 (see §1.5).

### 1.2 The WPML/KMZ SDK — `uav/sdk/wpmz` (`classes_016b200c.dex`)

This is DJI's WPML SDK (identical shape to DJI's public Cloud-API WPML spec). It is a **value/serde
layer only** — the actual KMZ read/write is native (JNI): the package has `wpmz/jni` (5 refs) plus
`value/ByteStream`, `value/ByteStreamHelper`, `value/BytesOffset`, `value/ByteResult` — i.e. Java
value objects are marshalled to/from a native byte buffer, so the XML/zip generation lives in a
`.so`, not in Java. **[LIVE-ONLY]** for exact on-wire KMZ bytes: hook the JNI in `uav/sdk/wpmz/jni`.

A KMZ is a zip containing `template.kml` (edit-time template) + `waylines.wpml` (executable
waylines). Key SDK model classes (`Luav/sdk/wpmz/value/mission/…`):

- Top mission tree: `AllKMZData`, `WaylineMission`, `WaylineMissionConfig`, `Wayline`,
  `WaylineExecuteWaypoint`, `WaylineTemplate`, `WaylineActionGroup`, `WaylineActionInfo`,
  `WaylineActionNodeList`, `WaylineActionTreeNode`, `WaylineActionTrigger`.
- Geometry: `WaylineLocationCoordinate2D` / `…3D`, `WaylineCoordinateParam`,
  `WaylineCoordinateMode`, `WaylineAltitudeMode`, `WaylineExecuteAltitudeMode`, `SurfaceFollowParam`.
- Payload/actions: `WaylinePayloadParam/Info/Type`, `WaylineCameraActuatorActionType`,
  `WaylineGimbalActuatorActionType/RotateMode`, `WaylineAircraftActuatorActionType`,
  `WaylineSprayActuatorActionType` (agriculture — **NOT-WM160**),
  `WaylineActionRecordPointCloudOperateType` (LiDAR — **NOT-WM160**),
  `WaylineTemplateMapping2DInfo / …3DInfo / …StripInfo` (mapping — **NOT-WM160**).
- Drone identity: `WaylineDroneInfo`, `WaylineDroneType`, `WaylineActionSmartOblique*`.
- Errors: `WPMLParseError(Msg)`, `WaylineCheckError(Msg)`.

Most of these branches (spray, LiDAR point cloud, oblique/mapping, agriculture `AgricultureWorkMode`)
are for enterprise/agri drones and are **NOT-WM160**. WM160 would only ever have used the plain
"waypoint + simple gimbal/photo action" subset — which it does not have at all.

### 1.3 The app waypoint component — `com/uav/waypoint` (613 classes, `classes_016b200c.dex`)

- `WaypointService` — public service; observables `getWaypointSupportObservable`,
  `isWaypointSupport`, `getOfflineWaypointSupport`, `getMissionSyncSupportObservable`,
  `getCameraModeSupport` (smali field list L32–73).
- `WaypointCenterManager` — mission store/runtime.
- `com/uav/waypoint/checker/WaypointCapabilityChecker` — validates a loaded mission against the
  connected aircraft's reported capabilities and **resets** unsupported settings
  ("reset setting by Capability", smali L2317). Sub-checkers under
  `checker/capabilityChecker/`: `ZoomCapabilityChecker`, `SpeedCapabilityChecker`,
  `GimbalPitchCapabilityChecker`, `GimbalRollCapabilityChecker`, `LostActionCapabilityChecker`.
- UI: `com/uav/fpv/component/mission/waypoint/ui/WaypointMainShell` / `WaypointMainVM`
  (`classes_00b9d00c.dex`) — subscribes `WayPointCapabilityChanged`, `WaypointRunError`, etc.

### 1.4 Waypoint enums (wire values from smali `<clinit>`)

Source: `com/uav/waypoint/base/enumInfo/*.smali`, `com/uav/waypoint/base/WpStartCheckType.smali`.

| Enum | Values (name = ordinal) |
|---|---|
| `WayLineFinishAction` | NoAction=0, RTH=1, Landing=2, FirstPoint=3 |
| `WayLineLostAction` (RC-lost) | RTH=0, Hover=1, Landing=2, Continue=3 |
| `WayLineMode` | Across=0, ByPass=1 |
| `HeadOrientation` (aircraft heading) | FollowWayLine=0, POI=1, Manual=2, Customize=3 |
| `GimbalOrientation` | POI=0, Manual=1, Customize=2 |
| `WaypointSpeed` | Global_Speed=0, Custom_Speed=1 |
| `CameraZoomAction` | Customize=0, Manual=1, Auto=2 |
| `WayPointCameraAction` | NONE=0, TakePhoto=1, StartRecord=2, StopRecord=3 |
| `WpStartCheckType` (pre-flight gate) | NO_ERROR=0, NO_SPACE=1, MOTOR_OFF=2, NO_BATTERY=3, HEIGHT_LIMIT=4, DISTANCE_LIMIT=5, NFC_LIMIT=6, NO_LOGIN=7, LOW_POWER_MODE=8, HOVER_MODE_NEED_SET=9 |

Other enums present (values follow declaration order): `GimbalRollMode`, `WayLineSequence`,
`MissionDownloadResult`, `SyncPromptType`.

### 1.5 The gate: how WM160 is excluded (the concrete mechanism)

`WaypointService.getWaypointSupportObservable()` (smali `classes_016b200c.dex`,
`WaypointService.smali` L2331–2349) reads the CSDK key:

```
UAVStdFlightControllerKey.e            (UAVFlightControllerDescKey)
  .o                                    (UAVKeyInfoGL)   -> "KeyIsWaypointSupport"
RxCSDK.j1(key, …)  ->  Observable<Boolean>
```

The overall support is a `combineLatest` of `KeyIsWaypointSupport` + connect-state +
intelligent-mode + a cached `waypoint_last_connect_support` flag. Sibling capability keys under the
same `UAVFlightControllerDescWaypointKey` (enumerated from DEX strings): `KeyIsCameraZoomConfigSupport`,
`KeyIsFixedSpeedSupport`, `KeyIsFixedSpeedNavigationSupport`, `KeyIsFixedSpeedNavigationCanAddingRc`,
`KeyIsFlyingSpeedSupported`, `KeyIsGimbalRollRotateForward`, `KeyWaypointMaxCount`, `KeyAddWaypoint`,
`KeyDeleteWaypoint`, `KeyFetchWaypoint`.

These are **get/listen keys the aircraft answers**; they are not hardcoded per model in the app.
WM160's FC returns "not supported", so the UI stays hidden and any DUML mission-start is refused.
**[LIVE-ONLY]** confirm value: Frida-hook `Lcom/uav/rx/csdk/RxCSDK;->j1` (or the KeyManager
getValue for `KeyIsWaypointSupport`) with WM160 connected; expect `false`.

### 1.6 DUML for waypoints (for completeness — all refused by WM160 FC)

**WPMZ (WaypointV3) — cmd_set 0x22 (FC2):** (`full_table.txt` L446–457)

| cmd_set/id | dec | name |
|---|---|---|
| 0x22/0x1D | 29 | `uav_fc2_get_get_waypoint_info` |
| 0x22/0x27 | 39 | `uav_fc2_get_API_WP2_GET_BREAK_POINT_INFO` |
| 0x22/0xAB | 171 | `uav_fc2_start_stop_wpmz_mission` |
| 0x22/0xAC | 172 | `uav_fc2_break_resume_wpmz_mission` |
| 0x22/0xAE | 174 | `uav_fc2_get_wp3_query_result` |
| 0x22/0xAF | 175 | `uav_fc2_get_wp3_query_breakpoint_info` |

`0xAB`/`0xAC` are **name-only** in the app's builder set (`DUML_COMMANDS_FULL.md` L908–909) — no
`Data*` packing class was found, so payload layout is **[LIVE-ONLY]** (capture on a supported drone,
e.g. Mini 3, and diff). The KMZ file itself is transferred to the FC out-of-band (file channel)
before `0xAB` starts it — exact transfer channel not decidable statically.

**FLYC waypoint speed — cmd_set 0x03:** `0x03/0x9C` set / `0x03/0x9D` get waypoint auto flight
speed (`full_table.txt` L189–190); builder `DataFlycWayPointSetIdleSpeed` (0x9C) = 4B f32 LE
`idleSpeed` (`DUML_COMMANDS_FULL.md` L355).

**Legacy WaypointV1 — cmd_set 0x03 (builders exist, `DUML_COMMANDS_FULL.md` L331–336):**
`0x82` UploadWayPointMission (51B header: count, cmdSpeed f32, idleSpeed f32, finishAction,
repeatNum, yawMode, traceMode, actionOnRCLost, gimbalPitchMode, hp lat/lng/height f64, gotoFirstFlag,
missionID u16), `0x83` download, `0x84` UploadWayPointByIndex (90B per point: index, lat/lng f64,
altitude f32, dampingDis, tgtYaw i16, tgtPitch i16, turnMode, wpSpeed, cameraActionType…),
`0x85` download-by-index, `0x86` MissionSwitch (1B cmd), `0x87` PauseOrResume (1B cmd),
`0x03/0xA3` GetWaypointInterruption. **All refused by WM160** (no FC waypoint engine).

---

## 2. Vision & tracking (SmartEye / ActiveTrack / Spotlight / POI / QuickShots)

### 2.1 Capability keys (dynamic, aircraft-pushed) — the gate

From DEX `KeyIs*` string scan:

- `KeyIsSmartEyeSupport`, `KeyIsSmartEyeSupported` — the whole SmartEye intelligent-tracking suite
  (ActiveTrack/Spotlight/POI/MasterShot). **WM160 = false → no ActiveTrack.**
- `KeyIsMultiTargetSupport`, `KeyIsMultiTrackingOn`, `KeyIsInSingleTracking` — multi-target tracking.
- `KeyIsFpvGimbalModeSupportedInSmartEye`, `KeyIsSmartEyeSpotlightModeMemorySupport`,
  `KeyIsSupportRollRotateByClickInSmartEye`.
- `KeyIsQuickShotCometSupport`, `KeyIsQuickShotInAction`, `KeyIsQuickShotSupportStartWithoutGPS`,
  `KeyIsQuickShotWaitToStartNeedConfirmState`, `KeyIsSupportRollRotateByClickInQuickShot` — QuickShots.
- `KeyIsHandHeld{Track,Spotlight,MasterShot,SmartPortrait,DollyZoom}FunctionSupported` — these are for
  **handheld Osmo-type gimbals, NOT-WM160** (drone, not handheld).

Component/UI classes: `com/uav/fpv/component/mission/smarteye/…` (the SmartEye panel; large
`SmartEyeCollapseOnSpotlightGate` / `…ViewModel` cluster in `classes_00b9d00c.dex`) — never shown on
WM160 because `KeyIsSmartEyeSupport=false`. **[LIVE-ONLY]** confirm via Frida on
`KeyIsSmartEyeSupport` getValue with WM160 attached.

### 2.2 Vision DUML — cmd_set 0x0A (VISION / SINGLE dev 0x11), app prefix `DataSingle*`/`DataEye*`

Builder-verified request payloads (little-endian) from `DUML_COMMANDS_FULL.md` L655–711 and command
names from `full_table.txt` L346–381. These are the real tracking commands; on WM160 they are only
issued inside QuickShots (partial) and otherwise unused.

| cmd_id | dec | app class | payload |
|---|---|---|---|
| 0x20 | 32 | `DataSingleSetTrackSelect` | 21B: `+0`u8 mMode; `+1`f32 centerX; `+5`f32 centerY; `+9`f32 width; `+13`f32 height; `+17`u8 exception; `+18`i16 sessionId; `+20`u8 const 0x4 |
| 0x22 | 34 | `DataSingleMoveTrackSelect` | 17B: u8 ctrlType + 4×f32 box |
| 0x24 | 36 | `DataSingleSetPointPos` | 11B: f32 posX, f32 posY, i16 sessionId, u8 tapMode |
| 0x27 | 39 | `DataSingleCommonCtrl` | 1B ctrlCmd |
| 0x31 | 49 | `DataEyeSendUserLocation` | 26B: lon f64, lat f64, Nspeed f32, Espeed f32, accuracy i16 |
| 0x49 | 73 | `DataEyeSendGPSInfo` | 34B: lon/lat f64, accuracy i16, alt f64, speed f32, bearing f32 |
| 0x94 | 148 | `DataEyeStartMultiTracking` | u8 trackingMode; u8 numberOfTracking (+ per-target boxes) |
| 0x97 | 151 | `DataEyeStopMultiTracking` | 2B: u8 cmdType, u8 numberOfTargets |
| 0x9A | 154 | `DataSmartEyeSelectTarget` | 4B: u8 version, u8 cmdType, u8 0x00, u8 index |
| 0xC0 | 192 | `DataEyeSetPOIStartWithGPS` | 28B: lat/lng f64, radius f32, height f32, velocity f32 |
| 0xC1 | 193 | `DataEyeSetPOIInitialTarget` | 20B: x/y/w/h f32, timeStamp u32 |
| 0xC3 | 195 | `DataEyeSetPOIAction` | 1B cmdId |
| 0xC4 | 196 | `DataEyeSetPOIParams` | 6B: id, len, value f32, len |
| 0xEC | 236 | `DataEyeSendGoHomeAction` | 2B: data, action |

Name-only (no builder, layout **[LIVE-ONLY]**): `0x1B` free-pano push, `0x5B`
switch_navigation_function, `0x5C` switch_fixed_speed, `0xBA` target_manager_cmd, `0xE7`
tracking_box_to_nav push, `0xF6` mastershot_set_param, `0xF9` multi_target_mastershot_param
(`full_table.txt` L375–381; `DUML_COMMANDS_FULL.md` L703–715). MasterShot (`0xF6/0xF9`) and
multi-target are SmartEye-gated → **NOT-WM160**.

Also present (subject box in camera set, not vision set): `DataCameraSetTrackingParms` `0x02/0xA6`
(5B, `DUML_COMMANDS_FULL.md` L191) — camera-side tracking parameters.

### 2.3 QuickShots on WM160 (the one partially-supported vision feature)

`MASTER_REPORT.md` states QuickShots are **partially** supported on WM160 (Dronie/Circle/Helix/
Rocket/Boomerang). A QuickShot selects its subject with the same vision track-select box
(`0x0A/0x20 DataSingleSetTrackSelect`) but runs a fixed camera-move recipe rather than free
follow. QuickShot state keys: `KeyIsQuickShotInAction`, `KeyIsQuickShotSupportStartWithoutGPS`,
`KeyIsQuickShotWaitToStartNeedConfirmState`. This is the only vision-tracking path a PC
ground-station can realistically exercise on WM160; **[LIVE-ONLY]** confirm which QuickShot subtypes
the WM160 FC/vision accepts (capture the `0x0A` traffic during each QuickShot).

### 2.4 Video-SEI AI/tracking metadata (dvtm / protobuf `TrackingInfo`)

The on-video tracking overlay metadata (bounding boxes carried in the H.264/H.265 SEI, "dvtm") is
decoded by a **third-party protobuf** package `com/lcti/mlace/proto` (`classes_0701100c.dex`):
`DvtmLibSdkProto`, `DvtmEagle4Wa530SdkProto`, `AIMetadataProto`, `AssembleSdkProto`, `MlaceProto`,
`XtstcProto`. It defines protobuf messages `TrackingInfo` / `TrackingInfos` / `TrackingInfoOrBuilder`
with a `bbox` field (grep-confirmed; also referenced in `classes_02f0700c.dex` and
`classes_00b9d00c.dex`). Related SEI class: `com/uav/videocore/SEIInfoLiveViewFOVState`.

This is the SmartEye display pipeline — the aircraft's vision co-processor embeds `TrackingInfo`
boxes into the video SEI, the app renders them. **WM160 has no SmartEye tracker, so it does not emit
these `TrackingInfo` SEIs.** Exact protobuf field numbers/types are **[LIVE-ONLY]**: baksmali
`Lcom/lcti/mlace/proto/DvtmLibSdkProto;` and read its `newMessageInfo` descriptor, or Frida-hook the
SEI parser and dump on a SmartEye-capable drone. For WM160, the AI overlay can be produced entirely
PC-side by the planned neural net (the SEI channel is not needed).

---

## 3. What WM160 actually supports vs rejects (evidence-cited summary)

**REJECTS (SDK code present, FC/vision refuses):**
- Waypoints — WaypointV3/WPMZ and legacy WaypointV1. Gate: `KeyIsWaypointSupport=false`
  (`WaypointService.smali` L2331–2349).
- ActiveTrack / Follow-Me / Spotlight / POI / MasterShot / multi-target. Gate:
  `KeyIsSmartEyeSupport` / `KeyIsMultiTargetSupport=false`; also `supportNavigationMode=false` for
  UAV59 (`MASTER_REPORT.md`), no forward vision (`ERROR_CODES.md` L51: WM160 has only downward VPS).
- Fixed-speed navigation / draw-trajectory (`0x0A/0x5B,0x5C,0x3B`) — SmartEye/nav-mode gated.

**PARTIAL:** QuickShots (fixed recipes using the `0x0A/0x20` track-select box).

**SUPPORTS (not this domain, for contrast):** manual flight, gimbal, camera photo/video/zoom,
timelapse (`0x0A/0x74…0x7B DataEyeSetTimeLapse*` builders exist and timelapse is FC-side, not
tracking) — subject to normal activation gating (see `FLIGHT_GATING.md`).

---

## 4. Frida / live-capture checklist (everything undecidable statically)

1. **Confirm the two master gates on WM160:** hook `Lcom/uav/rx/csdk/RxCSDK;->j1` and `->h1`; log
   the key name + emitted Boolean for `KeyIsWaypointSupport` and `KeyIsSmartEyeSupport`
   (expect both `false`).
2. **WPMZ DUML payloads (`0x22/0xAB`,`0xAC`) and KMZ transfer channel** — no app builder; capture on
   a supported drone (Mini 3 / Air 2S) and diff; watch the file-transfer channel before `0xAB`.
3. **`uav/sdk/wpmz/jni` native serializer** — hook to dump the raw KMZ (`template.kml` +
   `waylines.wpml`) bytes the SDK emits.
4. **`DvtmLibSdkProto.TrackingInfo` field layout** — baksmali the proto's `newMessageInfo` or hook
   the SEI decoder to dump the protobuf on a SmartEye drone; verify WM160 emits none.
5. **QuickShot acceptance on WM160** — capture `cmd_set 0x0A` (`0x20` select, `0x27` common-ctrl,
   `0x94` start) per QuickShot subtype to see which the WM160 vision co-processor ACKs vs NACKs.

---

### Source index
- Classes: `all_classes.txt` (`uav/sdk/wpmz/*` L29026+, `com/uav/waypoint/*`, `com/uav/fpv/component/mission/{waypoint,smarteye}/*`, `com/lcti/mlace/proto/*`).
- DUML: `full_table.txt` L189–190, L346–381, L446–457; `DUML_COMMANDS_FULL.md` L331–336, L355, L655–715, L908–909.
- Smali (baksmali of `classes_016b200c.dex`): `WaypointService.smali`, `WaypointCapabilityChecker.smali`, `base/enumInfo/*.smali`, `base/WpStartCheckType.smali`.
- Cross-refs: `MASTER_REPORT.md` (WM160 feature matrix), `ERROR_CODES.md` (downward-only VPS), `FLIGHT_GATING.md` (activation gate).
