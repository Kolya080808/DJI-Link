# INTELLIGENT_AND_PARAMS — WM160 automated flight features + the FC param name→hash table

Evidence-based reverse of DJI Fly (`dji.go.v5` v1.21.4). Every command below was read out of the
app's own DUML builder classes, disassembled with `baksmali` from the 16 DEX in
`reverse_docs/unpacked_app_dex/`. Enum letters (`CmdSet`, `CmdIdFlyc`, `CmdIdEYE`, `CmdIdCommon`)
were resolved from their `<clinit>` in `classes_016b200c.dex`; builder payloads from the
`Data*` classes in `classes_0451d00c.dex`. Numeric values are cited to the file + method they came
from — nothing here is guessed.

Companion docs: `FLIGHT_GATING.md` (takeoff/motors/virtual-stick/limits — not repeated here),
`MASTER_REPORT.md` §2.2 (frame/addressing), `flyc_param_infos.json` (687-param table),
`full_table.txt` (DUML name table).

Conventions (all confirmed from `uav/midware/util/BytesUtil.smali`):
- `n0(I)` = **u16 little-endian** (2 bytes). `o0(J)` = **u32 little-endian** (4 bytes, low 32 bits).
- `y(F)` = float IEEE-754 LE (4B), `x(D)` = double LE (8B), `z(I)` = int32 LE (4B),
  `A(J)` = int64 LE (8B), `D(S)` = int16 LE (2B), `B(String)` = `getBytes("GBK")`.
- Every builder's `start()` sets sender = APP (`DeviceType.APP` = 0x0a), `CMDTYPE.a`/`NEEDACK.a`
  (a req-with-ack), payload little-endian. `CmdSet` bytes: `d`=0x03 FLYC, `k`=0x0A EYE(vision),
  `a`=0x00 COMMON, `c`=0x02 CAMERA (from `CmdSet.smali`, per `FLIGHT_GATING.md`).

---

# GOAL A — automated / intelligent flight features

## A.0 Model gating first (what the aircraft will actually accept)

WM160 = UAV59, ProductType 0x3b, **no vision/obstacle/tracking sensors**, `supportNavigationMode=false`
(`FINDINGS.md`, `MASTER_REPORT.md §8`, `FLIGHT_GATING.md §G`). Consequence, reconfirmed here:
any command routed to the **vision/perception receiver** (`DeviceType.SINGLE`, sub-address id **7** —
see the QuickMovie/Pano/Timelapse `start()` below) is only honoured if the Mini's firmware maps that
address to its FC. Vision-*tracking* features (ActiveTrack, POI, MasterShot execution, waypoints,
obstacle avoidance) are **rejected** — the builders exist in the app but the aircraft has no hardware
to run them. QuickShots/Panorama/Timelapse are GPS-/camera-/gimbal-driven and are exposed for the
Mini, but their **exact wire trigger is partly behind the generated Key/FlyMcl layer** (see each
section) — the legacy DUML builders documented here are the app's own low-level surface for those
same features and are the strongest static candidates.

`isSupport` keys present for the Mini feature set (`isSupport_keys.txt`): `isSupportCapturePanorama`,
`isSupportPanoStatic`, `isSupportPanoZoomStatic`, `isSupportHyperLapse`, `isSupportHyperLapseFlat`,
`isSupportVelocitySphere`, `isSupportRTH`, `isSupportStartWithoutGPS`, `isSupportMasterShot` (present
but tracking-based → verify at runtime). No `isSupportActiveTrack`/`isSupportWaypoint` entry.

---

## A.1 QuickShots (Dronie / Rocket / Circle=Orbit / Helix=Screw / Boomerang=Oblique)

DJI's internal name is **"QuickMovie"**. Builder: `uav/midware/data/model/P3/DataEyeSetQuickMovieParams`.

- **Command: `cmd_set 0x0A (EYE) / cmd_id 0x4A`** = `uav_vision_set_action_cmd`
  (`start()` uses `CmdSet.k` + `CmdIdEYE$CmdIdType.S`; `CmdIdEYE.S` resolved = **0x4A**;
  cross-checked `full_table.txt` `0x0A/0x4A uav_vision_set_action_cmd_req`).
- **Receiver = vision/perception**: `DeviceType.SINGLE`, sub-address `Pack.g = 7` (from `start()`).
- **Payload** (`doPack`, a TLV list): 
  ```
  [0]      count  u8   (number of ActionParam entries)
  then for each entry:
    [+0]   index  u8   (ActionParamIndex.value)
    [+1]   size   u8   (ActionParamIndex.size: 1 or 4)
    [+2..] data        (size==4 → float LE via y(F); else int LE via z(I), only `size` bytes kept)
  ```
- **`ActionParamIndex` enum** (resolved from `DataEyeSetQuickMovieParams$ActionParamIndex.smali`,
  ctor `(name, ordinal, value, size)`):
  ```
  ACTION_TYPE   value=0x00 size=1     ← which QuickShot (see ActionType below)
  IS_START      value=0x01 size=1     ← 1=start / 0=stop
  VELOCITY_X    value=0x02 size=4 (float)
  VELOCITY_Y    value=0x03 size=4 (float)
  VELOCITY_Z    value=0x04 size=4 (float)
  DISTANCE      value=0x05 size=4 (float)   ← Boomerang/Oblique distance, Circle radius
  TIME          value=0x06 size=4 (float)
  PROGRESS      value=0x07 size=1
  END_OF_PARAMS value=0xFF size=1
  ```
- **`ActionType` (the sub-mode id, at ACTION_TYPE):** the class
  `DataEyeSetQuickMovieParams$ActionType.smali` is an **obfuscated constant holder** — fields
  `a=0x0, b=0x1, c=0x2, d=0x3, e=0x4, f=0x6, g=0x8, h=0xa` (8 distinct 1-byte codes). **The
  human name↔code mapping (which code is Dronie vs Rocket vs Circle vs Helix vs Boomerang) is NOT
  recoverable statically** — the source names were stripped. This is the one QuickShot value that
  needs a live capture. **Undecidable-static; Frida target:** hook
  `uav/midware/data/manager/P3/DataBase->start(...)` (or `DataEyeSetQuickMovieParams.doPack`) and
  launch each QuickShot from the app once → read the ACTION_TYPE byte.
- **Failure reasons** come back in the ACK: `getFailedReasons()` parses `[count u8]` then `count ×
  [reason u16]` pairs (`DataEyeSetQuickMovieParams$FailedReason`/`$FailedReasonStruct`).

**Modern path note:** on v5 the UI drives QuickShots through the generated model
`com/uav/flymodel/generated/impl/camera/quickshot/QuickShotControlModelImpl` (`startMission`,
`invalidReason`) and `handwrite/camera/quickshot/v1/*` (GPS FlyMcl). Config sliders map to the TLV
fields above: `QuickShotConfig.RocketModeHeight`→VELOCITY_Z, `ScrewModeRadius`/`ObliqueModeDistance`
→DISTANCE, `RotateFlightSpeed`→VELOCITY_*, `FlightDirection` (`FLIGHT_GATING.md §G`). Whether WM160
ultimately receives exactly `0x0A/0x4A` or a FLYC-side equivalent is HW-gated (receiver = vision addr
7) → confirm with the same Frida hook.

## A.2 Panorama (sphere / 180 / wide-angle)

Two cooperating commands:

- **Enable pano mode (vision):** `DataEyeSetPanoramaEnabled` →
  **`cmd_set 0x0A / cmd_id 0x6B`** (`CmdIdEYE.k0` resolved = 0x6B), receiver vision (SINGLE id 7).
  Payload = **2 bytes `[enable u8][enable u8]`** (`doPack` writes the same flag to [0] and [1]).
- **Set pano capture mode (camera):** `cmd_set 0x02 / cmd_id 0x6E` `uav_camera_set_pano_mode`
  (read-back `0x02/0x6F`) — `full_table.txt`/`cmdmap.txt`. This selects sphere / 180 / wide.
- **How frames are captured:** the aircraft yaws + pitches the gimbal through a grid and fires the
  shutter per cell; the **grid sequencing is orchestrated app-side** (behind the pano controller),
  not a single DUML. Live progress/geometry comes back as the push
  `DataEyeGetPushPanoramaInformation` (and `free_pano_cap_area_info 0x0A/0x1B`, `FLIGHT_GATING.md §G`).
  The exact per-frame gimbal/shutter sequence for WM160 is **undecidable statically** (app-side loop)
  → Frida `DataBase.start()` during a pano to log the gimbal-attitude + shutter DUML stream.
- Supported per `isSupportCapturePanorama` / `isSupportPanoStatic` / `isSupportPanoZoomStatic`
  (`isSupport_keys.txt`).

## A.3 Timelapse / Hyperlapse (free / circle / course-lock) + CineSmooth

Vision timelapse suite (all `CmdSet.k`=0x0A, receiver vision SINGLE id 7; ids resolved from
`CmdIdEYE$CmdIdType.smali`):

| Builder | cmd_set/cmd_id | Payload (`doPack`) |
|---|---|---|
| `DataEyeSetTimeLapseSubMode` | **0x0A/0x74** (`b1`) | `[subMode u8]` — selects free / circle / course-lock (see note) |
| `DataEyeSetTimeLapseStart`   | **0x0A/0x78** (`r1`) | 16 bytes: `[submodeType u8]`, `[u16 z@1]`, `[u16 z@3]`, `[u64 A@5]`, `[u8@0xD]`, `[u8@0xE]` |
| `DataEyeSetTimeLapseAction`  | **0x0A/0x7A** (`s1`) | `[type u8][isVideoGet u8]` — start/stop/pause the run |
| `DataEyeSetTimeLapseParams`  | **0x0A/0x7B** (`v1`) | `[paramType.type u8 ×2][value]` where value width/encoding = `ParamType.length`/`classType` (int z / float y / double x) — `uav_vision_set_time_lapse_set_param`, `full_table.txt 0x0A/0x7B` |
| `DataEyeSetTimeLapseKeyFrame`| (keyframe add/del) | `$Action` enum `ADD=0/DELETE=1` (hyperlapse waypoints) |
| `DataEyeGetPushTimeLapseKeyFrame` / `...OverallData` | push | live keyframe + overall progress |

- Camera side: `cmd_set 0x02 / cmd_id 0x4A` `uav_camera_set_timelapse_para` (get `0x02/0x4B`)
  (`full_table.txt`).
- **`subMode` is a raw `int` field** (no named enum in the builder). The free(0)/circle/course-lock
  numeric mapping is set by the caller — **undecidable statically here**; resolve via the same
  Frida `DataBase.start()` hook while picking each timelapse mode. Supported per `isSupportHyperLapse`
  / `isSupportHyperLapseFlat` (`isSupport_keys.txt`).
- **CineSmooth** is NOT a command — it is a control-gain profile (lower input limits / softer expo),
  see `FLIGHT_GATING.md §C` and the gain params in Goal B below.

## A.4 IOC / headless / course-lock / home-lock / Tripod / Cinematic — FULLY PINNED

Dedicated FLYC command set (receiver FLYC, `CmdSet.d`=0x03; ids resolved from `CmdIdFlyc`):

- **`DataFlycStartIoc` → `cmd_set 0x03 / cmd_id 0x97`** (`CmdIdFlyc.C2` = 0x97).
  Payload = **`[IOCType u8]`**. `IOCType` enum (`DataFlycStartIoc$IOCType.smali`):
  ```
  IOCTypeCourseLock = 0x01   (headless / course-lock)
  IOCTypeHomeLock   = 0x02   (home-lock)
  IOCTripod         = 0x03
  IOCTypeHomeLockA2 = 0x04   (== Cinematic = 0x04)
  IOCTypeOther      = 0x64
  ```
- **`DataFlycStopIoc` → `0x03/0x98`** (`CmdIdFlyc.F2` = 0x98).
- **`DataFlycSetIoc` → `0x03/0x2B`** (`CmdIdFlyc.q` = 0x2B) — enable/config IOC.
- Read-back: `DataFlycGetIoc` (`$MODE` enum).
- Alternate toggle via function-control `0x03/0x2A` (`CmdIdFlyc.p` = 0x2A confirmed):
  `FLYC_COMMAND.IOCOpen=0x13 / IOCClose=0x14` (`FLIGHT_GATING.md §C` appendix).
- Tripod on the Mini: exposed here as `IOCTripod=0x03`, but tripod-as-flight-mode is mostly a gain
  profile — treat with runtime verification.

## A.5 RTH configuration + precision landing

- **RTH altitude** = FC param `go_home.fixed_go_home_altitude` [212] def20/min20/max500 → hash-write
  `0x03/0xF9` (Goal B). No dedicated non-hash "set RTH alt" command.
- **Max height/radius** without hash: `DataFlycSetLimits 0x03/0x2D` `[mode u8][value u16 LE]`
  (`FLIGHT_GATING.md §E`).
- **On-signal-loss behaviour:** `set_fail_safe_action 0x03/0x3B` / `get 0x03/0x3C`
  (`DataFlycSetFsAction`/`GetFsAction`, `full_table.txt`).
- **Set/refresh home:** function-control `0x03/0x2A` `HOMEPOINT_NOW=0x03` (current pos) /
  `HOMEPOINT_LOC=0x05` (operator); arbitrary lat/lon via `DataFlycSetHomePoint 0x03/0x31`
  `[type u8][lat f64 rad][lon f64 rad][interval u8]` (`FLIGHT_GATING.md §D`).
- **Trigger RTH / cancel:** function-control `0x03/0x2A` `GOHOME=0x06` / `DropGohome=0x0C`.
- **Precision landing:** function-control `0x03/0x2A` `PRECISION_TAKE_OFF=0x22` records the precise
  takeoff spot; landing energy/state via pushes `DataEyeGetPushPreciseLandingEnergy` and
  `DataEyeGetPushException$PreciseLandingState`. Downward-vision dependent → verify on the unit.

## A.6 Everything else the app exposes — supported vs REJECTED

| Feature | WM160? | Command / evidence |
|---|---|---|
| QuickShots (QuickMovie) | **YES (GPS)** | `0x0A/0x4A` §A.1 (ActionType code obfuscated) |
| Panorama | **YES** | `0x0A/0x6B` enable + `0x02/0x6E` camera §A.2 |
| Timelapse / Hyperlapse | **YES** | `0x0A/0x74…0x7B` + `0x02/0x4A` §A.3 |
| CineSmooth | YES (gains) | §A.3 / gain params |
| IOC course-lock / home-lock | YES | `0x03/0x97` §A.4 |
| RTH / failsafe / precision-land | YES | §A.5 |
| Beginner/Novice | YES | param `novice_cfg.novice_func_enabled_0` [343] (hash-write) |
| **MasterShot** | **likely NO** | only `DataEyePushMasterShotInfo` (status push) + `mastershot_set_param 0x0A/0xF6` exist — **no Set/Start builder**; tracking-based → aircraft rejects. Verify `isSupportMasterShot` at runtime |
| **ActiveTrack / Follow / Spotlight** | **NO** | tracking cmds (`0x0A/0x20`, `SetTrackingTarget 0x0A/0x94`, `DataEyeStartMultiTracking`) — no sensors, `supportNavigationMode=false` |
| **POI / Orbit-of-interest** | **NO** | `DataEyeSetPOIAction/SetPOIParams/SetPOIInitialTarget`, `0x0A/0xC4` — vision-based, rejected |
| **Waypoint / WPMZ** | **NO** | `0x22 fc2` / `libwpmz_jni` present, no waypoint HW on Mini |
| **Obstacle avoidance** | **NO** | `DataEyeGetPushOmniAvoidance/FrontAvoidance` exist, no sensors |
| Gesture / Palm control | **NO** | `DataEyeSetHandGestureEnabled`, `DataEyeGetPerceptionGesture` — vision, rejected |

---

# GOAL B — the FC parameter name→hash table (unblocks max-speed, gains, RTH-alt, everything)

## B.0 Executive answer

The app **never receives a name↔hash table from the drone.** It ships all 687 **names** as a bundled
resource and **computes each 32-bit hash locally with a native function**. Therefore:

1. There is a get-param-info-**BY-INDEX** request (`0x03/0xF0`) and it returns the param's **name**,
   type, size, attribute and min/max/default — but **NOT the hash**. So you can enumerate names from
   the aircraft, but that does not give you hashes.
2. Every "by hash" request/write (`0x03/0xF7/0xF8/0xF9/0xFA`) requires the hash **first**, and the
   only place the hash exists is the local computation. This is the "circular" case — **it is real**,
   and the break is the local hash function, not a drone dump.
3. **The clean unblock:** the hash algorithm is the single native call
   `GroudStation.native_hashFromString(byte[])`. Feed it each of the 687 names from
   `flyc_param_infos.json` (GBK bytes) → you get the entire name→hash table offline. Then every param
   is writable via `0x03/0xF9 [hash u32 LE][value]`.

## B.1 How the app builds its param table (the whole chain, cited)

`uav/midware/data/manager/P3/UAVFlycParamInfoManager` (`classes_016b200c.dex`):
- Holds `HashMap<String name, ParamInfo>` (field `a`).
- On construction it spawns thread `"FlyParam-Midware"` (`c()`); the runnable (`$a.run()`) reads
  raw resource **`R$raw.flyc_param_infos`** via `com/uav/frame/util/V_FileUtil;->h(Context,int)`
  and passes the JSON string to `d()`.
- `d(json)` Gson-parses it to `List<ParamInfoBean>` (`V_JsonUtil.b`), then for each calls
  `bean.getParamInfo()` and stores by `name`.
- **`isNew()` is hard-coded `return true`** (method literally loads `const/4 v0, 0x1; return v0`).
  ⇒ the app **always** uses the hash addressing scheme, never the legacy index scheme.
- Lookups: `read(name)`, `readByHash(J)` (linear scan comparing `ParamInfo.hash`),
  `readByIndex(I)`, `getNameByHash(J)`, `getNameByIndex(I)`.

`uav/midware/data/params/P3/ParamInfoBean` (`classes_0451d00c.dex`) — the JSON record shape:
fields `index, typeID, size, attribute, name, minValue, maxValue, defaultValue` — **no hash field**
(matches `flyc_param_infos.json` exactly). Its `getParamInfo()` ends with:
```
v0 = BytesUtil.B(name)                                  ; name → GBK bytes
hash = GroudStation.native_hashFromString(v0)           ; native 32-bit hash → stored in a long
paramInfo.hash = hash
```
So **the hash is computed, not shipped and not queried from the drone.**

`uav/midware/natives/GroudStation` (`classes_0451d00c.dex`):
`public static native native_hashFromString([B)J` — implemented in
**`lib/arm64-v8a/libGroudStation.so`** (the DJI "packer" native lib; symbol registered dynamically,
no plain `Java_…` export string). Same lib holds `native_calcCrc16/8`, `native_getCRCFromData`.

`DataFlycGetParamInfo$TypeId` raw value → wire type (from `DataFlycGetParamInfo$TypeId.smali`;
the JSON `typeID` is exactly this value):
```
0=INT08U(1B) 1=INT16U(2B) 2=INT32U(4B) 3=INT64U(8B)
4=INT08S(1B) 5=INT16S(2B) 6=INT32S(4B) 7=INT64S(8B)
8=FLOAT(4B)  9=DOUBLE(8B) 10=BYTE      11=STRING   12/0x64=OTHER
```

## B.2 The by-INDEX enumeration request — `0x03/0xF0` (name, NO hash)

`DataFlycGetParamInfo` (`classes_0451d00c.dex`):
- **Request:** `cmd_set 0x03 (FLYC) / cmd_id 0xF0`, receiver FLYC (`CmdIdFlyc.lb` = **0xF0**).
  Payload `doPack` = **`[index u16 LE]`** (2 bytes, `n0`). So you can loop index = 0…686.
- **Response `getInfo()` parses** (offsets into `_recData`):
  ```
  [1..2]   typeId  u16       [3..4]   size    u16
  [5..6]   attribute u16     [7..10]  min  (4B, typed)
  [11..14] max (4B)          [15..18] default (4B)
  [19..end] name  (string)
  ```
  **There is no hash field in this response.** ⇒ by-index enumeration yields names (which you already
  have in the bundled JSON) but never a hash.

`DataFlycGetPushParamsByIndex` (a *push* of live values): `setPushRecData` walks
`[index u16][value(size)]` entries, resolving each index via `UAVFlycParamInfoManager.readByIndex` —
i.e. it needs the table already built, and again carries **no hash**. Confirms the drone only ever
speaks *index* (for the old scheme) or *hash* (for the new scheme), never handing you the pairing.

## B.3 Verdict on the circular dependency + the exact alternatives

**The circularity is real:** to read/write by hash you need the hash; the drone never returns it; the
only source is `native_hashFromString(name)`. Break it one of two ways:

**(1) Compute the whole table locally (recommended).** Take the 687 `name` strings from
`flyc_param_infos.json` and run each through `native_hashFromString` (GBK-encoded bytes, no null
terminator — `BytesUtil.B` = `getBytes("GBK")`, and names are ASCII so GBK == ASCII). Options:
   - **Frida (fastest):** attach to DJI Fly, then
     `Java.use('uav.midware.natives.GroudStation').native_hashFromString(...)` in a loop over the
     names → dump `{name: hash & 0xffffffff}`. One run = full table. (You can also just hook
     `ParamInfoBean.getParamInfo` / `UAVFlycParamInfoManager.read` and read `ParamInfo.hash` after the
     app finishes loading.)
   - **Ghidra/static:** reverse the one function in `lib/arm64-v8a/libGroudStation.so`
     (`native_hashFromString`) and re-implement it in Python. **This is the exact undecidable-static
     item** — the algorithm lives in the .so, not in DEX.

**(2) Frida on the write itself.** Hook `DataFlycSetParams.doPack`/`start` while changing a slider
(e.g. "Max Altitude" or a speed gain) → the 4-byte hash is right there in `_sendData[0..3]`. Reuse it.

**(3) `DataCommonGetCfgFile` — raw FC config download (does NOT solve name↔hash, but documented).**
`DataCommonGetCfgFile` (`classes_0451d00c.dex`): **`cmd_set 0x00 (COMMON) / cmd_id 0x4F`**
(`CmdIdCommon.E` = 0x4F), receiver settable (default `DM368`; set to FLYC for the FC config).
   ```
   Request  doPack (9 bytes): [0] fileType u8 (UAVUpgradeFileType.value)
                              [1..4] offset u32 LE   [5..8] length u32 LE
   Response: [0..3] relLength u32   [4..7] remainLength u32   [8..] data
   ```
   It is a **chunked raw-blob download** (offset/length paging); the app does **not** parse the blob's
   internal layout in DEX (`getDataByte()` just returns bytes[8:]). So whether the returned config
   file embeds name↔hash is **undecidable from the app** — the local `native_hashFromString` path is
   the reliable answer, not this blob.

## B.4 The WRITE path — confirmed `[hash u32 LE][value]` — `0x03/0xF9`

`DataFlycSetParams` (`classes_0451d00c.dex`), `cmd_set 0x03 / cmd_id 0xF9` (`CmdIdFlyc.fd` = **0xF9**,
`uav_fc_set_write_hash_param`, receiver FLYC). Because `isNew()` is always true, `doPack` emits, for
each param (it supports batching an array of `(name,value)`):
```
[hash u32 LE (o0 of ParamInfo.hash)] [ value : `size` bytes, little-endian ]
```
Value encoding is chosen by `TypeId` (via `DataFlycSetParams$1` switch) then truncated/copied to
`ParamInfo.size` bytes: INT16*→`z`/2B, INT32*→`z`/4B, INT64*→`A`/8B, FLOAT→`y`/4B, DOUBLE→`x`/8B,
BYTE→`D`. (The dead legacy branch would have used `[index u16][value]`, but `isNew()==true` disables
it.) So once you have a hash: **`55 … 03 F9 <hash LE u32> <value LE (size bytes)>`**.

Sibling ops (ids resolved from `CmdIdFlyc.smali`, cross-checked `full_table.txt`):
- `0x03/0xF7` `DataFlycGetParamInfoByHash` (`Rc`) — request `[hash u32 LE]`, response same layout as
  §B.2 (`getParamInfo()`, name at +19). Needs the hash already (`setIndex(name)` looks it up in the
  manager).
- `0x03/0xF8` read value by hash (`ad`, `uav_fc_read_hash_param`).
- `0x03/0xFA` reset to default by hash (`DataFlycResetParams`, `id`).

## B.5 The exact param NAME strings to target (from `flyc_param_infos.json`)

Max horizontal speed / tilt (raise for "Sport" top speed):
```
[312] g_config.control.horiz_vel_atti_range_0   INT16S size2  def23 min10 max60
[313] g_config.control.atti_range_0             INT16S size2  def35 min10 max60
[190] g_config.control.horiz_vel_gain_0         INT16S size2  def90 min0  max500
[999] mode_sport_cfg_tilt_atti_range_0          FLOAT  size4  def5  min5  max40
[318] g_config.control.vert_up_vel_0            INT16S size2  def5  min1  max10   (climb speed)
[319] g_config.control.vert_down_vel_0          INT16S size2  def4  min1  max10   (descend speed)
```
Limits / RTH / novice:
```
[236] flying_limit.max_height                    def120 min15 max500
[235] flying_limit.max_radius                     def30 min15 max5000
[212] go_home.fixed_go_home_altitude              def20  min20 max500
[343] novice_cfg.novice_func_enabled_0            (0=off; novice forces GPS)
```

**DarkNoGpsLock — important correction:** a `/dark/`, `/gps_lock/`, `/nogps/` scan of all 687 params
returns **nothing**. `DarkNoGpsLockEnable` is **NOT an FC param** — it is a KeyManager-backed setting
(`KeyDarkNoGpsLockEnable`, `Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn`, impl
`FlyLimitSettingsModelImpl$darkNoGPSLockOn`; `FLIGHT_GATING.md §F`). So it cannot be written by
`0x03/0xF9` with a param hash — its actual DUML is **undecidable statically** and must be captured by
Frida-hooking `DataBase.start()` while toggling the "fly in low light without GPS" switch in Safety
settings. (This is the one weak-GPS control that the param-hash route does *not* unlock.)

---

## Appendix — new command IDs resolved in this pass (sender = 0x0a APP)

```
0x03/0xF0  GetParamInfo BY INDEX      req [index u16 LE] → resp name/type/size/range (NO hash)  (CmdIdFlyc.lb)
0x03/0xF7  GetParamInfoByHash         req [hash u32 LE]  → resp name/type/size/range            (CmdIdFlyc.Rc)
0x03/0xF8  read value by hash                                                                   (CmdIdFlyc.ad)
0x03/0xF9  write value by hash        [hash u32 LE][value size-bytes LE]                        (CmdIdFlyc.fd)
0x03/0xFA  reset by hash                                                                        (CmdIdFlyc.id)
0x00/0x4F  GetCfgFile (raw blob)      req [ftype u8][off u32][len u32] → resp [rel u32][rem u32][data]  (CmdIdCommon.E)
0x03/0x2A  FunctionControl            [FLYC_COMMAND u8]  (home/RTH/motors/cali/IOCOpen0x13/precision0x22) (CmdIdFlyc.p)
0x03/0x2B  SetIoc                     (CmdIdFlyc.q)
0x03/0x97  StartIoc                   [IOCType u8: CourseLock1/HomeLock2/Tripod3/Cinematic4]    (CmdIdFlyc.C2)
0x03/0x98  StopIoc                    (CmdIdFlyc.F2)
0x0A/0x27  vision set_common_ctrl                                                               (CmdIdEYE.R=0x49 nearby; 0x27 per full_table)
0x0A/0x3E  vision set_pano_control                                                              (CmdIdEYE.Q)
0x0A/0x4A  vision set_action_cmd = QuickMovie/QuickShot   [count u8]{[idx u8][size u8][data]}   (CmdIdEYE.S)
0x0A/0x6B  SetPanoramaEnabled         [enable u8][enable u8]                                    (CmdIdEYE.k0)
0x0A/0x74  TimeLapse SubMode          [subMode u8]                                              (CmdIdEYE.b1)
0x0A/0x78  TimeLapse Start            16-byte struct                                            (CmdIdEYE.r1)
0x0A/0x7A  TimeLapse Action           [type u8][isVideoGet u8]                                  (CmdIdEYE.s1)
0x0A/0x7B  TimeLapse SetParam         [type u8×2][value]                                        (CmdIdEYE.v1)
0x02/0x6E  camera set_pano_mode  (get 0x02/0x6F)
0x02/0x4A  camera set_timelapse_para (get 0x02/0x4B)
```

## Open items requiring a live capture (Frida hook target named)
1. **QuickShot ActionType code→name** (Dronie/Rocket/Circle/Helix/Boomerang) — obfuscated fields
   a–h {0,1,2,3,4,6,8,10}; hook `DataEyeSetQuickMovieParams.doPack` / `DataBase.start()`.
2. **Timelapse subMode / pano frame sequence** numeric mapping — same hook while selecting each.
3. **native_hashFromString algorithm** — reverse `lib/arm64-v8a/libGroudStation.so`, or just call it
   over the 687 names via Frida to dump the full name→hash table.
4. **DarkNoGpsLock DUML** — hook `DataBase.start()` while toggling the Safety "low-light no-GPS"
   switch (not a param hash).
5. Whether WM160 firmware honours the **vision receiver (addr 7)** for QuickMovie/Pano/Timelapse or
   remaps them to the FC — send + observe ACK vs reject.
