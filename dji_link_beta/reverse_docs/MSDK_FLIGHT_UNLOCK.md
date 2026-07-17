# MSDK FLIGHT-CONTROL (virtual sticks) + UNLOCK/PARAM — reconciled with our DUML client

Scope: extract the exact MSDK v4 virtual-stick and unlock/limit sequences and map them onto our
raw-DUML client for the DJI Mavic Mini 1 (WM160). Every claim is cited to an MSDK doc, an MSDK
sample, or a local reverse-doc/source file. Where a fact is only decidable on hardware it is marked
**[HW]**.

Sources (web):
- DJIVirtualStickFlightControlData ref — developer.dji.com/iframe/mobile-sdk-doc/android/reference/dji/common/flightcontroller/DJIVirtualStickFlightControlData.html
- DJIFlightController (VirtualStickControlMode) category — developer.dji.com/iframe/mobile-sdk-doc/ios/Categories/DJIFlightController(VirtualStickControlMode).html
- DJIFlightControllerDataType (constants) — developer.dji.com/iframe/mobile-sdk-doc/android/reference/dji/sdk/FlightController/DJIFlightControllerDataType.html
- Virtual Stick tutorial — developer.dji.com/doc/mobile-sdk-tutorial/en/tutorials/virtual-stick.html
- DJI SDK Forum "Chapter 7: The virtual stick" and "What is the recommended configuration of virtual stick?"
- dji-sdk/Mobile-SDK-Android sample VirtualStickView.java; dji-sdk/Mobile-SDK-iOS sample FCVirtualStickViewController.m

Sources (local):
- `reverse_docs/FLIGHT_GATING.md` §H (three stick encodings + preconditions, from `libGroudStation`/app DEX)
- `reverse_docs/DARK_NOGPS_TRUTH.md` (fc_dark_need_gps_0 polarity/name, from DJI Fly v1.21.4 DEX)
- `control.py` (`FlightProfile`, `sticks_to_payload`), `drone.py` (`set_sticks`, `set_sticks_float`, `set_param`, `unlock_no_gps`)

---

## 0. IMPORTANT correction to the task premise

The task says "we send 0x03/0x8E set_sticks". **That is not what our client actually does by default.**
- `Drone.set_sticks()` (`drone.py:117`) sends `FlightProfile.cmd_set/cmd_id`, which default to
  **`0x01 / 0x0A` special-TLV** (`control.py:43-44`) — a 4×11-bit RC-emulation packet, *not* physical units.
- `0x03/0x8E` is only reachable via the separate **`Drone.set_sticks_float()`** (`drone.py:243`), which is
  the physical-unit (MSDK-like) float joystick and is **not** wired into the keyboard loop.

So there are two candidate stick paths in our own code, and the keyboard flies path #1 today. Section 1
maps MSDK onto **both** so we can fix whichever the WM160 FC actually honors **[HW]**.

---

## 1. VIRTUAL STICKS — exact MSDK sequence, fields, modes, ranges, rate

### 1a. The MSDK v4 call sequence (iOS/Android identical semantics)
```
1. flightController.setVirtualStickModeEnabled(true, completion)   // precondition toggle
   // gate: property isVirtualStickControlModeAvailable must be YES/true
2. set the 4 control-mode properties (see 1c) — do this BEFORE step 3
3. (optional) flightController.startTakeoff(...)                   // see §3: sticks alone don't arm
4. every 40 ms (25 Hz): flightController.sendVirtualStickFlightControlData(data, completion)
5. when done: setVirtualStickModeEnabled(false, ...)
```
- `setVirtualStickModeEnabled(true)` "enables virtual stick control mode, and by enabling it the
  aircraft can be controlled using `sendVirtualStickFlightControlData`" (DJIFlightController ref).
- `sendVirtualStickFlightControlData` "requires `isVirtualStickControlModeAvailable` to be true, and
  virtual stick commands should be sent to the aircraft **between 5 Hz and 25 Hz**. If virtual stick
  commands are not sent frequently enough the aircraft may regard the connection as broken which will
  cause the aircraft to hover in place until the next command comes through" (DJIFlightController ref).
  → **Send rate: 25 Hz (40 ms period). Never drop below 5 Hz.** The dji-sdk samples use a repeating
  timer at 40 ms; our keyboard loop is 20 Hz (`control.py:112 hz=20`) which is inside the band but bump
  to 25 Hz for margin.

### 1b. DJIVirtualStickFlightControlData — the four fields
Four `float` fields (DJIVirtualStickFlightControlData ref):
| field | meaning (units depend on mode, §1c) |
|---|---|
| `pitch` | Velocity (m/s) **or** Angle (deg) |
| `roll` | Velocity (m/s) **or** Angle (deg) |
| `yaw` | Angular velocity (deg/s) **or** Angle (deg) |
| `verticalThrottle` | Velocity (m/s) **or** Altitude/Position (m) |

### 1c. Control-mode enums + defaults
Properties on the flight controller (DJIFlightController VirtualStickControlMode category):
- `rollPitchControlMode`: **Velocity** | **Angle**  (Android: `RollPitchControlMode.VELOCITY/ANGLE`)
- `yawControlMode`: **AngularVelocity** | **Angle**
- `verticalControlMode`: **Velocity** | **Position**
- `rollPitchCoordinateSystem`: **Ground** | **Body**

Two "default" sets to be aware of:
- **SDK reset-defaults (applied on FC (re)connect):** rollPitch = **Angle**, yaw = **Angle**,
  vertical = **Velocity**, coordinate = **Ground**. Because these reset, you must set them explicitly
  every session (FLIGHT_GATING.md §H; DJIFlightController ref).
- **DJI's own recommended config** (SDK Forum "recommended configuration of virtual stick"):
  `verticalControlMode = VELOCITY`, `rollPitchControlMode = VELOCITY`,
  `yawControlMode = ANGULAR_VELOCITY`, `rollPitchCoordinateSystem = BODY`.
  Use this — velocity + Body is the intuitive "fly like a game" mapping.

Coordinate system meaning: **Ground** = axes fixed to North/East (cardinal); **Body** = axes fixed to
the aircraft's current heading (nose = forward). For keyboard "forward = where the nose points", use
**Body**.

### 1d. Value ranges / constants (DJIFlightControllerDataType)
- Roll/Pitch **Velocity**: `DJIVirtualStickRollPitchControlMaxVelocity = 15` m/s, Min = **-15** m/s.
- Roll/Pitch **Angle**: Max = **30°**, Min = **-30°**.
- Yaw **AngularVelocity**: `DJIVirtualStickYawControlMaxAngularVelocity = 100` deg/s, Min = **-100** deg/s.
- Yaw **Angle**: Max = **180°**, Min = **-180°**.
- Vertical **Velocity**: `DJIVirtualStickVerticalControlMaxVelocity = 4` m/s, Min = **-4** m/s.
- Vertical **Position**: altitude in metres (0 .. max-flight-height), min = 0.
(15 / 100 / 4 confirmed via DJIFlightControllerDataType search hit.)

### 1e. Sign / axis convention (verify on bench — known DJI footgun)
Intended: with **Body** coordinate + Velocity, `pitch` = velocity along the aircraft's fore/aft axis
(+ = forward), `roll` = velocity along the lateral axis (+ = right), `yaw` (+ = clockwise/turn right),
`verticalThrottle` (+ = up). **However** multiple developers report `pitch` and `roll` behave swapped
vs intuition (DJI SDK Forum "In virtualstick mode, pitch and roll control in opposite directions").
Our `control.py` already defines: `pitch +1 = forward`, `roll +1 = right`, `yaw +1 = right`,
`throttle +1 = up` (`control.py:52-56, 64-74`) — the same intent. **[HW]** Confirm the WM160 doesn't
swap pitch/roll and doesn't invert yaw before trusting the mapping.

### 1f. Mapping onto our DUML payloads
**Path A — `0x03/0x8E` float joystick (`set_sticks_float`, the MSDK-equivalent, physical units):**
`drone.py:243-247` packs `[flag u8][roll f32][pitch f32][yaw f32][throttle f32]` LE — byte-for-byte the
`DataFlycJoystick.doPack` layout (FLIGHT_GATING.md §H). To match MSDK Velocity/AngularVelocity semantics,
feed physical units, not [-1..1]:
```
roll_field     = roll_norm     * 5.0     # m/s   (cap well under the 15 m/s max for safety)
pitch_field    = pitch_norm    * 5.0     # m/s
yaw_field      = yaw_norm      * 60.0    # deg/s (under the 100 deg/s max)
throttle_field = throttle_norm * 2.0     # m/s   (under the 4 m/s max)
```
The **`flag` byte** almost certainly encodes the mode combination (coordinate + rollPitch + yaw +
vertical mode); our code sends `flag=0`. **[HW]** Determine which flag value = {Body, Velocity, AngularVel,
Velocity}. If the FC ignores `set_sticks_float`, the flag/mode is the first suspect.

**Path B — `0x01/0x0A` special-TLV (`set_sticks`, what the keyboard uses today):**
This is RC emulation, not MSDK physical units. It packs 4×11-bit channels, center 1024, ±660 → [364..1684]
(`control.py:77-94`). Channel order is assumed `(roll, pitch, yaw, throttle)` = ch0..ch3
(`control.py:47`, marked HYPOTHESIS). Scaling/bit-packing are exact; **[HW]** confirm the channel order
and that WM160 accepts this path vs Path A. This path has no control-mode concept — it always emulates
stick deflection (behaves like RC gimbals, mode set by the flight gear).

**Which path is authoritative is undecidable statically** (FLIGHT_GATING.md §H) — Path A is the direct
MSDK analogue; Path B is RC-emulation. Try Path A first (matches MSDK exactly), fall back to Path B.

### 1g. GPS vs ATTI
Virtual stick does **not** require GPS. In JOYSTICK mode the FC reports `FLYC_STATE = 17 = JOYSTICK`
regardless of Normal/Sport gear (FLIGHT_GATING.md §H, lines 130/140). With no GPS/VPS it flies in ATTI
and **drifts** (no position hold) — same caveat as §2 dark takeoff. Mavic Mini virtual stick is officially
supported since **MSDK 4.13** (27 Jul 2020, fw 01.00.0500) and the app exposes `isSupportVirtualJoyStick`
(`reverse_docs/isSupport_keys.txt:222`).

---

## 2. UNLOCK / LIMITS / NO-GPS — MSDK levers and the FC params they map to

### 2a. Beginner / novice mode
- MSDK: `flightController.setNoviceModeEnabled(bool, completion)` (a.k.a. beginner mode). Beginner mode
  clamps height/radius to small values and forces GPS. **Disable it** (`setNoviceModeEnabled(false)`)
  before free flight. On WM160 this corresponds to app "Beginner Mode".
- Our client has no direct equivalent; it is an FC param write (hashed `0x03/0xF9`). **[HW]** name TBD.

### 2b. Height / radius limits
- MSDK: `setMaxFlightHeight(int metres, completion)` (range ~20..500 m) and
  `setMaxFlightRadius(int metres, completion)` + `setMaxFlightRadiusLimitationEnabled(bool)` (~15..500 m).
- Our client: **non-hashed** `0x03/0x2D SetLimits [mode u8][value u16 LE m]` — `set_max_altitude`
  (mode 1, clamp 15..500) and `set_max_distance` (mode 2, clamp 15..5000) (`drone.py:166-172`). This is
  the direct DUML equivalent of the two MSDK setters. **[HW]** Confirm WM160 honors `0x03/0x2D` vs the
  hashed `flying_limit.max_height` param (FLIGHT_GATING.md open item #4).

### 2c. Dark / no-GPS takeoff — the correct lever and polarity (LOCAL-DECOMPILE truth)
There is **no public MSDK toggle** for dark/no-GPS takeoff on the Mini; it is an internal
FlightController KeyValue key that the app surfaces as an "Unlock" button. From `DARK_NOGPS_TRUTH.md`
(DJI Fly v1.21.4 DEX, cited line-by-line there):
- The KeyValue key is **`DarkNoGpsLockEnable`** (Boolean, `UAVFlightControllerKey.J4`). This is the
  **KeyValue name, NOT the flyc param string** — hashing this literal for `0x03/0xF9` matches no param
  (silent no-op).
- The **flyc/g_config parameter string** (what `0x03/0xF9` must hash) is **`fc_dark_need_gps_0`**
  (`ParamCfgName` field `Y`).
- **Polarity: unlock = write FALSE (0). Lock = TRUE (1).** The app's Unlock action calls
  `getDarkNoGPSLockOn().m(Boolean.FALSE)` (`SelfCheckVM.h2()`), i.e. sets the flag to 0 to allow takeoff.
- FC pushes `MotorStartFailedCause = DARK_NEED_GPS = 147 (0x93)` when the lock is armed; HMS code `0x761f`.

**Our client is now correct:** `Drone.unlock_no_gps(True)` writes `fc_dark_need_gps_0 = 0`
(`drone.py:191-195`) — right param name, right polarity (0 = unlocked). The earlier bug (writing
`DarkNoGpsLockEnable = 1`) is fixed. After unlock the aircraft takes off in **ATTI and drifts** (Mini's
only positioning aid is the downward VPS, blind in the dark) — this is a soft flag, not a physics wall.

### 2d. Horizontal-speed / flight-mode
- MSDK exposes flight modes indirectly; on Mini the app changes a **set of control-gain params** rather
  than one command (`drone.py:221-226` raises NotImplementedError with this note).
- Our `set_horizontal_speed` writes `g_config.control.horiz_vel_atti_range_0` (float m/s) via hashed
  `0x03/0xF9` (`drone.py:186-189`) — **[HW]** exact param name/type unconfirmed.

---

## 3. ENABLE PRECONDITIONS — what must be true before sticks/takeoff are accepted

MSDK-documented preconditions for virtual stick (FLIGHT_GATING.md §H + DJIFlightController ref):
1. `setVirtualStickModeEnabled(true)` succeeded and `isVirtualStickControlModeAvailable == true`.
2. **No mission running** (no waypoint/hotpoint/follow-me). N/A on Mini (unsupported anyway).
3. Flight-orientation mode = AircraftHeading. Collision avoidance / tripod / terrain-follow disabled if
   supported — **N/A on Mini** (no obstacle sensors).
4. `setVirtualStickAdvancedModeEnabled` is **optional** (adds wind compensation), default NO — not required.
5. Mini RC has **no P/A/F mode switch**, so there is no "put RC in P-mode" step.

Raw-DUML preconditions to reproduce the above (FLIGHT_GATING.md §H "Recommended PC sequence"):
1. **Sender must be app `0x02`, never `0x0a`.** `0x0a` = DJI-Assistant address → FC raises
   `MotorStartFailedCause = 2 (AssistantProtected)` and locks motors. (Confirmed empirically for takeoff;
   `drone.py:26-28` sets `DEV_APP = 0x02`.) NOTE: `control.py:31` still hard-codes `DEV_APP = 0x0a` for
   its standalone `build_flight_frame` — **fix this** to `0x02` (the live path via `Drone._cmd` already
   uses `0x02`, so the keyboard loop is fine; the stray constant is a trap).
2. **Request control authority:** `0x49/0x80` payload `01` (`request_control`, `drone.py:125`).
3. **Ground-station / external-control ON:** `0x03/0x80` payload `01` (`set_ground_station_mode`,
   `drone.py:133`) — the closest DUML analogue of `setVirtualStickModeEnabled(true)`.
4. (optional/**[HW]**) FC gate `serial_api_cfg.advance_function_enable = 1` via hashed `0x03/0xF9`
   (FLIGHT_GATING.md §H "Possible FC gate") — unverified whether WM160 needs it before external sticks.
5. **Arm/takeoff:** virtual-stick throttle alone will **not** arm from the ground. Take off first with
   `0x03/0x2A` `AUTO_FLY = 0x01` (auto lift) or `START_MOTOR = 0x07` (arm only), then stream sticks.
   (This matches MSDK: you call `startTakeoff()` then feed stick data.)
6. If still ignored: `0x06/0xF1 01` RC→PC handover and/or `0x19/0x41 01` preempt right-of-control
   (`rc_to_pc_control` / `preempt_control`, `drone.py:137-140`).
7. Release when done: `0x49/0x80 00`.

**Most likely reason some of our commands are ignored today:** missing the control-authority +
ground-station preconditions (steps 2-3), sending sticks before takeoff/arm (step 5), and/or the
`0x03/0x8E` `flag` byte not selecting a valid mode (§1f). The `0x0a`-sender lockout (step 1) is already
handled on the live path.

---

## 4. Does WM160 / Mavic Mini 1 support this over MSDK, or only DJI Fly?

- **Virtual stick: YES over MSDK v4** — added for Mavic Mini in **MSDK 4.13** (fw 01.00.0500)
  (FLIGHT_GATING.md §H; DJI release notes). The DJI Fly app itself carries `isSupportVirtualJoyStick`
  (`isSupport_keys.txt:222`), i.e. the WM160 FC speaks a joystick channel.
- **Height/radius limits, novice mode, dark/no-GPS unlock: YES**, but dark/no-GPS is exposed only as the
  app's "Unlock" button (§2c), not a public MSDK API — we drive it directly via the `fc_dark_need_gps_0`
  param write.
- **NOT supported on Mini 1 (don't bother):** Waypoint, ActiveTrack, obstacle avoidance, POI/Orbit,
  Follow-Me (no tracking/vision sensors) — MSDK release notes + FLIGHT_GATING.md §G.
- We bypass MSDK entirely (raw DUML over AOA), so the levers we use are the DUML commands above, not the
  MSDK method calls — but the MSDK sequence is the specification we are reproducing.

---

## 5. Concrete "do this" for the client

Virtual-stick flight (implement, then confirm variant on HW):
```
request_control()                    # 0x49/0x80 01
set_ground_station_mode(True)        # 0x03/0x80 01   (== setVirtualStickModeEnabled(true))
takeoff()                            # 0x03/0x2A 01  (arm+lift; sticks alone won't arm)
loop @ 25 Hz:                        # 40 ms period, never < 5 Hz
    set_sticks_float(roll*5, pitch*5, yaw*60, thr*2, flag=?)   # 0x03/0x8E, physical units [HW flag]
    # fallback if 0x8E ignored: set_sticks(roll,pitch,yaw,thr) # 0x01/0x0A special-TLV RC emulation
release_control()                    # 0x49/0x80 00
```
Modes to emulate in the `0x8E` flag: rollPitch = Velocity (±15 m/s), yaw = AngularVelocity (±100 deg/s),
vertical = Velocity (±4 m/s), coordinate = **Body**.

Unlock no-GPS/dark (already correct in `drone.py`):
```
unlock_no_gps(True)                  # 0x03/0xF9 hashed  fc_dark_need_gps_0 = 0  (0 = UNLOCK)
```
Then takeoff — aircraft lifts in ATTI and drifts. Optionally `setNoviceModeEnabled(false)` equivalent and
`set_max_altitude/set_max_distance` (0x03/0x2D) to widen the envelope.

Open HW items: (1) which stick encoding WM160 honors (0x03/0x8E vs 0x01/0x0A) and the `0x8E` flag byte;
(2) whether `serial_api_cfg.advance_function_enable=1` is a required gate; (3) confirm pitch/roll not
swapped and yaw sign; (4) confirm `0x03/0x2D` limits honored vs hashed `flying_limit.max_height`.
