# Virtual-Stick Research 2026 — why WM160 ignores our sticks, and the exact fix

Fresh, from-scratch pass. Sources used:
1. **Stripped native** `libsdk_jni.so` / `libsdk_key_value.so` (MSDK **v5** "uav::sdk" era) — file offset == vaddr in the first LOAD segment; ADRP+ADD string-xref scan + capstone at file offsets.
2. **Unpacked app DEX** (`reverse_docs/unpacked_app_dex/*.dex`) — the real DJI Fly / flymodel app (MSDK v5).
3. **NEW ground truth:** `scratchpad/msdk/provided.jar` = **dji-sdk-provided-4.18.jar** (MSDK **v4.18**, the SDK generation that *officially added Mavic Mini support*). Un-obfuscated DUML models under `dji/midware/data/model/P3/Data*.class`. `javap -p -c`. Strings are stringfog'd but **all numbers are plain**.

Do not trust the older docs (VIRTUAL_STICK_NATIVE.md etc.); everything below is re-derived.

---

## 0. TL;DR — the three competing wire formats, and which WM160 wants

There are **THREE** distinct joystick wire formats in the DJI stack. Our code currently sends #2. The likely-correct one for WM160 is **#1**.

| # | Command | Payload | Source / SDK gen | Units |
|---|---------|---------|------------------|-------|
| **1** | **FLYC `0x03`/`0x8E`** `DataFlycJoystick` | **17 B**: `flag u8` + 4×`f32 LE` (roll,pitch,yaw,throttle) | **MSDK v4.18 (Mini-supporting)** | **physical** (m/s, deg, deg/s) — mode set by `flag` |
| 2 | SPECIAL `0x01`/`0x0A` `SPECIAL_TLV_CMD` | TLV: 8 B of 4×11-bit channels (1024±660→364..1684) + `u32 0x200` + `0x06`, then TLV `0x55`=`0x04`, opt. TLV `0x56`=time | MSDK v5 native `VirtualJoyStickHelper::AssemblePack` | RC channels |
| 3 | SPECIAL `0x01`/`0x02` `virtual_rc_joystick` | 16 B: 4×`int32 LE`, each ∈ [-1000,+1000] | MSDK v5 `VirtualJoyStickMsg` (mobile-RC) | RC counts |

**Reconciliation of #2 and #3:** they are the *same v5 path*. The Java `VirtualJoyStickMsg` carries 4×int32 ∈ ±1000; the native converts each via
`ConvertVirtualStickValueToRcStickValue(v) = (v/1000.0)*660.0 + 1024.0`
(CONFIRMED by disasm at file off `0x22da160`: `scvtf; fdiv /1000; fmul *660; fadd +1024`) → the 11-bit channel that `AssemblePack` packs into the `0x01/0x0A` TLV. So the modern DJI-Fly path is **VirtualJoyStickMsg(±1000) → 0x01/0x0A TLV**. **Our current `drone.py set_sticks` already implements #2 correctly** (11-bit, 1024±660, TLV tags 0x01/0x55) — yet it does nothing.

**Why #1 is the answer for WM160:** the v4.18 SDK is the one DJI shipped *to add Mavic Mini*, and its `FlightController.sendVirtualStickFlightControlData` emits **`0x03/0x8E` floats**, not the TLV. WM160 is a 2019 WiFi drone whose firmware predates the v5 unified airlink TLV path. Since our correct-looking #2 frame is ignored, the FLYC `0x03/0x8E` frame is the format the WM160 flight-controller firmware actually honors. (We already had a stub `set_sticks_float` at `0x03/0x8E` but with `flag=0` and normalized values — both wrong; fixed below.)

---

## 1. `DataFlycJoystick` — the `0x03/0x8E` frame (CONFIRMED, provided.jar)

`javap -p -c dji/midware/data/model/P3/DataFlycJoystick.class`:

**Header** (`start()`): sender `DeviceType.APP`, receiver `DeviceType.FLYC`, `CMDTYPE.REQUEST`, `NEEDACK.NO`, `EncryptType.NO`, `cmd_set = CmdSet.FLYC`, `cmd_id = CmdIdFlyc.JoyStick`.
- `CmdSet.FLYC` = **3** (0x03) — CONFIRMED (`CmdSet` clinit: FLYC ctor `iconst_3,iconst_3`).
- `CmdIdFlyc.JoyStick` = ordinal **79**, **value 142 = 0x8E** — CONFIRMED (`CmdIdFlyc$CmdIdType` clinit `bipush 79; sipush 142`).

**`doPack()` payload = exactly 17 bytes** (`bipush 17; newarray byte`):

| Offset | Field | Type |
|--------|-------|------|
| `[0]`     | `flag`     | `u8`      |
| `[1..4]`  | `roll`     | `f32 LE`  |
| `[5..8]`  | `pitch`    | `f32 LE`  |
| `[9..12]` | `yaw`      | `f32 LE`  |
| `[13..16]`| `throttle` | `f32 LE`  |

Endianness: floats via `dgh.fdd(float)` = `Float.floatToIntBits` → `gfd(int)`, and `gfd` writes byte0=`v&0xFF`, byte1=`(v>>8)&0xFF`, … = **little-endian** (CONFIRMED). So `struct.pack("<ffff", roll, pitch, yaw, throttle)` after the flag byte is byte-exact.

---

## 2. The `flag` byte (THE missing piece — CONFIRMED)

Built by `FlightControllerAbstraction.fdd(VerticalControlMode, RollPitchControlMode, YawControlMode, FlightCoordinateSystem, boolean)` — bytecode:

```
flag = (RollPitchControlMode.value << 6)
     + (VerticalControlMode.value  << 4)
     + (YawControlMode.value       << 3)
     + (FlightCoordinateSystem.value << 1)
     + (advancedModeEnabled ? 1 : 0)
```

Bit layout of the single flag byte:

| Bit | Field | 0 | 1 |
|-----|-------|---|---|
| 0 | advanced mode (`setVirtualStickAdvancedModeEnabled`) | off | on |
| 1 | `FlightCoordinateSystem` | GROUND | BODY |
| 2 | (unused) | — | — |
| 3 | `YawControlMode` | ANGLE (deg, abs heading) | ANGULAR_VELOCITY (deg/s) |
| 4 | `VerticalControlMode` | VELOCITY (m/s) | POSITION (m) |
| 5 | (unused) | — | — |
| 6 | `RollPitchControlMode` | ANGLE (deg) | VELOCITY (m/s) |
| 7 | (unused) | — | — |

Enum `value()`s (CONFIRMED from clinit — value == 3rd ctor arg):
`RollPitchControlMode` ANGLE=0, VELOCITY=1 · `YawControlMode` ANGLE=0, ANGULAR_VELOCITY=1 · `VerticalControlMode` VELOCITY=0, POSITION=1 · `FlightCoordinateSystem` GROUND=0, BODY=1.

**Requested concrete flag values:**
- RollPitch=VELOCITY, Yaw=ANGULAR_VELOCITY, Vertical=VELOCITY, Coord=GROUND:
  `(1<<6)|(0<<4)|(1<<3)|(0<<1)|adv` = **0x48** (adv off) / **0x49** (adv on).
- RollPitch=VELOCITY, Yaw=ANGLE, Vertical=POSITION, Coord=BODY:
  `(1<<6)|(1<<4)|(0<<3)|(1<<1)|adv` = **0x52** (adv off) / **0x53** (adv on).

**`flag = 0x00` is a trap:** it means roll/pitch = ANGLE in *degrees*, yaw = ANGLE (absolute heading in degrees), vertical = velocity. Feeding normalized `±1` there = ±1° tilt ⇒ effectively no motion. Our old `set_sticks_float(flag=0)` would have looked dead even if the frame were right.

**Ranges** (`Limits.class` constant pool, CONFIRMED): vertical velocity **[-4 .. +5] m/s**; vertical height [0..500] m; roll/pitch velocity **±15 m/s**; roll/pitch angle **±30°**; yaw angle **±180°**; yaw angular-velocity ~±100°/s (not distinctly in pool; ±30/±180 are reused — use a conservative ±90–100). Send physical values inside these.

### Full send sequence in `sendVirtualStickFlightControlData` (v4.18)
Validate each axis against its mode's range (`COMMON_PARAM_ILLEGAL` on violation) → `DataFlycJoystick.getInstance().setFlag(build).setYaw(y).setPitch(p).setRoll(r).setThrottle(t).start()` → `EventBus.post(...)` (flight-record only). No per-tick config command; **the mode lives entirely in the flag byte of the same 17-byte frame** (answers the "separate vs inline" question: **inline**).

---

## 3. Enable / authority / preconditions

- `FlightController.setVirtualStickModeEnabled(boolean, cb)` is **abstract**; `setVirtualStickAdvancedModeEnabled(boolean)` just stores the `virtualStickAdvancedModeEnabled` field that becomes flag bit0. In the v4.18 app there is **no dedicated DUML "enable virtual stick" frame** — enabling is a client-side gate that (a) starts a periodic re-sender and (b) begins allowing `0x03/0x8E` frames. The FC decides acceptance from its own state + who holds control.
- **v5 gate (`IsSupportVirtualJoyStick`)** — native `IsSupportVirtualJoyStickHelper::AbsDidSetup` (disasm at `0x2bf2e40`): `support = (DeviceType==1) && (WiFiMode==2)`, i.e. **DeviceType.AIRCRAFT(1) && WiFiMode.OPERATION_CONTROL(2)** (enum ints confirmed by DEX agent). So on the v5 path the WiFi link must be in *operation-control* mode, not media-transfer, before sticks are honored. Relevant if we ever use format #2/#3.
- **Control authority:** our `request_control` sends `0x49/0x80` (`uav_sdk_get_or_release_control_auth`, template `<1,73,128>` CONFIRMED in native). Keep it. Ground-station `0x03/0x80` (`uav_fc_set_ground_station_on_off`, `<1,3,128>` CONFIRMED) is fine to keep but is not the joystick enable.
- **Airborne first:** the FC will not translate sticks into motion on the ground. Take off (our working `0x03/0x2A` `AUTO_FLY`) → wait until airborne/hover → *then* stream `0x03/0x8E`.

---

## 4. On-hardware verification (CONFIRMED enum values)

Watch these two OSD-push fields (both in `DataOsdGetPushCommon`, the telemetry we already decode):

1. **`FLYC_STATE`** — enum member **`Joystick = 17`** (CONFIRMED: clinit `bipush 17; ... putstatic Joystick`). **When the FC accepts our sticks, FLYC_STATE flips to Joystick(17).** This is the single decisive signal. (Full enum order: Manula, Atti, Atti_CL, Atti_Hover, Hover, GPS_Blake, GPS_Atti, GPS_CL, GPS_HomeLock, GPS_HotPoint, AssitedTakeoff(10), AutoTakeoff(11), AutoLanding(12), AttiLangding(13), NaviGo(14), GoHome(15), ClickGo(16), **Joystick(17)**, Cinematic(18), Atti_Limited(19), …)
2. **`SDKCtrlDevice`** (`getSDKCtrlDevice()` reads OSD-common **byte offset 52**, then `SDKCtrlDevice.find(i)`): **RC=0, APP=1, ONBOARD_DEVICE=2, CAMERA=3, OTHER=4** (CONFIRMED). Must read **APP(1)** once we hold control; if it stays **RC(0)**, the FC never handed control to us and any joystick format will be ignored.

Procedure: arm → takeoff → confirm airborne → `request_control` → start streaming `0x03/0x8E` at 20 Hz with a live pitch value → watch telemetry: `SDKCtrlDevice==APP` **and** `FLYC_STATE==Joystick`. If both flip but the drone doesn't move, the values/units are wrong; if they don't flip, it's authority/precondition.

---

## 5. WHAT TO CHANGE IN OUR CODE

`control.py`/`drone.py` currently stream format **#2** (`set_sticks` → `0x01/0x0A`) which the WM160 ignores. Switch the primary path to **#1** (`0x03/0x8E` floats + correct flag).

**`drone.py` — DONE in this pass.** `set_sticks_float` now defaults `flag=0x48` (rollpitch VELOCITY, yaw ANGULAR_VELOCITY, vertical VELOCITY, GROUND, advanced off) and there is a new helper:

```python
@staticmethod
def build_stick_flag(rollpitch_velocity=True, yaw_rate=True,
                     vertical_velocity=True, body_frame=False, advanced=False) -> int:
    rp = 1 if rollpitch_velocity else 0
    vt = 0 if vertical_velocity else 1
    yw = 1 if yaw_rate else 0
    co = 1 if body_frame else 0
    return ((rp<<6)|(vt<<4)|(yw<<3)|(co<<1)|(1 if advanced else 0)) & 0xFF

def set_sticks_float(self, roll, pitch, yaw, throttle, flag=0x48):
    self._cmd(0x03, 0x8E, bytes([flag]) + struct.pack("<ffff", roll, pitch, yaw, throttle),
              receiver=DEV_FC)

def set_sticks_velocity(self, roll, pitch, yaw, throttle,
                        h_mps=5.0, v_mps=2.0, yaw_dps=90.0):
    c = lambda v: max(-1.0, min(1.0, v))
    self.set_sticks_float(c(roll)*h_mps, c(pitch)*h_mps, c(yaw)*yaw_dps, c(throttle)*v_mps,
                          flag=self.build_stick_flag())
```

**`pc_client.py` `_send_loop` — CHANGE NEEDED (not yet applied):** route the primary path to `set_sticks_velocity` (normalized axes → physical units → `0x03/0x8E`), keep `0x01/0x0A` and `0x01/0x02` as fallbacks behind the existing toggle:

```python
if self.stick_mobilerc:
    self.d.set_sticks_mobilerc(a["roll"], a["pitch"], a["yaw"], a["throttle"])   # 0x01/0x02 fallback
else:
    self.d.set_sticks_velocity(a["roll"], a["pitch"], a["yaw"], a["throttle"])   # 0x03/0x8E PRIMARY
```

Keep the 20 Hz cadence. Because the frame is `NEEDACK.NO`, there is a firmware failsafe if frames stop — keep sending zero-sticks while idle (do not stop the loop), which the loop already does.

**Axis sign/units to sanity-check on bench:** pitch>0 = forward (m/s), roll>0 = right (m/s), yaw>0 = clockwise (deg/s), throttle>0 = up (m/s). Payload field order is roll,pitch,yaw,throttle regardless of the setter call order.

---

## 6. Confidence-ranked root-cause and first fix

1. **(HIGH) Wrong wire format** — we send the v5 TLV `0x01/0x0A`; WM160's Mini-era firmware honors the v4.18 FLYC float frame `0x03/0x8E`. **First fix: stream `0x03/0x8E` (flag 0x48 + 4 floats, physical units).**
2. **(MED) `flag=0`/normalized-value trap** — even our `0x03/0x8E` stub would have been inert (angle mode, ±1° / ±1 m/s of nothing). Fixed by `flag=0x48` + `set_sticks_velocity` scaling.
3. **(MED) Not airborne / no control authority** — verify `SDKCtrlDevice→APP(1)` and take off before streaming.
4. **(LOW) v5-path gate** — only relevant if we fall back to `0x01/0x0A`: needs WiFiMode OPERATION_CONTROL(2).

**Single most likely fix to try first:** take off, hold hover, `request_control`, then stream **`0x03/0x8E`** at 20 Hz with `flag=0x48` and values scaled to m/s / deg/s (e.g. pitch 3 m/s). Success = **FLYC_STATE flips to Joystick(17)** and the aircraft translates.

---

## 7. Web cross-check (independent, corroborates §1–6)

Independent web/GitHub pass (dji-firmware-tools, DJI docs, RosettaDrone) — **agrees on every load-bearing point**:

- **`FLYC_STATE 0x11 = Joystick`** — CONFIRMED again from `dji-firmware-tools` `FLYC_OSD_GENERAL_FLYC_STATE_ENUM`. Same value 17 we pulled from the jar. This is the go/no-go telemetry signal.
- **WM160 is MSDK v4-only** (Mini support added in v4.13; MSDK v5 excludes Mini 1). This *confirms* our core call: the **v4.18 `0x03/0x8E` float frame is the correct one**, not the v5 TLV. **RosettaDrone flies the Mini** with exactly our recommended setup: `RollPitch=VELOCITY, Yaw=ANGULAR_VELOCITY, Vertical=VELOCITY, coordinate=BODY/GROUND`, streamed at **`MOTION_PERIOD_MS=50` = 20 Hz** — i.e. our `flag=0x48` at 20 Hz, independently validated.
- **Send rate 5–25 Hz + watchdog:** DJI v4 docs state that if stick frames stop / are too slow, *"the aircraft may regard the connection as broken … which will cause the aircraft to hover in place."* So our 20 Hz is correct, and we must keep streaming zero-sticks while idle (loop already does). The drone **hovers**, does not fall, on stall.
- **cmd-id naming (dji-firmware-tools FLYC table):** `0x29 = "Joystick"` (low-level FC joystick) and **`0x8E = "App Joystick Data"`** — the latter is exactly what our `DataFlycJoystick` targets (jar → `CmdIdFlyc.JoyStick = 142 = 0x8E`). Cross-confirmed. `0x29` is a *distinct, lower-level* joystick id worth trying as fallback #2 if `0x8E` is ignored.
- **SPECIAL (cmd_set 0x01) table** from dji-firmware-tools: **`0x0A is NOT a joystick` — it is undefined in the SPECIAL table.** The real app-stick commands are `0x01/0x01` "Old Special App Control" and `0x01/0x03` "New Special App Control" (24-byte). Control-authority = `0x01/0x00` "Sdk Ctrl Mode Open/Close Nav"; arm/disarm = `0x01/0x05`. ⇒ Our old `0x01/0x0A` guess and the `0x49/0x80` authority are **community-unverified**; the FC-honored authority primitive is likely `0x01/0x00`. (Our `0x49/0x80` came from the native `uav_sdk_get_or_release_control_auth` template, which *is* real in the v5 SDK — but may not be what WM160 firmware expects.)
- **FlightControlData constructor gotcha:** DJI's public ctor is `FlightControlData(pitch, roll, yaw, verticalThrottle)` — **arg order ≠ wire order.** The wire (from our `doPack` reverse) is `roll@[1..4], pitch@[5..8]`. Our python packs `struct.pack("<ffff", roll, pitch, yaw, throttle)` = roll-first = **wire-correct**; do not "fix" it to match the ctor.
- **Transport caveat (their read):** the web sources say MSDK v4 virtual stick normally runs on an Android host through the RC link, and note *no public project flies the Mini over pure DUML*. But our AOA+DUML link already delivers takeoff/land to the FLYC — so emitting the same `0x03/0x8E` FLYC frame over our existing transport is the direct test. If `0x8E` is ignored, escalate to fallbacks in this order: `0x03/0x29` (FLYC "Joystick") → `0x01/0x03` (New Special App Control, 24-byte) with `0x01/0x00` authority + `0x01/0x05` arm.
- **Gap:** exact byte layout of FLYC `0x29` and the SPECIAL `0x01/0x03` (24-byte) payloads is not published in dji-firmware-tools' quick tables; would need a local grep of `flyc.lua`. `0x8E` we have in full from the jar, so this gap only matters for fallbacks.

Key web refs: RosettaDrone `DroneModel.java` (github.com/RosettaDrone/rosettadrone); DJI v4 `VirtualStickView.java` + FlightController API; dji-firmware-tools `dji-dumlv1-flyc.lua` / `-proto.lua`; DJI Onboard-SDK Virtual RC (364..1684 channel model, 1 s watchdog).

---

## Appendix — key offsets/VAs (independent citations)

- `IsSupportVirtualJoyStickHelper::AbsDidSetup` gate: `libsdk_jni.so` file off **`0x2bf2e40`** → `support = (arg1==1)&&(arg2==2)`.
- `ConvertVirtualStickValueToRcStickValue`: off **`0x22da160`** = `(v/1000)*660+1024`.
- `VirtualJoyStickHelper::AssemblePack` (11-bit TLV pack): off **`0x22da6f0`**; bitfields via `bfi` at shifts 8,19,30,41 + bit62; flags `0x200`; TLV tags `0x01`(len13)/`0x55`(len1,val`0x04`)/opt `0x56`(time).
- Native cmd templates (`uav_cmd_base_req<A,cmd_set,cmd_id>`): `<1,1,2>`=`virtual_rc_joystick`, `<1,1,10>`=`SPECIAL_TLV_CMD`, `<1,3,128>`=`ground_station_on_off`, `<1,73,128>`=`get_or_release_control_auth`, `<1,3,42>`=`fc_function_control`.
- v4.18 jar: `DataFlycJoystick` (17 B, flag+4×f32 LE), `CmdIdFlyc.JoyStick=142`, `CmdSet.FLYC=3`, flag builder `FlightControllerAbstraction.fdd(V,RP,Y,C,bool)`, `FLYC_STATE.Joystick=17`, `SDKCtrlDevice{RC=0,APP=1,ONBOARD=2,CAMERA=3,OTHER=4}@osd_off52`.

---

# ============================================================
# 2026-07-18 CORRECTED PASS — MSDK v4.18 bytecode ground truth
# (supersedes §2/§3 above where they conflict; all claims cite class + bytecode offset)
# ============================================================

## A. FLAG BYTE — CORRECTED (but our old formula was already RIGHT)

Builder is `FlightControllerAbstraction.fdd(VerticalControlMode, RollPitchControlMode, YawControlMode, FlightCoordinateSystem, boolean)` — a `private byte`. Exact bytecode (FlightControllerAbstraction.class, method `fdd(V,RP,Y,C,Z)B`, offsets 2–40):

```
flag = (byte)(RollPitchControlMode.value() << 6)
     + (byte)(VerticalControlMode.value()  << 4)
     + (byte)(YawControlMode.value()       << 3)
     + (byte)(FlightCoordinateSystem.value() << 1)
     + (byte)(advanced ? 1 : 0)
```

Enum `value()` (each enum clinit, 3rd ctor int = wire value; `value()` returns `_value`/`data` field — CONFIRMED):
- RollPitchControlMode: **ANGLE=0, VELOCITY=1**
- VerticalControlMode:  **VELOCITY=0, POSITION=1**
- YawControlMode:       **ANGLE=0, ANGULAR_VELOCITY=1**
- FlightCoordinateSystem: **GROUND=0, BODY=1**

Bit map (single byte): `[7]=0 [6]=RP(0=ang,1=vel) [5]=0 [4]=Vert(0=vel,1=pos) [3]=Yaw(0=ang,1=rate) [2]=0 [1]=Coord(0=grnd,1=body) [0]=advanced`

**HOVER flag = `0x48`** (RP=VELOCITY, Vert=VELOCITY, Yaw=ANGULAR_VELOCITY, Coord=GROUND, adv=0):
`(1<<6)|(0<<4)|(1<<3)|(0<<1)|0 = 0x40|0x08 = 0x48`. **This is CORRECT — our prior 0x48 was right.**

### Independent triple-confirmation of 0x48
1. MSDK v4.18 bytecode (above).
2. **DJI Onboard-SDK `dji_control.hpp`** flag constants (developer.dji.com/onboard-api-reference/dji__control_8hpp_source.html): `HORIZONTAL_VELOCITY=0x40`, `VERTICAL_VELOCITY=0x00`, `YAW_RATE=0x08`, `HORIZONTAL_GROUND=0x00`, `STABLE_DISABLE=0x00` → OR = **0x48**. Bit layout is bit-for-bit identical to the MSDK builder (bits 7:6 horiz, 5:4 vert, 3 yaw, 1 frame, 0 stable).
3. Prior native reverse ("velocity setup 0x48").

### WHY 0x48 + all-zeros should HOVER (and what "climb" really means)
- In **VerticalControlMode.VELOCITY** the vertical value is a **velocity in m/s, +up**, and **0.0 = hold current altitude** (OSDK: *"upward is positive"*; 0 = zero vertical velocity). MSDK range check for VELOCITY is **[-4 .. +5] m/s** (bytecode `float -4.0f`/`float 5.0f`); POSITION range is **[0 .. 500] m**.
- OSDK's **STABLE bit (bit 0)** only affects the **horizontal** axis (brake-to-hover / wind hold on zero horizontal input). It does **NOT** govern vertical. So enabling/disabling it cannot cause a vertical climb. (Source: OSDK Control class ref.)
- Therefore, per DJI's own code, **`0x48` + four zero floats = perfect hover**. A drone that CLIMBS on all-zeros is **not actually holding vertical velocity** — i.e. it is NOT in pure joystick vertical-velocity mode at that instant. Prime suspects, in order:
  1. **FC not in JOYSTICK flight state.** Decisive telemetry: OSD `FLYC_STATE` must read **Joystick(17)** while streaming. If it reads a GPS/nav/takeoff state, the FC is running its own vertical loop (e.g. finishing auto-takeoff climb to target height, or ground-station nav) and treats stick-vertical=0 as "no override." **DIAGNOSTIC: log FLYC_STATE during the climb.**
  2. **Residual auto-takeoff.** WM160 auto-takeoff climbs to a target height; if joystick frames start before takeoff completes, the climb is the takeoff, not the sticks.
  3. See §B (field-order swap) — does not explain an all-zero climb by itself, but is a real bug for any non-zero vertical/yaw command and must be fixed regardless.

  NOTE: zeros are zeros, so the §B swap cannot by itself turn an all-zero frame into a climb. If the climb truly happens on genuinely-all-zero frames, it is (1) or (2). Verify FLYC_STATE first.

## B. FIELD-ORDER SWAP — WM160-SPECIFIC (NEW, safety-critical for non-zero commands)

Before packing, the send routine passes the FlightControlData through
`FlightControllerAbstraction.fdd(FlightControlData) : FlightControlData` (bytecode offsets 2–98). For
`getFlycVersion() >= 16 && DroneType.value() >= wm220.value() && DroneType.value() != PM820PRO.value()`
it **SWAPS yaw and verticalThrottle**:
```
out.yaw              = in.verticalThrottle
out.verticalThrottle = in.yaw
```
Then `setYaw(out.yaw)` → wire[9..12], `setThrottle(out.verticalThrottle)` → wire[13..16].

DroneType `_value` (from `DataOsdGetPushCommon$DroneType` clinit, 2nd ctor int): **wm220=16, PM820PRO=23, WM160=53**. So `53 >= 16 && 53 != 23` ⇒ **the swap APPLIES to WM160** (provided runtime flycVersion >= 16, which for a 2019 WM160 is essentially certain).

**Net effect: the WM160 firmware wire order is `roll, pitch, THROTTLE(vertical), yaw`** — i.e. after the swap, wire[9..12] carries the user's vertical/throttle and wire[13..16] carries the user's yaw. Our python currently packs `struct.pack("<ffff", roll, pitch, yaw, throttle)` (yaw 3rd, throttle 4th) = **WRONG for WM160**. Correct packing for WM160:

```python
struct.pack("<ffff", roll, pitch, throttle, yaw)   # throttle in slot 3 (wire[9..12]), yaw in slot 4 (wire[13..16])
```

Empirical check: command a pure positive throttle. If the drone **yaws** instead of climbing (and pure yaw makes it climb/descend), the swap is active → use the packing above. This is a genuine bug that will make climb-commands yaw and yaw-commands climb; it must be fixed even though it does not explain the all-zero climb.

## C. NEUTRAL / HOVER VALUES (CONFIRMED)
- roll = pitch = yaw = verticalThrottle = **0.0f** (little-endian IEEE754; `dji/midware/util/dgh.fdd(F)` → LE, CONFIRMED). Hover = 16 zero bytes after the flag.
- verticalThrottle **0.0 in VELOCITY mode = hold altitude** (not a climb). Positive = up, m/s, clamp [-4, +5].
- If you ever want an explicit altitude hold instead, use **POSITION** vertical mode (flag bit4=1, e.g. 0x58) and send the **current/target height in metres** [0..500]; 0.0 there = "go to 0 m" = descend, so do NOT use POSITION mode with 0.

## D. ENABLE / AUTHORITY / RELEASE — CORRECTED (answers the "release doesn't return to RC" bug)

### D1. SDKCtrlDevice telemetry — VERIFIED against jar (trust our parser)
`DataOsdGetPushCommon.getSDKCtrlDevice()` reads **u8 at OSD-common payload offset 52 (0x34)** (`get(52, 1, Integer)`), then `SDKCtrlDevice.find(i)`. Enum values (clinit): **RC=0, APP=1, ONBOARD_DEVICE=2, CAMERA=3, OTHER=4**. Our parser (u8 @ 0x34 of the 0x03/0x43 OSD push; 0=RC/1=APP/2=ONBOARD/3=CAMERA) is **CONFIRMED correct**.

### D2. The real ENABLE/RELEASE switch is FLYC NavigationSwitch (ground-station open/close)
`FlightControllerAbstraction.ssf(boolean enabled, cb)` (bytecode): 
```
GS_COMMAND cmd = enabled ? OPEN_GROUND_STATION : CLOSE_GROUND_STATION;
DataFlycNavigationSwitch.getInstance().setCommand(cmd).start(...);
```
`DataFlycNavigationSwitch` header: **cmd_set = CmdSet.FLYC = 0x03**, **cmd_id = CmdIdFlyc.NavigationSwitch = 128 = 0x80**, payload = **1 byte = GS_COMMAND.value()**.
`GS_COMMAND` clinit values: **OPEN_GROUND_STATION = 1, CLOSE_GROUND_STATION = 2**, OTHER = 100.

⇒ **OBTAIN app/nav control:  `cmd_set 0x03, cmd_id 0x80, payload [0x01]`  (OPEN_GROUND_STATION)** — this is what we already send as "ground station on", and it is what flips **SDKCtrlDevice → APP**.
⇒ **RELEASE back to RC:      `cmd_set 0x03, cmd_id 0x80, payload [0x02]`  (CLOSE_GROUND_STATION)** — **payload 2, NOT 0 and NOT 1.**

**This is the fix for the stuck-on-APP bug:** our release sends `0x49/0x80 [0x00]` (and/or a wrong ground-station byte). The FLYC ground-station-mode close needs payload **0x02**. Send `0x03/0x80 [0x02]`, then confirm `SDKCtrlDevice` flips back to **RC(0)**.

### D3. About 0x49/0x80 (the community/native control-auth)
In the **v4.18 jar** cmd_set 0x49 (73) = `CmdSet.SDKAgent`, whose CmdIds are file/MOP transfer (14,15,48,49,50) — there is **no 0x49/0x80 control-auth in v4.18 MSDK**. The `0x49/0x80` obtain/release came from the **v5 native** (`uav_sdk_get_or_release_control_auth`, `<1,73,128>`) and is an OSDK/DUML-firmware convention. HW shows `0x49/0x80 [1]` participates in getting APP; keep it as an obtain step, but the **authoritative MSDK-v4.18 enable/disable for WM160 is the FLYC NavigationSwitch `0x03/0x80` with [1]=open / [2]=close.** Recommended sequences:

```
OBTAIN:  0x03/0x80 [0x01]  (OPEN_GROUND_STATION)   [+ optional 0x49/0x80 [0x01]]  → verify SDKCtrlDevice==APP(1)
RELEASE: stop joystick stream → 0x03/0x80 [0x02]  (CLOSE_GROUND_STATION)  [+ optional 0x49/0x80 [0x00]] → verify SDKCtrlDevice==RC(0)
```
No arming DUML needed for this (motors/takeoff already handled by our working 0x03/0x2A).

## E. VIDEO NOTES — youtu.be/0LzVEOzoVxg
Title: **"Mavic Mini with DJI SDK 4.13 & Virtual Sticks Coordinate System Overview"**, channel **Dennis Baldwin** (dbaldwin / droneblocks), Aug 2 2020, iOS Mobile-SDK 4.13 tutorial. **Timed captions were NOT retrievable** (WebFetch + jina mirror returned only the page shell). Recoverable content (description/chapters): it is a **virtual-stick coordinate-system tutorial** — ground vs body/aircraft frame (chapter ~5:19), roll + throttle/vertical control, enabling/disabling virtual sticks (~8:17); notes SDK 4.13 has limited Mini support (waypoints missing). It does NOT document the climb bug, exact send rate, or arm order. Treat as corroboration that Mini virtual-stick uses the MSDK `FlightControlData` + ground/body coordinate flag (our §A), nothing more. URL: https://www.youtube.com/watch?v=0LzVEOzoVxg

## F. CITATIONS (this pass)
- Flag builder: `dji/sdksharedlib/hardware/abstractions/flightcontroller/FlightControllerAbstraction.class` method `fdd(V,RP,Y,C,Z)B` off 2–40; send routine `fdd(cb,FlightControlData,V,RP,Y,C,Z)V` off 292–352; swap `fdd(FlightControlData)FlightControlData` off 2–98; enable/disable `ssf(Z,cb)V`.
- Enums: `dji/common/flightcontroller/virtualstick/{VerticalControlMode,RollPitchControlMode,YawControlMode,FlightCoordinateSystem}.class` clinit.
- Pack: `dji/midware/data/model/P3/DataFlycJoystick.class` doPack (17 B: flag@0, roll@1, pitch@5, yaw@9, throttle@13, LE); CmdSet.FLYC=3, CmdIdFlyc.JoyStick=142.
- DroneType: `dji/midware/data/model/P3/DataOsdGetPushCommon$DroneType.class` (wm220=16, PM820PRO=23, WM160=53).
- Authority/release: `DataFlycNavigationSwitch.class` (FLYC/0x80, GS OPEN=1/CLOSE=2); `CmdIdFlyc$CmdIdType` NavigationSwitch=128; `CmdSet` OnboardSDK=25, SDKAgent=73; `CmdIdSDKAgent$CmdIdType` (no 0x80).
- SDKCtrlDevice: `DataOsdGetPushCommon.getSDKCtrlDevice()` off 52; enum RC=0/APP=1/ONBOARD=2/CAMERA=3/OTHER=4.
- OSDK: developer.dji.com/onboard-api-reference/dji__control_8hpp_source.html ; classDJI_1_1OSDK_1_1Control.html . Video: youtube.com/watch?v=0LzVEOzoVxg .
