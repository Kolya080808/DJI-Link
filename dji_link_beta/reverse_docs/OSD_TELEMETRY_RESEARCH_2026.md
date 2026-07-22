# OSD Telemetry Research 2026 — WM160 (Mavic Mini 1)

Multi-source re-derivation of the OSD-common push offsets, motivated by a live-hardware
bug: **flight-mode parses ("GPS_Atti") but satellites, gps_level, latitude, longitude and
altitude all read empty/None at the same time.**

Sources are tagged:
- **[app]** — extracted smali at `/tmp/all/uav/midware/data/model/P3/DataOsdGetPushCommon.smali`
  (DJI Fly, non-obfuscated `uav.midware.*` = repackaged `dji.midware.*`).
- **[msdk]** — `dji-sdk-provided-4.18.jar` (Maven Central `dji-sdk-provided/4.18`),
  `dji.midware.data.model.P3.DataOsdGetPushCommon` via `javap -p -c`.
- **[dji-firmware-tools]** — `comm_og_service_tool` / `flyc_osd_general` field table.

---

## 1. How `DataBase.get(offset, len, class)` works  [app]

`DataOsdGetPushCommon` extends `UAVOsdDataBase` -> `DataBase`. Every getter calls:

```
invoke-virtual {p0, OFFSET, LEN, CLASS} DataBase->get(IILjava/lang/Class;)Ljava/lang/Number;
```

`DataBase.get(off,len,class)` (smali lines 2767-2958) reads **directly from `_recData`**
at the raw byte `off` via `BytesUtil` (`.O`=int/LE, `.Y`=short/LE, `.H`=double/LE,
`.v`=byte). **There is NO base-offset / no header skip added inside `get()`** — the
offset in each getter is an absolute index into `_recData`.

=> **`_recData` must be the DUML *payload* (the bytes after the 13-byte DUML header and
before the 2-byte CRC), starting at the first data byte.** So getter offset 0x00 ==
payload byte 0. This is the same convention telemetry.py `_parse_osd(p)` uses with
`p = pkt.payload`. Good — provided `pkt.payload` is sliced identically (see §6).

<!-- PROGRESS: 20% -->

## 2. Offset table from [app] getters (DataOsdGetPushCommon.smali)

| Field | Getter | Offset | Len | Class/type | Notes |
|-------|--------|--------|-----|------------|-------|
| longitude | getLongitude | 0x00 | 8 | Double (rad->deg) | `get(0x00,8,Double)` |
| latitude  | getLatitude  | 0x08 | 8 | Double (rad->deg) | `get(0x08,8,Double)` |
| height (rel/baro) | getHeight | 0x10 | 2 | Short | x0.1 m |
| xSpeed | getXSpeed | 0x12 | 2 | Short | x0.1 |
| ySpeed | getYSpeed | 0x14 | 2 | Short | x0.1 |
| zSpeed | getZSpeed | 0x16 | 2 | Short | x0.1 (climb) |
| pitch | getPitch | 0x18 | 2 | Short | x0.1 |
| roll  | getRoll  | 0x1a | 2 | Short | x0.1 |
| yaw   | getYaw   | 0x1c | 2 | Short | x0.1 |
| flycState / mode | getFlycState | 0x1e | 1 | Short | `& 0xFF7F` then find(); bit7 = rcState |
| appCommand | getAppCommand | 0x1f | 1 | Short | |
| **status dword** | (many bit getters) | **0x20** | 4 | Integer | see §3 |
| gpsNum / satellites | getGpsNum | 0x24 | 1 | Short | 1 byte (Short is boxing) |
| flightAction | getFlightAction | 0x25 | 1 | Short | |
| motorFailedCause | getMotorFailedCause | 0x26 | 1 | Short | `& 0x7F` (ver>=0x1a raw) |
| nonGpsCause / waypointLimit | getNonGpsCause | 0x27 | 1 | Integer | low nibble |
| swaveHeight (VPS) | getSwaveHeight | 0x29 | 1 | Short | x0.1 m |
| flyTime | getFlyTime | 0x2a | 2 | Integer | deciseconds |
| motorRevolution | getMotorRevolution | 0x2c | 1 | Short | |
| flycVersion | getFlycVersion | 0x2f | 1 | Integer | |
| droneType | getDroneType | 0x30 | 1 | Integer | |
| imuInitFailReason | getIMUinitFailReason | 0x31 | 1 | Integer | |
| motorFailReason | getMotorFailReason | 0x32 | 1 | Integer | |
| SDKCtrlDevice | getSDKCtrlDevice | 0x34 | 1 | Integer | 1=APP |

### The 0x20 status dword (Integer LE @0x20) bit getters  [app]
- `groundOrSky()`  = (w >> 1) & 3
- `isMotorUp()`    = (w >> 3) & 1
- `isSwaveWork()`  = w & 0x10
- `isGpsUsed()`    = w & 0x8000
- `getVoltageWarning()` = (w >> 9) & 3
- `isVisionUsed()` = w & 0x100
- `getGpsLevel()`  = **(w >> 0x12) & 0xF**   (0x12 = 18)
- `getGohomeStatus()` = (w >> 5) & 7
- `getBatteryType()`  = (w >> 0x16) & 3
- `canIOCWork()`   = w & 1
- `getModeChannel()` = (w & 0x6000) >> 0xD

**Every one of these [app] offsets already matches telemetry.py exactly.** So the offsets
are NOT the bug. The problem is upstream: whether the OSD push is being *matched/sliced*
at all, and where the mode byte comes from when coords are empty. See §5-§6.

## 3. Cross-check [msdk] dji-sdk-provided-4.18.jar (javap -p -c)

Downloaded `dji-sdk-provided-4.18.jar` from Maven Central
(`repo1.maven.org/.../com/dji/dji-sdk-provided/4.18/`). Class
`dji.midware.data.model.P3.DataOsdGetPushCommon` is NOT obfuscated. `javap -p -c` of
the getters (arguments to `DataBase.get(off,len,Class)` appear as `iconst/bipush` pushes
right before the `invokevirtual ...get`):

| Field | [msdk] bytecode | offset | len | agrees w/ [app]? |
|-------|-----------------|--------|-----|------------------|
| getLongitude | `iconst_0; bipush 8; ldc Double` | 0x00 | 8 | ✅ |
| getLatitude  | `bipush 8; bipush 8; ldc Double` | 0x08 | 8 | ✅ |
| getHeight    | `iconst_0; bipush 16; iconst_2; Short` | 0x10 | 2 | ✅ |
| getGpsLevel  | `bipush 32; iconst_4; Integer` then `bipush 18; bipush 15` (>>18 &0xF) | 0x20 | 4 | ✅ |
| getGpsNum    | `bipush 36; iconst_1; Short` | 0x24 | 1 | ✅ |
| getFlycState | `_recData; bipush 30; iconst_1; Short` then `sipush -129` (&0xFF7F) | 0x1e | 1 | ✅ |
| getSDKCtrlDevice | `bipush 52; iconst_1; Integer` | 0x34 | 1 | ✅ |
| getSwaveHeight | `iconst_0; bipush 41; iconst_1; Short` | 0x29 | 1 | ✅ |

**[msdk] 4.18 matches [app] on every offset.**

## 4. Cross-check [dji-firmware-tools] flyc_osd_general (0x03/0x01 dissector)

`comm_dissector/wireshark/dji-dumlv1-flyc.lua`, `flyc_osd_general_dissector`, walks the
payload with a running `offset` starting at 0:

```
longitude       double  @0x00 (+8)
latitude        double  @0x08 (+8)
relative_height int16   @0x10 (+2)
x/y/z speed     int16   @0x12/0x14/0x16
pitch/roll/yaw  int16   @0x18/0x1a/0x1c
flyc_state      uint8   @0x1e
... status dword @0x20 (e_gps_level etc.)
gps_nums        uint8   @0x24
...
ultrasonic_hgt  uint8   @0x29
```

**Third source — identical offsets.** All three sources (app / msdk / dji-firmware-tools)
agree. Note dji-firmware-tools registers this under **FLYC set 0x03, subcmd 0x01** as its
"general" push id; the WM160-era DUMLv1 FC uses **0x03/0x43** for `DataOsdGetPushCommon`
(confirmed from [app]/[msdk] config below). The *layout* is what matters and it is the same.

## 5. cmd_set / cmd_id / sender — where the push actually comes from  [app]+[msdk]

`DataOsdGetPushCommon` is registered in TWO command tables (`/tmp/all/uav/midware/data/config/P3/`):

- **CmdIdOsd$CmdIdType** — `"GetPushCommon"`, ordinal 0, **cmd_id = 0x01** → set **OSD (0x09)**.
  `"GetPushHome"`, ordinal 1, **cmd_id = 0x02** → `DataOsdGetPushHome`.
- **CmdIdFlyc$CmdIdType** — `"GetPushCommon"`, ordinal 0x24, **cmd_id = 0x43** → set **FLYC (0x03)**.
  `"GetPushHome"`, ordinal 0x25, **cmd_id = 0x44** → `DataOsdGetPushHome`.

CmdSet numeric values (CmdSet.smali / DeviceType.smali): **FLYC = 0x03, OSD = 0x09**.
`DeviceType`: WHO=0, CAMERA=1, APP=2, **FLYC=3**, GIMBAL=4, CENTER=5, RC=6, WIFI=7,
DM368=8, **OSD=9**, PC=0x0a, BATTERY=0x0b, ...

So the canonical live frame from a WM160 FC is:
**cmd_set=0x03 (FLYC), cmd_id=0x43, sender=0x03 (FLYC), receiver=0x02 (APP)**.
The 0x09/0x01 (OSD-set) form is an alias for the *same model class* but is a different
transport; whether WM160 actually emits it (and with an identical byte layout) is NOT
guaranteed and must be confirmed from a live dump before trusting it.

## 6. How `_recData` is populated — proves offset 0 == payload byte 0  [app]

Receive path in `DataBase.smali`:
`setPushRecPack(Pack)` → reads `Pack.p:[B` (line 6715) → `setPushRecData(p.p)` →
`setRecData()` → `_recData = p.p`. **No slicing, no base offset.**

`Pack.p` is filled in `RecvPack.smali` (lines 649-673): after reading `m`=cmd_set @byte9
and `n`=cmd_id @byte10 of the frame, it `arraycopy`s from `offset = 0x0b` (11) — i.e. the
byte right after cmd_id — into `Pack.p`. So **`_recData[0]` = the first payload byte after
`[magic|len|crc8|sender|receiver|seq|seq|cmd_type|cmd_set|cmd_id]`.**

Our `duml.py` does exactly the same: `payload = frame[11:-2]`. **Our slicing convention is
byte-identical to DJI's.** `_parse_osd(p)` with `p = pkt.payload` therefore uses the right base.

Also note `BytesUtil.Y/.O/.H` (the readers behind `get()`) return **0** — not an error —
when the requested `offset+len` exceeds the array. So in the app a short buffer yields 0s,
never a crash; our Python returns `None` for the same out-of-range read. This difference
matters for the diagnosis below.

<!-- PROGRESS: 70% -->

## 7. HOME push — DataOsdGetPushHome (0x03/0x44 or 0x09/0x02)  [app]+[msdk]+[dji-fw-tools]

| Field | Getter | offset | len | type | source |
|-------|--------|--------|-----|------|--------|
| longitude | getLongitude | 0x00 | 8 | Double rad→deg | [app]+[msdk] |
| latitude  | getLatitude  | 0x08 | 8 | Double rad→deg | [app]+[msdk] |
| **home flags** | isHomeRecord / hasGoHome / getGoHomeStatus / getGoHomeMode / getAircraftHeadDirection / isBeginnerMode / isReatchLimitHeight | **0x14** | 2 | Integer(uint16) | [app]+[msdk] |
| goHomeHeight | getGoHomeHeight | 0x16 | 2 | Integer(uint16) | [app]+[msdk] |
| dataRecorderStatus | | 0x1a | 1 | | [app] |
| courseLockAngle | | 0x18 | 2 | Short | [app] |
| flycLogIndex/curFileIndex | | 0x1e | 2 | | [app] |
| motorEscmState | | 0x29 | 4 | | [app] |
| forceLandingHeight | | 0x2d | 1 | (needs len>0x2d) | [app] |
| status dword2 | many isXAbnormal() | 0x2e | 4 | Integer | [app] |

Flag bits inside the uint16 @0x14 (from [app] getters + [dji-fw-tools]
`flyc_osd_home_point_osd_home_state`):
- bit0 (0x01) = **isHomeRecord** (home point recorded)  ← `home_recorded`/`home_set`
- bit1 (0x02) = goHomeMode
- bit2 (0x04) = aircraftHeadDirection
- bit3 (0x08) = isDynamicHomePointEnable
- bit4 (0x10) = isReatchLimitDistance
- bit5 (0x20) = isReatchLimitHeight
- bit6 (0x40) = isMultipleModeOpen
- bit7 (0x80) = **hasGoHome**
- bits8-9 = compassCeleStatus, bit10 = isCompassCeleing, bit11 = isBeginnerMode, bit12 = IOCEnabled

**Confirms the existing `parse_home_location`**: lon f64 @0x00, lat f64 @0x08, flags u16 @0x14
bit0 = recorded. These offsets are correct. dji-firmware-tools shows the same
(`osd_lon`/`osd_lat` doubles first, `osd_home_state` uint16 with `e_homepoint_set = 0x01`).

⚠️ dji-firmware-tools additionally documents an **`osd_alt` float32** between the lat and the
home_state on *some* variants, which on those variants pushes `home_state` to @0x14 only if
the two doubles are followed directly by it. On WM160 [app]+[msdk] place `home_state`
squarely at **0x14** (right after the 2 doubles @0x00 and @0x08 = 16 bytes), so there is no
extra float on this platform. Home offsets are therefore confirmed for WM160.

<!-- PROGRESS: 82% -->

## 8. WHY does mode parse while GPS / coords / altitude read empty?

This is the crux, and it is NOT an offset error — all offsets are triple-confirmed and our
payload slicing is byte-identical to DJI's. The logic:

`_parse_osd` sets `flight_mode` **only** from `u8 @0x1e`, and it is the **only** place
`flight_mode` is ever assigned (grep-confirmed). It runs only when
`len(payload) >= 0x34`. Within that same buffer:
- `altitude_m` = s16 @0x10 — in range whenever len ≥ 0x12
- `gps_level`  = (u32@0x20 >> 18)&0xF — in range whenever len ≥ 0x24
- `satellites` = u8 @0x24 — in range whenever len ≥ 0x25

So if a genuine ≥0x34-byte OSD-common frame were being parsed, `altitude`, `gps_level`
and `satellites` could **never** be `None` — at worst they'd be `0`. The fact that they are
`None` *simultaneously* with a plausible `mode` means **the frame that set `mode` is NOT a
correctly-aligned DataOsdGetPushCommon**. Two concrete mechanisms produce exactly this:

**(A) A shifted / version-prefixed variant.** If the frame we match has N extra leading
bytes (a subtype/index/version prefix — common on the OSD-set 0x09/0x01 transport, which
wraps the same model), then every field is displaced by +N:
- coords @0x00/0x08 read the prefix + start of the real longitude → almost always outside
  [-180,180]/[-90,90] → **rejected by the range guard → stays None** ✓ (exactly the symptom)
- `mode` @0x1e reads a shifted byte; `FLYC_STATE.find()` maps *many* codes to valid names
  (0-17,19,23-52,100), so a shifted byte very often yields a plausible name like GPS_Atti ✓
- `gps_level`/`sats`/`alt` read shifted bytes → garbage or, if the shift pushes 0x24/0x25
  past the end of a shorter-than-expected buffer, our helpers return **None** ✓

**(B) Wrong frame entirely.** `feed_packet` dispatches purely on (cmd_set,cmd_id) with **no
sender check** and aliases BOTH `0x03/0x43` **and** `0x09/0x01` onto `_parse_osd`. If WM160
emits something ≥0x34 bytes under `0x09/0x01` that is *not* the common OSD (or is the
subscription-wrapped form), we parse it at classic offsets and get a coincidental mode with
dead coords.

Either way the mechanism is the same: **mode is being read from a mis-aligned or wrong
buffer; the coordinates fail the range guard (→None) and the GPS/alt bytes read past the
end or read noise.** The offsets themselves are correct.

## 9. Corrected offset table (source-tagged) — DataOsdGetPushCommon

| off | field | len | type | scale | sources |
|-----|-------|-----|------|-------|---------|
| 0x00 | longitude | 8 | f64 | rad→deg | app,msdk,fwtools |
| 0x08 | latitude  | 8 | f64 | rad→deg | app,msdk,fwtools |
| 0x10 | rel/baro height | 2 | s16 | ×0.1 m | app,msdk,fwtools |
| 0x12 | xSpeed | 2 | s16 | ×0.1 | app,msdk,fwtools |
| 0x14 | ySpeed | 2 | s16 | ×0.1 | app,msdk,fwtools |
| 0x16 | zSpeed (climb) | 2 | s16 | ×0.1 | app,msdk,fwtools |
| 0x18 | pitch | 2 | s16 | ×0.1 | app,msdk |
| 0x1a | roll  | 2 | s16 | ×0.1 | app,msdk |
| 0x1c | yaw   | 2 | s16 | ×0.1 | app,msdk |
| 0x1e | flycState/mode | 1 | u8 | `& 0x7F` | app,msdk,fwtools |
| 0x20 | status dword | 4 | u32 | bits | app,msdk,fwtools |
| — | gps_level | | | `(u32@0x20 >> 18) & 0xF` | app,msdk |
| — | is_flying | | | `(u32@0x20 >> 1) & 3 == 2` | app,msdk |
| — | motors_on | | | `(u32@0x20 >> 3) & 1` | app,msdk |
| 0x24 | gpsNum/satellites | 1 | u8 | — | app,msdk,fwtools |
| 0x25 | flightAction | 1 | u8 | — | app,msdk |
| 0x26 | motorFailedCause | 1 | u8 | `& 0x7F` | app,msdk |
| 0x27 | nonGpsCause/flags | 1 | u8 | nibble | app,msdk |
| 0x29 | swaveHeight (VPS) | 1 | s8 | ×0.1 m | app,msdk,fwtools |
| 0x2a | flyTime | 2 | u16 | deciseconds /10 | app,msdk |
| 0x2f | flycVersion | 1 | u8 | — | app,msdk |
| 0x30 | droneType | 1 | u8 | — | app,msdk |
| 0x34 | SDKCtrlDevice | 1 | u8 | 1=APP | app,msdk |

**No offset in telemetry.py `_parse_osd` needs to change.** They are all correct.

## 10. Exact fix for telemetry.py

The fix is NOT in the offsets — it is in *frame identification and coherence*:

1. **Restrict to the canonical frame and require the FC as sender.** Parse the common OSD
   only from `cmd_set==0x03 and cmd_id==0x43 and sender==0x03 (FLYC)`. Do **not** also feed
   `0x09/0x01` into the same parser until a live dump proves it carries the identical,
   unshifted layout on WM160. Same for home: `0x03/0x44` from sender 0x03; treat `0x09/0x02`
   as unconfirmed.

   ```python
   FLYC = 0x03  # DeviceType.FLYC sender
   def feed_packet(self, pkt):
       p = pkt.payload
       if pkt.sender == FLYC and pkt.cmd_set == 0x03 and pkt.cmd_id == 0x43 and len(p) >= 0x35:
           self._parse_osd(p)
       elif pkt.sender == FLYC and pkt.cmd_set == 0x03 and pkt.cmd_id == 0x44 and len(p) >= 0x18:
           self.parse_home_location(p)
       ...
   ```
   (Require `len >= 0x35` so `SDKCtrlDevice @0x34` is in range; `>= 0x18` for home so the
   flags u16 @0x14 and goHomeHeight @0x16 are present.)

2. **Add a coherence gate so a mis-aligned frame can't masquerade as OSD-common.** Before
   trusting `mode`, sanity-check the same buffer: e.g. require `pitch/roll` (s16 @0x18/0x1a)
   within ±1800 (±180.0°) and `flycVersion` (u8 @0x2f) nonzero/plausible. If the buffer is
   shifted, these fail and we skip it instead of publishing a phantom GPS_Atti.

3. **Capture the truth.** The static evidence proves the offsets are right, so the residual
   ambiguity (is it a prefix shift or a wrong-frame alias?) can only be closed with one real
   dump. Run `pc_client --capture osd.txt`, then inspect the first bytes of the frames that
   currently trigger `mode`. Check:
   - which `(sender,set,id,len)` actually carries the position — expect `(0x03,0x03,0x43,≈0x36+)`;
   - whether byte[0:16] is a plausible pair of f64 radians (|deg| ≤ 180) once GPS locks;
   - if there is a constant leading prefix, its width N — then either strip N or confirm the
     0x09 transport wraps the model and slice accordingly.

   `dump_packets()` already records one sample per `(sender,set,id)`; use it to enumerate
   every FC push and find the one whose bytes 0..16 decode to the real lat/lon.

4. **Do NOT change any byte offset in `_parse_osd` or `parse_home_location`.** They match DJI
   [app] 100%, MSDK 4.18 [msdk] 100%, and dji-firmware-tools [dji-fw-tools] 100%.

### Source agreements / disagreements
- app vs msdk vs dji-firmware-tools: **no disagreement** on any common-push or home-push
  offset for the WM160 era.
- The only cross-source nuance is the optional `osd_alt` float in some dji-firmware-tools
  home variants; [app]+[msdk] confirm WM160 keeps `home_state` at 0x14 with no extra float.
- WM160 selection by DroneType (u8 @0x30) / FlycVersion (u8 @0x2f): version-gated getters
  (motorFailedCause, isEscError, paddleState, etc.) branch on `getFlycVersion()`, but the
  **position/GPS/mode block (0x00-0x34) is version-independent** — no offset shift by version.

<!-- PROGRESS: 100% -->
