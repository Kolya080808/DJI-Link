# MSDK_FULL_REFERENCE — the complete DJI Mobile SDK contract for the Mavic Mini 1 (WM160 / FC7203)

**Purpose.** One self-contained reference for reimplementing the whole DJI Fly / MSDK feature set in
Python over raw DUML, so we never have to open the MSDK docs again. Every subsystem is documented in
**two layers side by side**:

- **MSDK layer** — the public DJI Mobile SDK **V4** API surface (methods, enums with integer values,
  units, ranges, preconditions, real sample call-order). This is the *specification we are
  reproducing*. Sourced from developer.dji.com V4 reference + the `dji-sdk/Mobile-SDK-{Android,iOS}`
  sample repos + a CFR decompile of `com.dji:dji-sdk:4.3.2` (the last non-SecNeo AAR, which still
  carries the enum integers and the `dji.midware.data.model.P3.*` DUML models).
- **WM160 DUML layer** — the actual on-wire `cmd_set/cmd_id/payload` DJI Fly emits to *this* airframe,
  from our own reverse (`reverse_docs/*`, baksmali of DJI Fly v1.21.4 + native libs + live captures).
  **These are the bytes to send.** Cross-referenced to the exact companion doc for deep detail.

Identity everywhere: **WM160 = Mavic Mini 1 = UAV59 = ProductType 0x3B (59)**, camera `FC7203`, RC =
UAV59RC (id 99). DUML device addresses: `CAMERA=0x01`, `APP=0x02`, `FLYC=0x03`, `GIMBAL=0x04`,
`RC=0x06`, `DM368/video=0x08`, `BATTERY=0x0D`; PC/DJI-Assistant=0x0A (**do not** send flight cmds as
0x0A — the FC raises `AssistantProtected` and locks motors; send as APP=0x02).

> **Golden rule for the port.** MSDK enum *ordinals are almost never the DUML wire byte.* Map MSDK ↔
> DUML **by name**, and use the DUML column. Where the two disagree, the WM160-specific reverse
> (`reverse_docs/`) wins over the generic 4.3.2 decompile, because 4.3.2 predates WM160.

Companion docs (deep detail, cited inline): `MASTER_REPORT.md`, `APP_MAP_INDEX.md`,
`FLIGHT_GATING.md`, `TAKEOFF_UNLOCK.md`, `DARK_NOGPS_TRUTH.md`, `CAMERA_AND_NOGPS.md`,
`TELEMETRY_TRUTH.md`, `MEDIA_TRANSPORT_TRUTH.md`, `MSDK_MEDIA_SEQUENCE.md`, `PARAM_WIRE.md`,
`PARAM_HASH.md`, `INTELLIGENT_AND_PARAMS.md`, `DOMAIN_keyvalue_sdk.md`, `DOMAIN_rc_functions.md`,
`DOMAIN_media_album.md`, `MEDIA_TRANSFER.md`, `DUML_COMMANDS_FULL.md`, `ERROR_CODES.md`.

---

## 0. WM160 MSDK SUPPORT — which SDK, and how limited

**The original Mavic Mini (WM160) is supported ONLY in the MSDK V4 line, added in v4.13 (2020-07-27),
and NOT in MSDK V5 at all.** Support is **partial / firmware-limited** — it is the *aircraft firmware*
that refuses the missing features, not an SDK gap (DJI staff, GitHub `Mobile-SDK-Android` #728/#606:
*"Mavic Mini 不支持任何 Mission 类的任务 / Mini 固件限制，不支持航线飞行"*). MSDK V5 lists only Mini 3 /
Mini 3 Pro / Mini 4 Pro — WM160 is absent. So **V4 is the only SDK reference, and raw DUML is the
correct route for anything MSDK gates off.** Model constant: `Model.MAVIC_MINI`, display string
`"Mavic Mini"` (added in 4.13; match on this exact string).

| Subsystem / feature | WM160 via MSDK V4 | Notes |
|---|---|---|
| Product/component enumeration, connection lifecycle | ✅ | `Model.MAVIC_MINI` |
| FlightControllerState / OSD telemetry (10 Hz push) | ✅ | standard `DJIFlightControllerState` |
| **Virtual Stick** (manual stick injection) | ✅ | *the* automation entry point; added 4.13, fw 01.00.0500 |
| Takeoff / Land / RTH / turnOn·OffMotors / home / max height+radius / novice | ✅ | |
| IMU / compass state + calibration | ✅ | |
| Battery state | ✅ *(partial)* | non-smart 2S pack → lifetime/cycles/per-cell often 0/absent |
| Gimbal state + pitch rotate/reset | ✅ *(pitch only)* | roll/yaw stabilized but **not** user-controllable |
| Live video feed (`VideoFeeder` H.264) | ✅ | works (Litchi et al.) |
| QuickShots (Rocket/Dronie/Circle/Helix) | ✅ | GPS-based |
| Panorama / Timelapse / Hyperlapse | ✅ | |
| **Waypoint / Hotpoint / TapFly / any Mission task (even one GoToAction)** | ❌ | firmware-rejected |
| **ActiveTrack / Follow-Me / Spotlight / POI / Orbit** | ❌ | no tracking/vision sensors; `supportNavigationMode=false` |
| **Obstacle avoidance / FlightAssistant** | ❌ | `getFlightAssistant()` returns **null** (only downward VPS) |
| MasterShot | ❌ (likely) | tracking-based; verify `isSupportMasterShot` live |
| RC master/slave, `setControllingGimbalIndex` | ❌ | Inspire 1 / M100 / M300 only |
| AirLink | WiFiLink only | "Enhanced Wi-Fi" 2.4/5.8 GHz; no OcuSync/Lightbridge |

Sources: forum.dji.com/thread-221359; `Mobile-SDK-Android` #455/#594/#606/#728/#1105; deepwiki
`Mobile-SDK-Android-V5/1.1`. Independently corroborated locally in `FLIGHT_GATING.md §H`,
`MASTER_REPORT.md §8`, `INTELLIGENT_AND_PARAMS.md §A.6`.

---

## 1. PRODUCT / COMPONENT MODEL & SDK LIFECYCLE

### 1.1 Class model (MSDK)
- `DJIBaseProduct` (abstract) → **`DJIAircraft`** (WM160) / `DJIHandheld`.
- Components on `DJIBaseProduct`: `camera(s)`, `gimbal(s)`, `battery(ies)`, `airLink`, `videoFeeder`.
- **`DJIAircraft`-only**: `flightController`, `remoteController`, `mobileRemoteController`.
- Utility: `getModel()`/`model`, `getFirmwarePackageVersion…`, `get/setName`, `isFirmwareVersion:newerThanVersion:`.
- Multi-instance components are lists indexed by **component index** (WM160 = index 0 for the single
  camera/gimbal/battery/FC).

### 1.2 Registration & connection (`DJISDKManager`)
1. App Key in manifest/Info.plist → `registerApp(...)`; validated against DJI server once (needs
   internet on first run only).
2. `didRegister` success → `startConnectionToProduct()` (or bridge mode).
3. `onProductConnect/Disconnect/Changed` (iOS `appManagerDidUpdateConnectionStatus`).
4. `getProduct()` → cast to `Aircraft`.
- **Android** connectivity: `BaseProduct.setBaseProductListener` →
  `onComponentChange(ComponentKey, old, new)` + `onProductConnectivityChanged(bool)`.
  `ComponentKey` enum: `CAMERA, GIMBAL, FLIGHT_CONTROLLER, BATTERY, REMOTE_CONTROLLER, AIR_LINK, …`.
- **iOS**: `DJIBaseProductDelegate` (component/connectivity + diagnostics).

### 1.3 WM160 DUML equivalent
There is **no registration/connection handshake on the wire** for us — the Pi speaks the AOA
composite mux directly (`DOMAIN_transport_usb_aoa.md`, `MASTER_REPORT.md §2`). "Product connected"
= the FC OSD push (`0x03/0x43`) is flowing. The one hard cloud gate is **one-time activation**
(`0x00/0x32` + `0x03/0x62`, `TAKEOFF_UNLOCK.md §4`); after that no account/internet is ever needed.
Component enumeration ≈ the static UAV59 capability table (`DOMAIN_productconfig.md`) + the
`isSupport*` keys resolved live.

---

## 2. KEYMANAGER / DJIKey — the KeyValue system and how a key becomes a DUML frame

This is the most important mechanism to understand, because DJI Fly v5 writes almost everything
(including `DarkNoGpsLockEnable`) through it. Deep detail: `DOMAIN_keyvalue_sdk.md`.

### 2.1 Mental model
A `DJIKey` is an **address into product state** — a tuple:
```
(componentType, componentIndex, subComponentType, subComponentIndex, paramName)
```
plus a capability subset **{canGet, canSet, canListen/Push, canPerformAction}**. The single gateway
`KeyManager` (iOS `DJISDKManager.keyManager`; Android `DJISDKManager.getInstance().getKeyManager()`)
turns *key + operation* into a device transaction and caches values.

### 2.2 MSDK API surface
**iOS `DJIKey`** creators: `keyWithParam:`, `keyWithIndex:andParam:`,
`keyWithIndex:subComponent:subComponentIndex:andParam:`. Props: `param`, `index`, `subComponent`,
`subComponentIndex`, `isComponentKey`, `isMissionKey`.
**iOS `DJIKeyManager`**:
```
getValueForKey:                      // sync cached read → DJIKeyedValue
getValueForKey:withCompletion:       // async device pull
setValue:forKey:withCompletion:
performActionForKey:withArguments:andCompletion:
startListeningForChangesOnKey:withListener:andUpdateBlock:   // push
stopListeningOnKey:ofListener:  /  stopAllListeningOfListeners:
isKeySupported:
```
`DJIKeyedValue` wraps the raw value (enums → NSNumber, structs → NSValue) with typed accessors +
`paramRange` (`DJIParamCapabilityMinMax`).
**Android** `DJIKey` subclasses expose static `create(...)` factories (never constructed directly):
```
CameraKey.create(paramKey[, componentIndex])  /  createLensKey(paramKey, comp, sub)
FlightControllerKey.create(paramKey)
FlightControllerKey.createFlightAssistantKey(...) / createRTKKey(...) / createAccessLockerKey(...)
```
`KeyManager`: `getValue(key)` (sync cached), `getValue(key, GetCallback)` (async), `setValue`,
`performAction(key, ActionCallback, Object... args)`, `addListener(key, KeyListener)`,
`removeListener`, `isKeySupported`, `removeKey` (clear cache).
Subclasses (both platforms): `FlightControllerKey, CameraKey, GimbalKey, BatteryKey, ProductKey,
AirLinkKey, RemoteControllerKey, HandheldControllerKey, PayloadKey, AccessoryKey, DiagnosticsKey,
RTKBaseStationKey, RadarKey, LidarKey`.

### 2.3 How DJI Fly v5 actually implements it (our decompile) — key → native → DUML
DJI Fly's on-device KeyValue framework is `uav.sdk.keyvalue` (4537 classes). Java code **never names a
`cmd_set`/`cmd_id`**. A key is `UAVKeyInfo(componentType, subComponentType, name, converter)`; the
invocable `UAVKey` binds concrete indices. The dispatch:
```
UAVKeyManager.r/t/F/m/C  →  JNIKeyValue.native_get/get_sync/set/do_action/listen
   ( int productId, int componentType, int componentIndex,
     int subComponentType, int subComponentIndex, String name, byte[] value )
```
- **The ABI carries only the NAME STRING + a little-endian serialized value blob — no numeric key
  id.** The mapping `name → cmd_set/cmd_id → wire framing` lives entirely inside the native
  `libsdk_key_value.so` / `libsdk_jni.so` and is **NOT recoverable from Java/smali.** To learn the
  DUML behind any key you must reverse those `.so` or Frida-hook `JNIKeyValue.native_set/get/…`.
- `ComponentType` wire values: `CAMERA=1, REMOTECONTROLLER=3, FLIGHTCONTROLLER=4, GIMBAL=5,
  BATTERY=6, WIFI=7, AIRLINK=8, FLIGHTASSISTANT=11, OCUSYNC=28, …`. `SubComponentType.IGNORE =
  0xFFFE` (the WM160 default for all normal keys). **These are NOT DUML cmd_set numbers** — native
  translates them.
- **Value serialization** (`ByteStreamHelper`, all **little-endian**): Bool=1B(0/1), Int32=4B LE,
  Int64=8B LE, Double=8B LE, String=`[u32 LE len][UTF-8]`, List=`[u32 LE count]+elems`,
  **enums = int32 LE (`enum.value()`)**, structs recurse in field-declaration order.
- Concrete WM160 value objects: `Attitude` = 3×double (pitch,roll,yaw) 24B; `VirtualJoyStickMsg` =
  4×int32 LE (ch0..ch3) 16B; `VirtualStickFlightControlParam` = pitch/roll/yaw/vert doubles + 4 mode
  enums (int32 LE each) + advancedModeEnabled bool; `FCFlightMode` enum int32 LE.
- **Push path**: native → `PushProcessor.d(UAVKey, value)` → registered listeners; how telemetry
  (Velocity/Attitude/battery%/flight-mode) streams without polling.
- **No Java-side product gating**: every key exists for every product; support is decided at runtime
  by native + the `isSupport*` capability keys (`isSupport_keys.txt`, e.g. `isSupportVirtualJoyStick`).

**Consequence for the port.** The KeyValue SDK gives you the exhaustive *catalogue* (key names + value
byte layouts) but the WM160 `cmd_set/cmd_id` per key is native and must be captured live. Our
confirmed-working commands are the raw-DUML ones (`FLIGHT_GATING.md`, `DUML_COMMANDS_FULL.md`), which
bypass this SDK. **Frida hook points** to bridge a key to its DUML: `JNIKeyValue.native_set/get_sync/
do_action/listen` (Java, cleanest — gives the 5 ints + name + value bytes), and correlate against the
on-wire DUML.

### 2.4 Important FlightControllerKey constants (Android `UPPER_SNAKE` / iOS `DJIFlightControllerParam…`)
Access: **PUSH** = listenable+gettable · **GET** = query · **SET** = settable · **ACTION** = performAction.

Telemetry (PUSH): `AIRCRAFT_LOCATION`(LocationCoordinate3D), `ALTITUDE`(Float m rel. takeoff),
`TAKEOFF_LOCATION_ALTITUDE`(Float), `VELOCITY_X/_Y/_Z`(Float, N/E/D m/s),
`ATTITUDE_PITCH/_ROLL/_YAW`(Double deg), `COMPASS_HEADING`(Float), `IS_FLYING`, `IS_LANDING`,
`IS_GOING_HOME`, `ARE_MOTORS_ON`, `SATELLITE_COUNT`(Int), `GPSSignalStatus`(enum),
`FLIGHT_MODE`(enum)/`FLIGHT_MODE_STRING`(String), `WIND_SPEED`, `FLY_TIME_IN_SECONDS`,
`REMAINING_FLIGHT_TIME`(Int).
Home/limits: `HOME_LOCATION`(PUSH/SET), `IS_HOME_LOCATION_SET`,
`HOME_LOCATION_USING_CURRENT_AIRCRAFT_LOCATION`(ACTION), `GO_HOME_HEIGHT_IN_METERS`(GET/SET Int),
`GO_HOME_STATUS`(enum), `SMART_RETURN_TO_HOME_ENABLED`, `MAX_FLIGHT_HEIGHT`(GET/SET Int),
`MAX_FLIGHT_HEIGHT_RANGE`(MinMax), `MAX_FLIGHT_RADIUS`(GET/SET), `MAX_FLIGHT_RADIUS_ENABLED`,
`NEED_LIMIT_FLIGHT_HEIGHT`.
Modes/failsafe/battery: `NOVICE_MODE_ENABLED`(PUSH/SET Bool), `TRIPOD_MODE_ENABLED`,
`ORIENTATION_MODE`(enum), `IS_FAIL_SAFE`, `CONNECTION_FAIL_SAFE_BEHAVIOR`(enum hover/land/gohome),
`LOW_BATTERY_WARNING_THRESHOLD`, `SeriousLowBatteryWarningThreshold`,
`BATTERY_PERCENTAGE_NEEDED_TO_GO_HOME`.
Virtual stick: `VIRTUAL_STICK_CONTROL_MODE_ENABLED`(PUSH/SET),
`IS_VIRTUAL_STICK_CONTROL_MODE_AVAILABLE`(GET/PUSH), `SEND_VIRTUAL_STICK_FLIGHT_CONTROL_DATA`(ACTION),
iOS mode keys `YawControlMode/RollPitchControlMode/VerticalControlMode/RollPitchCoordinateSystem`.
Actions (no arg): `TURN_ON_MOTORS/TURN_OFF_MOTORS/TAKE_OFF/CANCEL_TAKE_OFF/START_LANDING/
CANCEL_LANDING/CONFIRM_LANDING/START_GO_HOME/CANCEL_GO_HOME/LOCK_COURSE_USING_CURRENT_HEADING`;
iOS also `StartPrecisionTakeoff`, `StartSimulator`/`IsSimulatorActive`, `SerialNumber`, `Name`.

### 2.5 Important CameraKey constants
`MODE`(PUSH/SET CameraMode)/`FLAT_CAMERA_MODE`, `SHOOT_PHOTO_MODE`, `IS_RECORDING`,
`CURRENT_VIDEO_RECORDING_TIME_IN_SECONDS`, `IS_SHOOTING_SINGLE_PHOTO/…`, `IS_STORING_PHOTO`,
`EXPOSURE_MODE`, `EXPOSURE_SETTINGS`(ISO+shutter+aperture+EV struct), `ISO`, `SHUTTER_SPEED`,
`APERTURE`, `EXPOSURE_COMPENSATION`, `METERING_MODE`, `WHITE_BALANCE`(struct), `CAMERA_COLOR`,
`SHARPNESS/CONTRAST/SATURATION`(Int), `ANTI_FLICKER`, `PHOTO_FILE_FORMAT`, `PHOTO_ASPECT_RATIO`,
`PHOTO_BURST_COUNT/PHOTO_AEB_COUNT`, `PHOTO_TIME_INTERVAL_SETTINGS`, `RESOLUTION_FRAME_RATE`(struct),
`VIDEO_FILE_FORMAT`, `VIDEO_STANDARD`, `START_SHOOT_PHOTO/STOP_SHOOT_PHOTO`(ACTION),
`START_RECORD_VIDEO/STOP_RECORD_VIDEO`(ACTION), `FORMAT_SD_CARD`(ACTION), `EXIT_PLAYBACK`(ACTION),
`SDCARD_IS_INSERTED`, `SDCARD_REMAINING_SPACE_IN_MB`, `SDCARD_STATE`, `CAMERA_STORAGE_LOCATION`,
`FOCUS_MODE`, `DIGITAL_ZOOM_FACTOR`(Float).

---

## 3. FLIGHT CONTROLLER

### 3.1 Virtual Sticks

**MSDK enable sequence & preconditions.**
```
1. setVirtualStickModeEnabled(true, cb)          // gate: isVirtualStickControlModeAvailable == true
2. set the 4 control-mode properties (§3.1.2)    // BEFORE sending data (they reset on FC reconnect)
3. (optional) setVirtualStickAdvancedModeEnabled(true)  // wind comp; default false; NOT required
4. startTakeoff(cb) or turnOnMotors(cb)          // sticks alone do NOT arm from the ground
5. every 40 ms: sendVirtualStickFlightControlData(data, cb)   // 5–25 Hz
6. setVirtualStickModeEnabled(false, cb)         // when done
```
`isVirtualStickControlModeAvailable` requires: vs-mode enabled, **no mission running** (trivially true
on WM160), flight-orientation = `AIRCRAFT_HEADING`, terrain-follow off, tripod off. WM160 RC has **no
P/A/F switch**, so there is no "put RC in P-mode" step. `setVirtualStickAdvancedModeEnabled` resets to
`false` on FC reconnect.

**FlightControlData struct** — Android ctor arg order **`FlightControlData(pitch, roll, yaw,
verticalThrottle)`** (4 floats). Field units depend on the mode enums below.

**3.1.2 Control-mode enums** (DJI publishes **no integer ordinals** — map by name; the mode ints ride
as int32 LE inside `VirtualStickFlightControlParam`):
- `VerticalControlMode`: `VELOCITY` (m/s, +=up) | `POSITION` (altitude m).
- `RollPitchControlMode`: `ANGLE` (deg from level) | `VELOCITY` (m/s).
- `YawControlMode`: `ANGLE` (heading deg) | `ANGULAR_VELOCITY` (deg/s).
- `FlightCoordinateSystem`: `GROUND` (North/East frame) | `BODY` (aircraft-heading frame).
- **SDK reset-defaults on FC (re)connect:** rollPitch=ANGLE, yaw=ANGLE, vertical=VELOCITY,
  coord=GROUND — so set them explicitly every session.
- **DJI's recommended config** (and the Android sample's): vertical=VELOCITY, rollPitch=VELOCITY,
  yaw=ANGULAR_VELOCITY, coordinate=**BODY** (game-style "forward = nose"). The iOS sample uses GROUND.

**3.1.3 Ranges** (authoritative — iOS Constants Reference):
| field / mode | range |
|---|---|
| Vertical velocity | **−4 … +4 m/s** |
| Vertical position (altitude) | **0 … 500 m** |
| Roll/Pitch velocity | **−15 … +15 m/s** |
| Roll/Pitch **angle** | **−30 … +30°** |
| Yaw angular velocity | **−100 … +100 °/s** |
| Yaw angle | **−180 … +180°** |

**3.1.4 Send rate (verbatim).** *"Virtual stick commands should be sent between 5 Hz and 25 Hz. If not
sent frequently enough the aircraft may regard the connection as broken, which will cause it to hover
in place until the next command."* Android sample timer = 200 ms (5 Hz); target **~40 ms (25 Hz)**,
hard floor 200 ms. iOS sample sends straight from the touch-move callback (no timer).

**3.1.5 Known footgun.** Multiple devs (and the Android `VirtualStickView.java` comment) report
`pitch`/`roll` behaving swapped and/or yaw inverted vs intuition — **verify axis mapping on the WM160
bench** before trusting it. Our `control.py` intent: pitch+1=forward, roll+1=right, yaw+1=right,
throttle+1=up.

**3.1.6 WM160 DUML mapping** (`FLIGHT_GATING.md §H`, `TAKEOFF_UNLOCK.md §6`, `control.py`).
`setVirtualStickModeEnabled(true)` ≈ **ground-station ON** at the DUML layer. Three stick encodings
exist; **which one WM160 firmware accepts is the #1 open HW item** (settle with one Frida capture):

| # | cmd_set/cmd_id | payload | role | our code |
|---|---|---|---|---|
| A | **0x01/0x0A** special-TLV | 4×11-bit channels, `value=round(norm·660+1024)`∈[364..1684], center 1024; PUSH (cmd_type 0x00) | RC-emulation. **Packing/scale CONFIRMED** from `libsdk_jni VirtualJoyStickHelper::AssemblePack`; channel order (roll,pitch,yaw,throttle)=ch0..3 is a hypothesis | `drone.set_sticks`, `control.py` |
| B | 0x01/0x02 mobile-RC joystick | 13 B: 4×11-bit + flags `0x0200|(mode<<10)`; carries a `FlycMode` A=0/P=1/F=2 | alt RC-emulation | — |
| C | **0x03/0x8E** FLYC float joystick | 17 B: `[flag u8][roll f32][pitch f32][yaw f32][throttle f32]` LE (physical units) | strongest match to MSDK angle/velocity semantics | `drone.set_sticks_float` |

Virtual stick puts the FC in **JOYSTICK mode (FLYC state 17)**; does **not** require GPS (flies ATTI &
drifts with no GPS/VPS). Input clamps are FC params: `serial_api_cfg.input_pitch_limit`[363]
def3500/max6000, `input_roll_limit`[364], `input_yaw_rate_limit`[365] def/max15000,
`input_vertical_velocity_limit`[366] def/max600. For encoding C, feed physical units, cap well under
the maxes (e.g. ±5 m/s, ±60 °/s, ±2 m/s); the `flag` byte likely selects the mode combination — value
unknown (**HW**).

**Preconditions (DUML), full sequence** — `TAKEOFF_UNLOCK.md §6`, `FLIGHT_GATING.md §H`:
```
1. 0x49/0x80 01           request control authority              (ACK)     [only cmd in set 0x49]
   (if ignored) 0x06/F1 01 RC→PC   and/or  0x19/41 01 preempt
2. 0x03/0x80 01           ground-station / external-control ON    (ACK)     [= setVirtualStickModeEnabled]
   (optional) 0x03/0xF9 write serial_api_cfg.advance_function_enable=1 [362] (hashed; unverified gate)
3. 0x03/2A 01             AUTO_FLY (arm+lift)   — or 0x03/2A 07 START_MOTOR (arm only)
4. stream sticks @ 5–25 Hz   (encoding A or C — confirm on HW)
5. panic: 0x03/2A 02 land | 0x03/2A 06 RTH | 0x03/0xFE 01 force-disable motors
6. 0x49/0x80 00           release control
```

### 3.2 Takeoff / Land / RTH / Motors

**MSDK methods & their real semantics:**
| method | behavior / precondition |
|---|---|
| `startTakeoff` | auto-takeoff to hover @ **1.2 m**; **completion callback fires early (~0.5 m)** — success ≠ hover. On-ground/motors-off. |
| `startPrecisionTakeoff` | records a visual snapshot for precise RTH; completes @ **6 m**. |
| `startLanding` | auto-land; cb returns once descent begins. |
| `confirmLanding` | at **<0.3 m** the FC pauses and waits; check `state.isLandingConfirmationNeeded()`; FlightMode → `ConfirmLanding` while paused. |
| `cancelLanding` | stop auto-land, hover; pending `startLanding` cb errors. |
| `startGoHome` / `cancelGoHome` | RTH needs a recorded home + typically ≥6–7 sats; cancel → hover. |
| `setHomeLocation(LocationCoordinate2D)` | accepted only within ~30 m of takeoff/aircraft/mobile(≥10 m acc)/RC-GPS. |
| `setHomeLocationUsingAircraftCurrentLocation` | home = current aircraft location. |
| `turnOnMotors` / `turnOffMotors` | arm / disarm (**off only while on the ground**). FLYC then reports `ENGINE_START`/`MotorsJustStarted`. |

**Naming note:** MSDK V4 has **`turnOnMotors`/`turnOffMotors`**, not `startMotors/stopMotors` (that's
OSDK). Both `turnOnMotors` and `startTakeoff` arm at the DUML layer; takeoff adds the 1.2 m climb.

**WM160 DUML — it's ALL one command: `0x03/0x2A FunctionControl`**, receiver FLYC, **1-byte
`FLYC_COMMAND`** (fully resolved from `DataFlycFunctionControl$FLYC_COMMAND.smali`; 2 bytes only for
`OAR_PANEL_CALI`):
```
0x01 AUTO_FLY (takeoff: motors+lift)   0x02 AUTO_LANDING        0x03 HOMEPOINT_NOW (home=here)
0x04 HOMEPOINT_HOT                     0x05 HOMEPOINT_LOC (home=operator)   0x06 GOHOME (RTH)
0x07 START_MOTOR (arm, no lift)        0x08 STOP_MOTOR (disarm)  0x09 Calibration (compass/IMU)
0x0A DeformProtecClose  0x0B Open      0x0C DropGohome (cancel RTH)         0x0D DropTakeOff (cancel)
0x0E DropLanding (cancel land)         0x0F/0x10 DynamicHomePoint Open/Close
0x11/0x12 Follow Open/Close            0x13/0x14 IOC Open/Close             0x15 DropCalibration
0x16 PackMode  0x17 UnPackMode  0x18 EnterManualMode
0x1E ForceLanding   0x1F ForceLanding2   0x22 PRECISION_TAKE_OFF
0x2F OAR_PANEL_CALI  0x36 MASS_CENTER_CALI  0x37 EXIT_MASS_CENTER_CALI      0x64 OTHER
```
So `55…03 2A 01`=takeoff, `03 2A 06`=RTH, `03 2A 07`=arm only, `03 2A 02`=land.
- Arbitrary home lat/lon: `0x03/0x31 SetHomePoint` — `[type u8][lat f64 LE RADIANS][lon f64 LE
  RADIANS][interval u8]` (18 B). (Note: lat/lon are **radians**, matching the OSD convention.)
- Emergency motor kill: `0x03/0xFE SetMotorForceDisable` — **1-byte** `[isDisable u8]` (01 lock → OSD
  +0x33 reads `LOCK_BY_APP`=115; 00 release). *`drone.py` currently sends 2 bytes — fix to 1.*
- RTH altitude is a **param**, not a command: `go_home.fixed_go_home_altitude`[212] def20/min20/max500
  (hash-write `0x03/0xF9`). Fail-safe/RTH behaviour: `0x03/0x3B set` / `0x03/0x3C get`.
- ⚠ **`confirmLanding` correction:** `0x1E` in the enum is **ForceLanding** (previously mislabeled
  "confirm_land" in `drone.py`). The <0.3 m land-confirm handshake may be a distinct command/key —
  watch the `isLandingConfirmationNeeded` bit and verify.

### 3.3 Flight modes & FLYC states

**MSDK `FlightControllerState.getFlightMode()` → `FlightMode` enum** (names only, no published
ordinals; iOS drops underscores, Android may SCREAM_SNAKE):
`Manual, Atti, AttiCourseLock, AttiHover, Hover, GPSBlake, GPSAtti, GPSCourseLock, GPSHomeLock,
GPSHotPoint, AssistedTakeOff, AutoTakeOff, AutoLanding, AttiLanding, GPSWaypoint, GoHome, Joystick,
AttiLimited, Draw, GPSFollowMe, ActiveTrack, TapFly, GPSSport, GPSNovice, ConfirmLanding,
TerrainFollow, Tripod, TrackSpotlight, MotorsJustStarted, NaviAdvGoHome, NaviAdvLanding, PANO,
Farming, FPV, GPSAttiLimited, GPSGentle, Pointing, Tracking, Unknown`.

**Low-level FLYC state — WITH integer values** (this is what you decode from the OSD push
`0x03/0x43`, byte **+0x1e & 0x7F**). Authoritative wire table (o-gs/dji-firmware-tools + our
`telemetry.py`/`diag_codes.py`; the load-bearing ones are **6=GPS_Atti, 17=Joystick**):
```
0 Manual        1 Atti          2 Atti_CL       3 Atti_Hover    4 Hover
5 GPS_Blake     6 GPS_Atti      7 GPS_CL        8 GPS_HomeLock  9 GPS_HotPoint
10 AssistedTakeoff 11 AutoTakeoff 12 AutoLanding 13 AttiLanding 14 NaviGo
15 GoHome       16 ClickGo      17 Joystick     18 Cinematic    19 Atti_Limited
20 NaviSubMode_Draw 21 NaviMissionFollow 22 NaviSubMode_Tracking 23 NaviSubMode_Pointing
24 PANO 25 Farming 26 FPV 27 SPORT 28 NOVICE 29 FORCE_LANDING 30 TERRAIN_TRACKING
31 PALM_CONTROL 32 QUICK_SHOT 33 TRIPOD_GPS 34 TRACK_HEADLOCK 35 ENGINE_START 36 DETOUR
37 TIME_LAPSE 38 POI_WITH_VISION 39 OMNI_MOVING 40 OTHER
```
(`TELEMETRY_TRUTH.md §1` — note our two internal tables diverge slightly ≥16; `TELEMETRY_TRUTH.md`
is the app's own parser and is authoritative for WM160. `telemetry.py`'s FLYC dict is wrong ≥16 and
should adopt this.)

**RC flight-mode switch (MSDK `DJIRCFlightModeSwitch` / `…HardwareFlightModeSwitchState`):** `ONE/TWO/
THREE` (positions) mapping to P (Positioning, GPS+VPS; also enables Missions/VirtualStick on RCs
without an F position), S (Sport), F (Function). **WM160 RC has NO physical mode switch** — mode is
app/SDK-driven, P is the effective default. On WM160, Normal/Sport/Cine are **control-gain FC param
profiles**, not distinct FC states — **there is no clean "set flight mode" DUML** (`FLIGHT_GATING.md
§C`); adjust `g_config.control.horiz_vel_atti_range_0`[312], `mode_sport_cfg_*`, input limits [363–366].

### 3.4 Home point, max height, max radius, go-home height

**MSDK setters** (V4 folds flight limits onto `FlightController`):
| API | range | key |
|---|---|---|
| `setMaxFlightHeight` | **20–500 m** | `MAX_FLIGHT_HEIGHT` |
| `setMaxFlightRadius` | **15–8000 m** (V4; 3.x was 15–500) | `MAX_FLIGHT_RADIUS` |
| `setMaxFlightRadiusLimitationEnabled` | — | disabled → no radius limit |
| `setGoHomeHeightInMeters` | **20–500 m** | (3.x `setGoHomeAltitude`) |
No defaults documented — read them back over DUML.

**WM160 DUML (NO hash needed for height/radius):** `DataFlycSetLimits` = **`0x03/0x2D`**, receiver
FLYC, 3-byte `[mode u8][value u16 LE m]`, mode `High=1`(max height)/`Far=2`(max radius)/`Low=3`(min
height). Read-back `0x03/0x2E [mode]`. FC clamps: height `flying_limit.max_height`[236] **15–500**
def120; radius `flying_limit.max_radius`[235] **15–5000** def30. e.g. ceiling 500 m: `55…03 2D 01 F4
01`. Enable flags: `advanced_function.radius_limit_enabled`[207] def0, `height_limit_enabled`[205]
def1 (cannot be 0 — a height limit is always on). **Max speed has no clean non-hash command** →
control-gain params via `0x03/0xF9` (§9). Set/refresh home via `0x03/2A` HOMEPOINT_NOW=0x03 /
HOMEPOINT_LOC=0x05, or arbitrary lat/lon via `0x03/0x31`. `drone.py set_max_altitude/set_max_distance`
are correct.

### 3.5 Novice / beginner mode
**MSDK:** `setNoviceModeEnabled(bool)` / getter — slower, less responsive, forces GPS.
**WM160 DUML:** param `novice_cfg.novice_func_enabled_0`[343] (0=off), hash-write `0x03/0xF9`; caps
height/radius 30 m ([344]/[345]). **Novice forces GPS** → refusal `NO_GPS_AND_NOVICE` (OSD +0x33
value 10). Set [343]=0 to allow no-GPS takeoff. Caps can also be lifted non-hash via `0x03/0x2D`.

### 3.6 ATTI vs GPS, and no-GPS / "dark" takeoff (the unlock lever)
Deep detail: `DARK_NOGPS_TRUTH.md` (authoritative — it *corrects* earlier docs). Key facts:
- **Takeoff IS allowed without GPS lock** — the Mini flies on its **downward VPS** (needs light +
  texture). Do NOT gate takeoff on satellite count (`isSupportStartWithoutGPS`=true). If VPS also
  fails (dark) the FC drops to **ATTI**: holds altitude, **not** horizontal position → it drifts,
  no braking, no OA. ATTI is not user-selectable; it's automatic when GPS+VPS are both gone.
- **The "dark + no-GPS" refusal is a SOFT, unlockable flag.** FC pushes
  `MotorStartFailedCause = DARK_NEED_GPS = 147 (0x93)` at OSD +0x33; HMS `0x761f`; DJI Fly shows an
  "Unlock" button.
- **Override mechanism + corrected polarity/name:** to CLEAR the lock, write the flag **FALSE / 0**
  (writing TRUE re-arms it — the old bug). The KeyValue key is `DarkNoGpsLockEnable` (Boolean,
  ComponentType FLIGHTCONTROLLER=4); the underlying **flyc param string is `fc_dark_need_gps_0`**
  (NOT the literal `"DarkNoGpsLockEnable"`, which hashes to no param). App's Unlock action:
  `getDarkNoGPSLockOn().m(Boolean.FALSE)`. Result = a *drifting ATTI-only takeoff*, not dark position
  hold. **Our `drone.py unlock_no_gps(True)` writes `fc_dark_need_gps_0=0` — correct.**
- ⚠ Not statically proven that the app's Unlock emits exactly `0x03/0xF9 fc_dark_need_gps_0` (may be a
  KeyValue command); confirm by Frida-hooking `DataBase.start()` while tapping in-app Unlock once.

### 3.7 Collision / obstacle avoidance — NOT on WM160
`FlightController.getFlightAssistant()` is `@Nullable` and **returns null on WM160** (no obstacle
sensors, only downward VPS). Implement none of `DJIIntelligentFlightAssistant`
(`setCollisionAvoidanceEnabled`, `setVisionAssistedPositioningEnabled`, `setActiveObstacleAvoidance…`,
`setUpwardVisionObstacleAvoidance…`, `setLandingProtectionEnabled`). Precision landing is
downward-vision (`PRECISION_TAKE_OFF=0x22` records the spot; energy via push
`DataEyeGetPushPreciseLandingEnergy`) — verify on HW.

### 3.8 LEDs, compass, IMU, connection
- **LEDs:** `setLEDsEnabledSettings(LEDsSettings)`; builder fields `frontLEDsOn/rearLEDsOn/
  statusIndicatorOn/beaconsOn` (most no-op on the single-LED Mini). DUML `0x03/0xBC–0xBE` fmu led.
- **Compass** (`getCompass()`): `heading`(deg, TrueN=0, [−180,180]), `hasError`, `isCalibrating`,
  `calibrationState`; `startCalibration/stopCalibration`. `DJICompassCalibrationState`:
  NotCalibrating/Horizontal/Vertical/Successful/Failed/Unknown.
- **IMU** (`DJIIMUState`): `index`, `gyroscopeState/accelerometerState`(`DJIIMUSensorState`:
  Disconnected/Calibrating/CalibrationFailed/DataException/WarmingUp/InMotion/NormalBias/MediumBias/
  LargeBias/Unknown), `calibrationState`(None/Calibrating/Successful/Failed/Unknown),
  `calibrationProgress`[1..100 or −1]. `startIMUCalibration(cb)`.
- **WM160 DUML:** compass/IMU cali both via `0x03/2A Calibration=0x09`; progress via pushes
  `DataFlycGetPushCheckStatus` (21 B bitfield, `TAKEOFF_UNLOCK.md §2`),
  `DataFlycGetPushMassCenterCaliStatus`, `DataFlycGetPushFlycInstallError`. Gimbal cali is a separate
  command `0x04/0x08`.
- **State push:** MSDK `setStateCallback` fires **10 Hz**. WM160 equivalent = the OSD push `0x03/0x43`
  (§8).

---

## 4. CAMERA

Wire enums for WM160 come from `uav/sdk/keyvalue/value/camera/*` (our reverse) and the legacy
`DataCamera*` builders. **The MSDK enum `.value()` is often NOT the DUML byte** — both columns given;
use the DUML/WM160 column. Deep detail: `CAMERA_AND_NOGPS.md`, `full_table.txt`.

### 4.1 Work mode — `CameraMode` (cmd `0x02/0x10 set_camera_working_mode`, receiver 0x01)
| MSDK name | MSDK int | **WM160 DUML byte** (`CameraWorkMode.smali`) |
|---|---|---|
| SHOOT_PHOTO | 0 | **0** |
| RECORD_VIDEO | 1 | **1** |
| PLAYBACK | 2 | **2** |
| MEDIA_DOWNLOAD | 4 | **3** ← the media-list mode (see §5) |
| — TURNING | — | 4 |
| — POWER_SAVE | — | 5 |
| — DOWNLOAD | — | 6 |
| — TRANSCODE | — | 7 |
| BROADCAST | 5 | 8 |
| UNKNOWN | 255 | 9 (/ OTHER=100) |
> Reconciliation: the generic 4.3.2 decompile maps MSDK `MEDIA_DOWNLOAD(4) → DUML DOWNLOAD=6`, but the
> WM160-specific `CameraWorkMode.smali` says **MEDIA_DOWNLOAD = 3** (DOWNLOAD=6 is a separate value).
> **For WM160 send byte 3 to enter media-list mode; fallback probe 6.** (`MEDIA_TRANSPORT_TRUTH.md`.)

### 4.2 Exposure mode — `ExposureMode` (`0x02/0x1E`, 1-byte, receiver 0x01)
| name | MSDK int | **WM160 wire value** (`CameraExposureMode.smali`) |
|---|---|---|
| PROGRAM (full auto) | 1 | **0x01** |
| SHUTTER_PRIORITY | 2 | **0x02** |
| APERTURE_PRIORITY | 3 | 0x03 (fixed-aperture Mini ignores) |
| MANUAL | 4 | **0x04** |
| UNKNOWN | 255 | 0xFF |
Payload = single byte (a 2nd `sceneMode` byte is appended only for legacy expMode==6, unused on WM160).
**Precondition gates (this is why ISO/EV "did nothing"):** ISO is settable only in MANUAL/priority;
**EV is settable only in a NON-manual mode.** They are mutually exclusive by exposure mode. → set
MANUAL(0x04) *before* ISO; set PROGRAM(0x01) *before* EV.

### 4.3 ISO — `0x02/0x2A` (byte `(type<<7)|value`; type 0=absolute → byte = value)
| ISO | MSDK/WM160 value | ISO | value |
|---|---|---|---|
| AUTO | 0x00 | ISO_1600 | 0x07 |
| ISO_50 | 0x02 | ISO_3200 | 0x08 |
| ISO_100 | 0x03 | ISO_6400 | 0x09 |
| ISO_200 | 0x04 | ISO_12800 | 0x0A |
| ISO_400 | 0x05 | ISO_125 | 0x15 |
| ISO_800 | 0x06 | ISO_FIXED/LOCK | 0xFF |
WM160 usable range AUTO/100/200/400/800/1600/3200. Requires MANUAL exposure. (MSDK ISO enum has no
`FIXED` — that's expressed via ExposureMode=MANUAL.) *`drone.py` sends the enum index (0x05 for 400) —
correct; earlier bug was sending literal 400.*

### 4.4 Exposure compensation (EV) — `0x02/0x2E` (1-byte = value)
`0 EV = 0x10`, each 1/3-EV step = ±1. WM160 UI range −3.0..+3.0 → **0x07..0x19**. Full table
`NEG_5P0EV=0x01 … NEG_0EV=0x10 … POS_5P0EV=0x1F`, `FIXED=0xFF`. MSDK `ExposureCompensation`: same
scheme, `N_0_0(0 EV)=index 16`, range indices 1..31. EV applies in NON-manual mode only.

### 4.5 White balance — `0x02/0x2C`
Mode enum (`CameraWhiteBalanceMode`): `AUTO=0x00, SUNNY=0x01, CLOUDY=0x02, WATER_SURFACE=0x03,
INDOOR_INCANDESCENT=0x04, INDOOR_FLUORESCENT=0x05, MANUAL/custom=0x06, NATURAL=0x07, UNDERWATER=0x08`.
Composite `CameraWhiteBalance{mode, colorTemperature:int}`; custom color-temp = raw int (MSDK
`ColorType` T2000K=0..T10000K=16 in 500 K steps). Preset payload `[mode]`; custom `[0x06, ctByte]` —
exact custom byte NOT pinned statically (native codec) → Frida. For AUTO just send `[0x00]`.

### 4.6 Photo / video
- **Take photo:** `0x02/0x01` payload = photo-type; **record:** `0x02/0x02` (1=start/0=stop/2=pause/
  3=resume). MSDK: `startShootPhoto`/`stopShootPhoto`, `startRecordVideo`/`stopRecordVideo` (or the
  ACTION keys). `drone.take_photo` sends `[0x02]` (single).
- **`ShootPhotoMode`** (MSDK int → DUML photo-type byte, `DataCameraSetPhoto.TYPE`): SINGLE 0→1,
  HDR 1→2, BURST 2→4, AEB 3→5, INTERVAL 4→6, TIME_LAPSE 5→6, PANORAMA 6→7, RAW_BURST 7→9,
  SHALLOW_FOCUS 8→98, UNKNOWN 255→11. WM160 = SINGLE (12 MP), interval for timed.
- **Photo file format** (`PhotoFileFormat`): RAW=0, **JPEG=1 (WM160, JPEG only, no RAW)**,
  RAW_AND_JPEG=2, TIFF_14=4… **Aspect ratio**: RATIO_4_3=0 (WM160 native 4000×3000), 16_9=1, 3_2=2.
- **Video** (WM160 max **2.7K 2720×1530** @25/30, **1080p 1920×1080** @25/30/50/60; MP4=1; no 4K/720p
  in-app). `VideoResolution` MSDK ints: 1920×1080=3, 2720×1530=5 — **DUML resolution byte differs and
  the 4.3.2 drone table predates WM160; confirm the 2.7K wire byte on-device.**
  `VideoFrameRate` (MSDK int → **DUML byte** `cmdValue`): 25→2, 30→14, 50→5, 60→16, 24→13, 48→15,
  120→7. `VideoStandard`: PAL=0(25/50), NTSC=1(30/60). DUML set-video-params `0x02/0x18`.
- Camera extras (cmd_ids exact): metering `0x02/0x22`, focus `0x02/0x24/0x30/0x32`, sharpness/contrast/
  saturation `0x02/0x38/0x3A/0x3C`, AE-lock `0x02/0x68`, pano mode `0x02/0x6E`, timelapse para
  `0x02/0x4A`, format-SD `0x02/0x72`, zoom `0x02/0x34` (`09 00 00 <u16=×100>`), photo mode `0x02/0x6A`,
  codec `0x02/0xAB` (H264/H265). `drone.py` has photo/record/mode/zoom/ISO/EV/WB/format/codec.

---

## 5. MEDIA MANAGER — the file-list / download state machine (fixes the `0xe0` NAK)

Deep detail: `MEDIA_TRANSPORT_TRUTH.md` (authoritative, native-verified), `MSDK_MEDIA_SEQUENCE.md`,
`DOMAIN_media_album.md`, `MEDIA_TRANSFER.md`.

### 5.1 MSDK state machine & call order
`camera.isMediaDownloadModeSupported()` gates everything. `mm = camera.getMediaManager()`;
`scheduler = mm.getScheduler()` (may start **SUSPENDED** — must `resume()`).
**`FileListState`**: `UNKNOWN, SYNCING (busy), INCOMPLETE (delta — don't clear), DELETING (busy),
RENAMING, RESET (→ next refresh is full), UP_TO_DATE`. Refresh outcome `Reset→Syncing→UpToDate` (full)
or `Incomplete→Syncing→UpToDate` (delta). No published integer ordinals — derive from captures.

**Enter-media-mode has TWO mutually-exclusive paths**, chosen by `camera.isFlatCameraModeSupported()`:
- Path A (legacy, `false`): `camera.setMode(CameraMode.MEDIA_DOWNLOAD)` → exit `setMode(SHOOT_PHOTO)`.
- **Path B (flat-mode — Mavic Mini / WM160, `true`): `camera.enterPlayback()` → `camera.exitPlayback()`.**

**Ordered list-files sequence (from the samples):**
```
1. isMediaDownloadModeSupported() == true
2. mm = camera.getMediaManager();  scheduler = mm.getScheduler(); if SUSPENDED -> scheduler.resume(cb)
3. ENTER: WM160/flat -> camera.enterPlayback(cb)   (legacy -> setMode(MEDIA_DOWNLOAD, cb))
4. on success: mm.refreshFileListOfStorageLocation(StorageLocation.SDCARD, cb2)
   (gate: do NOT refresh while state ∈ {SYNCING, DELETING})
5. on cb2 success: List<MediaFile> = mm.getSDCardFileListSnapshot();  sort by getTimeCreated()
6. thumbnails/previews: per file scheduler.moveTaskToNext(new FetchMediaTask(file, THUMBNAIL, cb))
```
**Snapshot ≠ live list** — wait for UP_TO_DATE / the refresh cb. INCOMPLETE = delta (don't clear).

### 5.2 MediaFile & transports
`MediaFile`: `getFileName/getFileSize/getIndex/getMediaType/getTimeCreated/getDurationInSeconds(0 for
photos)/getResolution/getFrameRate/getStorageLocation/isValid`. `MediaType`: JPEG/MP4/MOV/DNG/TIFF/
PANORAMA/SHALLOW_FOCUS/QUICK_SHOT (PANORAMA/SHALLOW_FOCUS are multi-part → `fetchSubFileDataList`,
only MOV/MP4 playable). **Two different transports:**
- Full download: `fetchFileData(dir, name, DownloadListener)` → callbacks `onStart / onRateUpdate
  (total,current,persize) / onRealtimeDataUpdate(bytes,pos,isLast) / onProgress / onSuccess(path) /
  onFailure`. **Downloads are serialized** — chain the next in `onSuccess`; cancel with
  `mm.exitMediaDownloading()`. Resume via `fetchFileByteData(offset, listener)`.
- Thumbnail/preview/metadata: the **scheduler** + `FetchMediaTask(media, FetchMediaTaskContent
  {PREVIEW/THUMBNAIL/CUSTOM_INFORMATION/XMP_INFORMATION}, cb)` — `scheduler.resume()` mandatory first.
- Delete: `mm.deleteFiles(List<MediaFile>≤255, cb)`; state → DELETING; remove from local snapshot.
- Video playback (stream, not download): `playVideoMediaFile/pause/resume/stop/moveToPosition`;
  `VideoPlaybackStatus {STOPPED, PLAYING, PAUSED}`.

### 5.3 WM160 DUML — the exact frames and why we were getting `0xe0`
- Transport = the **same AOA DUML datalink** as flight/video — **no separate FTP/RNDIS/socket** for
  the RC path (native `FileTaskManager`/`CommonFileDownloadHandler`/`FileTransferHandler` → the AOA
  service port). The HTTP/CURL and WiFi-highspeed paths in `libsdk_jni.so` are for direct-WiFi/cloud
  products — decoys for us.
- Wire commands (receiver camera **0x01**): **`0x00/0x20 get_file_list`**, **`0x00/0x1F
  get_file_data`** (thumbnail/screennail/original), **`0x00/0x28 delete_file`**. cmd_set 0x00 is on the
  **non-encrypted** list. Data returns as windowed DUML `file_transfer_push` frames needing
  **selective-ACKs** back (`FileTransferHandler::SendACKPack`) or the pump stalls; `SendAbortPack` to
  stop.
- **Root cause of `0xe0`** (= firmware "command refused / not available in this state", generic NAK):
  we were entering **PLAYBACK (2)**; the file family (`0x00/0x1F/0x20/0x28`) is serviced **only in
  MEDIA_DOWNLOAD (3)** — a `download_mode` state the firmware tracks separately from `liveview_mode`.
  **Fix: `0x02/0x10 set_camera_working_mode` with byte = 3 (MEDIA_DOWNLOAD)**, wait for the camera to
  *report* download_mode (not just the `0x00` ack), then `0x00/0x20`. Fallback probe byte 6 (DOWNLOAD).
  `0x02/0x0C switch_playbackmode`, `0x02/0x09`, `0x02/0xB3` each `0xe0` on WM160 — those cmd_ids simply
  aren't implemented on the 2019 FC7203 firmware and are **not needed** (the app drives the Mini via the
  legacy `SpecialCommandManager` / `0x02/0x10` path). `drone.enter_playback` already sends byte 3 —
  correct.
- **List request/response layout** (SDK-internal `FileListRequest.toBytes`, prefix 46 B — note the
  native *re-serializes* the real request, so treat this as the CSDK format, confirm the wire bytes
  with one capture): req `[+0 index i32][+12 type i32: MEDIA=0/COMMON=1/MEDIA_FOLDER=4][+16 slot i32:
  EXTERN1(SD)=0][+29 isSubMedia u8][+34 filter i32×]`. Response `FileList→FilePackage→MediaFile[]`;
  per-file `MediaFile`: `valid u8, fileIndex i32 (primary id), fileType i32 (0=JPEG,1=DNG,2=MOV,3=MP4,
  4=PANORAMA), fileName str, fileSize i64, date(24 B), starTag i32, duration i64 ms`. Page with
  index/count until `isPageLastFile`. `CameraStorageSlot`: EXTERN1=0(SD), INTERNAL1=1, UNKNOWN=65535.
- **Known WM160 quirk** (`Mobile-SDK-Android` #618/#1188): `getSDCardFileListSnapshot()` returns empty
  on Mini/Mini 2 even when DJI Pilot lists files — the MSDK abstraction is finicky on exactly this
  hardware, which is precisely why raw-DUML is the right call. Budget for the flat-mode enter step +
  correct storage-index paging.

**Ordered DUML sequence:**
```
1. 0x02/0x10 -> camera 0x01, byte 3 (MEDIA_DOWNLOAD)    [fallback probe 6];  wait for download_mode state
2. 0x00/0x20 get_file_list -> 0x01 (slot EXTERN1=0, type MEDIA=0); page index/count
3. 0x00/0x1F get_file_data -> 0x01; ACK the windowed file_transfer_push frames back
4. 0x00/0x28 delete -> 0x01 (same mode)
5. restore liveview: 0x02/0x10 byte 1 (RECORD_VIDEO) or 0 (SHOOT_PHOTO)
```
**Still needs one live album capture** for: the exact on-wire `get_file_list_req`/`get_file_data_req`
byte layout (native-built), the enter-playback frame details, and the `file_transfer_push` window +
selective-ACK framing (`MEDIA_TRANSPORT_TRUTH.md §5`).

---

## 6. GIMBAL

WM160 gimbal is **pitch-only** in software (roll/yaw are mechanically stabilized but not
user-controllable). Deep detail: `CAMERA_AND_NOGPS.md §(e)`, `DUML_COMMANDS_FULL.md` cmd_set 0x04.

**MSDK.** `GimbalMode`: `FREE` (independent of aircraft yaw), `FPV` (yaw+roll fixed to airframe, pitch
only), `YAW_FOLLOW` (yaw follows heading, pitch+roll), `UNKNOWN` — WM160 is effectively YAW_FOLLOW-like
(single pitch axis). `rotate(Rotation{mode, pitch, roll, yaw, time}, cb)` with `RotationMode`
`ABSOLUTE_ANGLE` (rel. aircraft heading, 0=level/forward) / `RELATIVE_ANGLE` (delta) / `SPEED`
(deg/s, sign=direction). `reset(Axis{YAW,PITCH,ROLL,YAW_AND_PITCH}, ResetDirection{UP_OR_DOWN,CENTER})`
or legacy `reset()` (pitch→level, yaw→forward). `setPitchRangeExtensionEnabled(bool)` = DJI Fly "allow
upward gimbal rotation". `GimbalState`: `attitudeInDegrees{pitch,roll,yaw}`, `isAttitudeReset`,
`isCalibrating`, `isPitch/Yaw/RollAtStop`, `mode`.

**WM160 ranges (confirmed):** pitch **−90°..0°** default; **−90°..+20°** with pitch-range-extension /
"allow upward rotation" enabled. Roll/yaw not adjustable.

**WM160 DUML (receiver GIMBAL 0x04):**
| cmd | class | payload |
|---|---|---|
| **0x04/0x0C** speed | `DataGimbalSpeedControl` | `[yaw·10 i16][roll·10 i16][pitch·10 i16][flags]` (°/s, send ~10 Hz, `0x81 00`) |
| **0x04/0x14** abs angle | `DataGimbalAbsAngleControl` | `[yaw i16][roll i16][pitch i16][0x00][flags]` |
| 0x04/0x0A angle+dev | `DataGimbalAngleControl` | 10 B with allowDeviation + duration |
| **0x04/0x4C** recenter/set-mode | `DataGimbalNewResetAndSetMode` | **`[workMode u8][resetCmd u8]`** |
| 0x04/0x08 auto-cali | `DataGimbalAutoCalibration` | empty |
| 0x04/0x01 control | `DataGimbalControl` | `[pitch u16][roll u16][yaw u16][mode u16→1B]` |
- **Recenter** = `0x04/0x4C` payload `[0xFE, 0x01]` (workMode 0xFE=keep current; resetCmd
  `RECENTER=0x01`, `SELFIE=0x02`, `PITCH_YAW=0x03`, `ONLY_PITCH=0x04`, `ONLY_ROLL=0x05`,
  `ONLY_YAW=0x06`). Work-mode enum (byte0): `YawNoFollow=0x00, FPV=0x01, YawFollow=0x02, OTHER=0xFE`.
  **Do NOT use 0x04/0x13** (that's `reset_default_params` = user calibration reset, not recenter).
- `drone.py gimbal_speed`/`gimbal_angle`/`gimbal_recenter` are correct (recenter `[0xFE,0x01]`).
  ⚠ Enum ordinals ≠ wire; the gimbal work-mode byte must be confirmed empirically.

---

## 7. BATTERY / TELEMETRY

### 7.1 MSDK `DJIBatteryState`
| property | type | unit | WM160 note |
|---|---|---|---|
| `chargeRemainingInPercent` | UInt | % | ✅ reliable |
| `chargeRemaining` | UInt | mAh | ✅ |
| `fullChargeCapacity` | UInt | mAh | ✅ |
| `designCapacity` | UInt | mAh | constant |
| `voltage` | UInt | **mV** | ✅ |
| `current` | Int | **mA** (signed: −discharge) | ✅ |
| `temperature` | double | °C | ✅ |
| `numberOfDischarges` | UInt | cycles | ⚠ 0/absent (non-smart pack) |
| `lifetimeRemaining(InPercent)` | UInt | % | ⚠ 0 on WM160 |
| cell voltages (`cellVoltages`) | array mV | | ⚠ smart batteries only |
| `isBeingCharged` | Bool | | |
**WM160 = non-smart 2S battery** → trust percent/voltage/current/temp; lifetime/cycles/per-cell are
best-effort.

### 7.2 WM160 DUML battery — `0x0D/0x02` (`DataSmartBatteryGetPushDynamicData`), verified byte-perfect
| field | offset | type | scale | unit |
|---|---|---|---|---|
| index | 0x00 | u8 | | |
| **voltage** | 0x01 | u32 | ×1 | mV |
| **current** | 0x05 | **s32** | ×1 | mA (neg=discharge — read `<i`) |
| full capacity | 0x09 | u32 | ×1 | mAh |
| remain capacity | 0x0D | u32 | ×1 | mAh |
| **temperature** | 0x11 | s16 | ×0.1 | °C |
| cell size | 0x13 | u8 | | count |
| **remaining %** | 0x14 | u8 | ×1 | % |
Per-cell mV = `0x0D/0x03` (u16 LE per cell from ~offset 0x02). *`telemetry.py` is missing temperature
(s16@0x11 ×0.1) and must read current as signed s32.*

### 7.3 MSDK `DJIFlightControllerState` (the OSD/telemetry core; 10 Hz push)
`aircraftLocation`(lat/lon/alt), `altitude`(m rel. takeoff, baro), `takeoffLocationAltitude`(m),
`velocityX/Y/Z`(m/s, **N-E-D**), `attitude{pitch,roll,yaw}`(deg), `isFlying`, `areMotorsOn`,
`flightTimeInSeconds`, `satelliteCount`, `GPSSignalLevel`(enum), `flightMode`/`flightModeString`,
`isHomeLocationSet`, `homeLocation`, `goHomeHeight`(m), `goHomeExecutionState`,
`goHomeAssessment`, `batteryThresholdBehavior`, `isLowerThanBatteryWarningThreshold`,
`isLowerThanSeriousBatteryWarningThreshold`, `isLandingConfirmationNeeded`.
- `goHomeAssessment`: `remainingFlightTime`(s) ← **this is where "remaining flight time" lives**,
  `timeNeededToGoHome`(s), `batteryPercentageNeededToGoHome`(%), `maxRadiusAircraftCanFlyAndGoHome`,
  `aircraftShouldGoHome`. `batteryThresholdBehavior`: `FLY_NORMALLY / GO_HOME / LAND_IMMEDIATELY / UNKNOWN`.
- `GPSSignalLevel` (uint8): 0 `LEVEL_0` almost none … 2 weak (gohome works) … 3 good (hover) … 4 very
  good (records home) … 5 strong; `NONE`.

### 7.4 WM160 DUML OSD — `0x03/0x43` (`DataOsdGetPushCommon`), verified offsets
| field | offset | type | scale | unit |
|---|---|---|---|---|
| longitude | 0x00 | f64 | radians→deg | ° |
| latitude | 0x08 | f64 | radians→deg | ° |
| **altitude (rel. baro height)** | 0x10 | s16 | ×0.1 | m |
| velocity X/Y/Z (N/E/**climb**) | 0x12/0x14/0x16 | s16 | ×0.1 | m/s |
| pitch / roll / yaw | 0x18/0x1a/0x1c | s16 | ×0.1 | ° |
| **flyc_state** | 0x1e | u8 `&0x7f` | | enum (§3.3) |
| gohome status | 0x20 | u32 `>>5 &0x7` | | enum |
| is_flying | 0x20 | u32 `&0x0e` | | bool |
| motors on | 0x20 | u32 bit3 | | bool |
| GPS signal level | 0x20 | u32 `>>0x12 &0xf` | | 0..5 |
| satellite count | 0x24 | **u16** | | count |
| **motor start-fail cause** | 0x26 | u8 `&0x7f` | | enum (§10) |
| battery % (FC copy) | 0x28 | u32 | | % |
| VPS/ultrasonic height | 0x29 | s16 | ×0.1 | m |
| flight time | 0x2a | u16 | ×1 | s |
Home lat/lon = f64 **radians** @ 0x00/0x08 (aircraft *and* home use the radians convention).
**Remaining flight time is NOT in 0x0D/0x02** — it's a computed FC push (`u16 seconds @0x00` of
`uav_fc_...battery_capacity_gohome_landing...push` / `uav_fc_electricity_push`), same as MSDK's
`goHomeAssessment.remainingFlightTime`. *`telemetry.py` fixes needed: read motor-fail cause at 0x26 (not
0x33), satellites as u16, current as signed, add temperature & VPS height, fix FLYC names ≥16
(`TELEMETRY_TRUTH.md §6`).* Deep detail: `TELEMETRY_TRUTH.md`, `TELEMETRY_TABLE.txt`.

---

## 8. REMOTE CONTROLLER / AIRLINK

Deep detail: `DOMAIN_rc_functions.md`.

### 8.1 Remote controller (MSDK)
- **Flight-mode switch** (`DJIRCFlightModeSwitch` / `…HardwareFlightModeSwitchState`): positions
  `ONE/TWO/THREE` = P/S/F; read via `DJIRCHardwareState.flightModeSwitch`. **WM160 RC has no physical
  switch** — mode is app/SDK-driven, P default.
- **Control authority:** there is no single "request control" RC call for a consumer bird — automation
  is gained via the FlightController **Virtual Stick** path (§3.1). RC hand-off is the automation entry.
- **NOT for WM160:** `RemoteControllerMode` MASTER/SLAVE (Inspire 1/M100 only),
  `requestGimbalControlRight…`, `setControllingGimbalIndex` (M300 only), `DJIRCBatteryState` (Smart
  Controller only), `DJIRCGPSData` (RCs with built-in GPS only). Skip all of these.

### 8.2 WM160 RC DUML (cmd_set 0x06, receiver RC 0x06) — the useful bits
- **Live stick/button push:** `0x06/0x05 DataRcGetPushParams` — `getAileron`@0(roll), `getElevator`@2
  (pitch), `getThrottle`@4, `getRudder`@6 (all 2 B, unsigned, DJI 364..1024..1684). Dial/gyro offsets
  are RC-model-dependent (multiple getters @8/13/16/20/22/24) — **which one the Mavic Mini RC uses
  needs a live capture.** Buttons (C1/C2/5-D) via `0x06/0x51` (bit flags byte 0).
- Channel config read `0x06/0x01` (empty req, 3-byte records); hardware min/mid/max `0x06/0x04`.
- Stick mode `0x06/0x19` (`Japan=1/America=2/China=3/Custom=4`); CE/FCC power `0x06/0x20` (`CE=0/FCC=1`);
  gimbal-dial axis `0x06/0x35` (`Pitch=0/Roll=1/Yaw=2`), speed `0x06/0x2B`, gain `0x06/0x33`.
- Stick calibration `0x06/0x03` (name-only), progress `0x06/0xF8` (8-segment A–H). Not needed for PC
  virtual-stick flight. Frequency pairing `0x06/0x2F` (`Current=0/Enter=1/Cancel=2`). RC battery push
  `0x06/0x1E` (`volume@0` u32, `percent@4` u8). Firmware info `0x06/0x79`.
- **Hand-off to PC:** `0x06/0xF1 uav_rc_set_app_to_pc_control` (name-only, try payload `01`). The
  `0x06/0x05` push is still useful as the *echo* of what the physical RC is doing (takeover/override).
- **NOT-WM160 on this set:** `0x06/0x3D` MultiRcPairing, `0x06/0x46` select-target, `0x06/0x49` RTK,
  `0x06/0x4A` 4G, `0x06/0x6B` racing-RC, `0x06/0x99` follow-focus.

### 8.3 AirLink (MSDK)
`DJIAirLink` exposes only the sub-links a product has. **WM160 = "Enhanced Wi-Fi" → `DJIWiFiLink`
only** (no OcuSync/Lightbridge/auxiliary/master-slave/gimbal-index — skip entirely). `DJIWiFiLink`:
`setChannelSelectionMode(AUTO|MANUAL)`, `setChannelNumber`/`getChannelNumbers` (1–13 = 2.4 GHz, higher
= 5.8 GHz — WM160 supports both bands), `setBandwidth`, `setDataRate`, `setSSID`/`setPassword`,
`getSignalQuality` (0–100). Video: `DJIVideoFeeder.primaryVideoFeed` delivers **H.264** frames
(`videoFeed:didUpdateVideoData:`) → `DJIVideoPreviewer`/`DJICodecManager`. WM160 video feed works.
`DJIVideoFeedPhysicalSource` tags the source (single primary camera on WM160).

**WM160 DUML video** (`MASTER_REPORT.md §2.3`, `liveview.py`): H.264 **not encrypted**;
`[16-byte header][H.264 slice]`; start via `0x02/0x09` camera select, `0x08/0x41` decoder caps (26 B),
`0x08/0x42` fps, `0x08/0x69` bandwidth, then request an I-frame `0x02/0xB3`. Transport = the AOA
composite mux (video on unit types `0x574x`, DUML on `0x5749`). `drone.start_liveview` implements this.

---

## 9. FC PARAMETERS — the hash mechanism (unlocks speed/gains/RTH-alt/novice)

Deep detail: `PARAM_WIRE.md`, `PARAM_HASH.md`, `INTELLIGENT_AND_PARAMS.md §B`.

**Wire:** `0x03/0xF9 DataFlycSetParams` write = `[hash u32 LE][value size-bytes LE]` (batched pairs);
`0x03/0xF8` read-by-hash `[hash u32 LE]`; `0x03/0xF7` get-info-by-hash; `0x03/0xF0` get-info-**by-index**
`[index u16 LE]` → returns name/type/size/range **but NO hash**; `0x03/0xFA` reset-by-hash. Frame
`cmd_type=0x40` (req-with-ack), sender APP 0x02, receiver FLYC 0x03. Value width by `TypeId`:
`0 INT08U(1) 1 INT16U(2) 2 INT32U(4) 3 INT64U(8) 4 INT08S(1) 5 INT16S(2) 6 INT32S(4) 7 INT64S(8)
8 FLOAT(4) 9 DOUBLE(8) 10 BYTE 11 STRING`.

**The circular dependency (real):** the drone never returns the name↔hash mapping. The 687 param
*names* ship as an app resource (`flyc_param_infos.json`); each 32-bit hash is computed locally by
native `GroudStation.native_hashFromString(GBK-bytes)` in `libGroudStation.so`. **Unblock:** either
(a) Frida-call that native fn over the 687 names to dump the full table, or (b) reverse the one
function (our `param_hash.py` re-implements `h=(b+(h<<8))%(2^32−5)` over GBK — verify it matches).
Key param names: `flying_limit.max_height`[236], `flying_limit.max_radius`[235],
`go_home.fixed_go_home_altitude`[212], `novice_cfg.novice_func_enabled_0`[343], `fc_dark_need_gps_0`
(dark unlock), `g_config.control.horiz_vel_atti_range_0`[312], `vert_up_vel_0`[318]/`vert_down_vel_0`
[319], `serial_api_cfg.input_*_limit`[363–366], `serial_api_cfg.advance_function_enable`[362].

**Encryption caveat** (`PARAM_WIRE.md §6`, `DUML_ENCRYPTION.md`): param frames are plaintext by default,
but if the link negotiated encryption the FC drops plaintext cmd_set-0x03 frames (SIMPLE XOR,
cmd_type→0x43). `drone.py` SIMPLE-encrypts `0x03/0xF0/0xF7/0xF8/0xF9/0xFA` — capture-confirm whether
WM160/AOA actually requires it. cmd_set 0x00 (media/common) is exempt.

---

## 10. DIAGNOSTICS / "cannot take off" gates (read OSD +0x26, `&0x7f`)

The FC reports why it won't spin motors as a single byte, parsed by
`DataOsdGetPushCommon$MotorStartFailedCause` (full 96-code table in `TAKEOFF_UNLOCK.md §1`,
`diag_codes.py`). The ones a PC can **clear with a command**:
`2 AssistantProtected` (disconnect the 0x0A sender), `4 DistanceLimit` (0x03/0x2D Far),
`10 NoviceProtected` (novice[343]=0), `19/28 Simulator` (stop sim 0x0B), `22 UnActive` (activate
0x00/0x32+0x03/0x62), `23 FlyForbiddenError` (NFZ unlock 0x03/0x41+0x47 or relocate),
`115 LOCK_BY_APP`/`119 STOP_BY_APP` (your own force-disable / re-arm), `116 START_FLY_HEIGHT_ERROR`
(0x03/0x2D High), `136 RC_THROTTLE_NOT_MIDDLE` (send neutral sticks), `147 DARK_NEED_GPS` (§3.6),
`162 FIRST_TAKE_OFF_WARNING` (re-send AUTO_FLY), plus recalibration-fixable IMU/compass (5/24/37/38/
118/120 via `0x03/2A 09`). Everything else is `PHYS` (hardware/battery/firmware — the app can't bypass
it either) or `WAIT` (transient — poll +0x26). **Activation** is a hard one-time gate
(`DRONE_NOT_ACTIVATED`); after it, no per-flight login exists. Diagnostic text is on-device
(`res/raw/hms2sdkcode.json` → DiagnosticCode → `strings.xml`, 743 codes in `diag_codes_full.py`;
`ERROR_CODES.md`).

---

## 11. TRANSPORT / DUML FRAMING (recap — `MASTER_REPORT.md §2`, `duml.py`)

- **AOA composite mux:** `[0]=0x55 [1]=0xCC | type u16 LE | length u32 LE | payload`. Route by type:
  `0x5749`=DUML, `0x574A/0x574D`=video, others=aux. Resync on `55 CC`.
- **DUML frame:** `55 | len(10b)+ver(6b) | CRC8(seed 0x77) | sender | receiver | seq u16 LE | cmd_type
  | cmd_set | cmd_id | payload | CRC16(seed 0x3692)`. `cmd_type = (CMDTYPE<<7)|(NEEDACK<<5)|Encrypt`:
  req-with-ack = **0x40**, ack reply = 0xC0, SIMPLE-encrypted = 0x43. Sender for flight = APP **0x02**.
- **DUML SIMPLE encryption** (`DUML_ENCRYPTION.md`): self-inverse byte-keystream XOR, static key, only
  for FC config frames (cmd_set 0x03) once the link negotiates it; COMMON 0x00 exempt.

---

## 12. CROSS-REFERENCE TO OUR PYTHON CLIENT (`drone.py`, `control.py`)

**Correct / confirmed:** DUML framing & CRCs (`duml.py`); device addresses; `takeoff`=0x03/2A/01,
`start_motors`=0x07, `stop_motors`=0x08, `land`=0x02, RTH=0x06, cancels 0x0C/0x0D/0x0E;
`set_max_altitude/distance`=0x03/0x2D `[mode][u16]`; camera ISO/EV/exposure enum indices; gimbal
speed/angle/recenter (`[0xFE,0x01]`); `enter_playback`=0x02/0x10 byte 3; `unlock_no_gps` writes
`fc_dark_need_gps_0=0` (right name + polarity); virtual-stick TLV packing/scale.

**To fix (spotted across the reverse):**
1. `motor_force_disable` sends 2 bytes `[flag,mode]` → the app builder sends **1 byte** `[isDisable]`.
2. `control.py` `DEV_APP=0x0a` constant in the standalone `build_flight_frame` is a trap — flight must
   send as **0x02** (the live `Drone._cmd` path already uses 0x02; the stray 0x0a constant should go).
3. `telemetry.py`: motor-fail cause is at **+0x26 `&0x7f`** (not 0x33); satellites u16@0x24; battery
   current is **signed** s32@0x05; add temperature s16@0x11 ×0.1 and VPS height s16@0x29; FLYC-state
   name dict is wrong ≥16 (use §3.3 / `TELEMETRY_TRUTH.md`).
4. `set_flight_mode` is correctly `NotImplementedError` — mode = control-gain params (hash), not a
   command on WM160.
5. `set_horizontal_speed` param name/type is best-effort — confirm the hash live.
6. Virtual-stick loop `hz=20` is inside the 5–25 band; bump to 25 Hz for margin; confirm which
   encoding (0x01/0x0A vs 0x03/0x8E) WM160 accepts + the `flag` byte for 0x03/0x8E.

---

## 13. GAPS STILL NEEDING A LIVE CAPTURE (Frida hook named)

1. **Virtual-stick encoding WM160 accepts** (0x01/0x0A special-TLV vs 0x03/0x8E float) and the
   `0x03/0x8E` `flag` mode byte; whether `0x03/0x80`+`0x49/0x80`(+`serial_api_cfg.advance_function_enable
   =1`) are hard preconditions — hook `DataBase.start()` + the native `libsdk_jni` DUML writer while
   flying with virtual sticks. **#1 priority.**
2. **Param name→hash table** — Frida-call `GroudStation.native_hashFromString` over the 687 names, or
   hook `DataFlycSetParams.start()` while moving a slider; then verify `param_hash.py` matches.
3. **DarkNoGpsLock exact DUML** — hook `DataBase.start()` while tapping the app's "fly in low light
   without GPS" Unlock once (confirm it's `0x03/0xF9 fc_dark_need_gps_0=0` vs a KeyValue command).
4. **Media wire bytes** — one live album capture for the native-built `get_file_list_req`(0x00/0x20) /
   `get_file_data_req`(0x00/0x1F) byte layouts, the WM160 enter-playback frame, and the
   `file_transfer_push` window + selective-ACK framing. Confirm mode byte 3 (vs 6) populates the list.
5. **RC `0x06/0x05` push layout for the Mavic Mini RC** — which dial/gyro offset is real; stick
   range/mid — hook `DataRcGetPushParams`/`DataBase.get` or sniff `55…06 05…`.
6. **QuickShot ActionType code→name** (obfuscated a–h = {0,1,2,3,4,6,8,10}) and timelapse/pano
   sub-mode numeric mappings — hook `DataEyeSetQuickMovieParams.doPack` while launching each.
7. **Compass/IMU calibration DUML** (whether `Compass.StartCompassCalibration` emits `0x03/2A/09` or a
   key-only frame) — hook `DataBase.start()` during in-app calibrate.
8. **Whether WM160 honours `0x03/0x2D` limits vs the hashed `flying_limit.max_height`**, and whether
   the AOA link requires SIMPLE encryption on cmd_set-0x03 param frames — send + read back.
9. **DUML wire bytes behind any KeyValue key** (`name→cmd_set/cmd_id`) — native black box; hook
   `JNIKeyValue.native_set/get_sync/do_action/listen` and correlate to on-wire DUML.
10. **Exact WM160 2.7K (2720×1530) DUML video-resolution byte** (4.3.2 table predates WM160) — read the
    camera video-params push on-device.

---

### Source index
- MSDK V4: developer.dji.com (FlightController/Camera/Gimbal/Battery/RemoteController/AirLink/KeyManager
  references + component guides + Constants.html), `dji-sdk/Mobile-SDK-{Android,iOS}` samples
  (VirtualStickView.java, FCVirtualStickViewController.m, FetchMediaView/MediaPlaybackView,
  MediaManagerDemo), CFR decompile of `com.dji:dji-sdk:4.3.2` (enum ints + `dji.midware.data.model.P3.*`),
  `Mobile-SDK-Android` issues #455/#592/#594/#606/#618/#728/#1105/#1188, o-gs/dji-firmware-tools
  (`dji-dumlv1-flyc.lua`).
- WM160 DUML ground truth (this repo): `MASTER_REPORT.md`, `FLIGHT_GATING.md`, `TAKEOFF_UNLOCK.md`,
  `DARK_NOGPS_TRUTH.md`, `CAMERA_AND_NOGPS.md`, `TELEMETRY_TRUTH.md`, `MEDIA_TRANSPORT_TRUTH.md`,
  `MSDK_MEDIA_SEQUENCE.md`, `MEDIA_TRANSFER.md`, `DOMAIN_media_album.md`, `DOMAIN_keyvalue_sdk.md`,
  `DOMAIN_rc_functions.md`, `PARAM_WIRE.md`, `PARAM_HASH.md`, `INTELLIGENT_AND_PARAMS.md`,
  `DUML_COMMANDS_FULL.md`, `ERROR_CODES.md`, `full_table.txt`/`cmdmap.txt`/`TELEMETRY_TABLE.txt`,
  and the client `drone.py`/`control.py`/`telemetry.py`/`duml.py`/`param_hash.py`.
