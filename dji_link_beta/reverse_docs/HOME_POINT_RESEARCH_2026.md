# Home Point Set/Rewrite — Independent Reverse (2026-07-18)

Target: DJI Mavic Mini 1 (WM160 / UAV59), PC → DUML over AOA.
Goal: set home point to (a) an explicit GPS lat/lon, (b) the aircraft's current location; and verify.

**Methodology note:** everything below was re-derived from binaries in this pass. Nothing was
taken on faith from other files in `reverse_docs/`. Where a prior claim is confirmed or
contradicted it is called out explicitly.

**Three independent sources agree byte-for-byte.** This layout was derived from (1) the stripped
native `libsdk_jni.so` (MSDK v5 C++, symbol table recovered — §1), (2) the **public, un-obfuscated
DJI MSDK v4.18 Java** (`dji-sdk-4.18.aar`, decompiled with `javap` — the authoritative ground
truth, DJI's own `DataFlycSetHomePoint.doPack()`), and (3) the o-gs Wireshark DUML dissector.
All three produce the identical command. Every field below is marked with which sources confirm it.

## 0. TL;DR

| What | Value |
|---|---|
| cmd_set | `0x03` (FLYC) — CmdSet.FLYC value 3 |
| cmd_id | `0x31` (49) — CmdIdFlyc.SetHomePoint value 49 |
| sender / receiver | APP (`0x0A`) → FLYC (`0x03`) |
| ack | NEEDACK=YES, EncryptType=NO, CMDTYPE=REQUEST |
| payload length | **18 bytes (0x12), both variants** |
| lat/lon encoding | **f64 little-endian, RADIANS on the wire** (MSDK converts deg→rad before packing) |
| set-to-explicit-GPS | `[0]=0x02 (APP)`, lat@1, lon@9, `[17]=0x64` |
| set-to-current-aircraft | `[0]=0x00 (AIRCRAFT)`, lat/lon zeroed, `[17]=0x64` — 18 bytes, not 1 |
| verify push | FLYC `0x03`/`0x44` `DataOsdGetPushHome`: lon@0, lat@8 (f64 rad), alt@16 (f32), flags@20 |

**HOMETYPE enum (from MSDK `DataFlycSetHomePoint$HOMETYPE`):** `AIRCRAFT=0, RC=1, APP=2, FOLLOW=3`.
MSDK `setHomeLocation(coord)` sends `APP=2`; `setHomeLocationUsingAircraftCurrentLocation()` sends
`AIRCRAFT=0`. This is exactly what the native derivation found (§2/§3) — fully consistent.

## 1. How the command descriptor was recovered

`libsdk_jni.so` (80 MB, aarch64) is *deliberately* damaged: the section header table is
garbage, `PT_DYNAMIC` was gutted to only `DT_NEEDED`/`DT_SONAME`, so `nm -D` returns 0 symbols
and `readelf -S` prints junk. However **a full `.symtab` and its `.strtab` survive in the file**,
just unreferenced.

- strtab base: file/vaddr `0x2414A0`, size `0x978AB4`
- symtab: file offset `0x2F8` … `0x1A9128`, 24-byte `Elf64_Sym`, **72,513 symbols**

Recovery method: take a known mangled name's vaddr from `strings -t x`, compute
`st_name = vaddr - 0x2414A0`, search the file for that 4-byte LE value, and confirm the
surrounding 24-byte records parse as `Elf64_Sym`. For this segment `p_offset == p_vaddr == 0`,
so **file offset == virtual address** for all code below.

The descriptor comes straight out of a C++ template instantiation name:

```
_ZN3uav4core16uav_cmd_base_reqILh1ELh3ELh49E24uav_fc_set_homepoint_req24uav_fc_set_homepoint_rspEC2Ev
  → uav::core::uav_cmd_base_req<(unsigned char)1,      // cmd_type
                                (unsigned char)3,      // cmd_set  = 0x03 FLYC
                                (unsigned char)49,     // cmd_id   = 0x31
                                uav_fc_set_homepoint_req,
                                uav_fc_set_homepoint_rsp>::uav_cmd_base_req()
```

**There is exactly one set-home command in the entire SDK.** I extracted all 449
`uav_cmd_base_req<...>` instantiations; grepping them for `home` yields only this one plus
`confirm_electricity_gohome` (3/82) and the RC-dynamic-homepoint push (see §6).

Confirmed again in the ctor's machine code at VA `0x2A04AD8`:

```asm
2a04b54  mov  w8, #0x301
2a04b58  movk w8, #0x231, lsl #16     ; w8 = 0x02310301
2a04b5c  str  w8, [x19]               ; LE bytes -> 01 03 31 02
```

i.e. header byte0 `0x01` = cmd_type, byte1 `0x03` = **cmd_set**, byte2 `0x31` = **cmd_id**.
Default ack timeout `0x1F4` = 500 ms (`str w9,[x0,#0x14]`).

## 2. Variant A — set home to an explicit GPS coordinate

Symbol: `_Z15HomeLocationSet...` @ **VA `0x2B88DF8`**, size `0x394`.

### 2.1 Range validation (client side, before any packet is built)

```asm
2b88e68  ldr  d0, [x0, #8]            ; UavValue+0x08 = latitude  (degrees, f64)
         fcmp d0, #-90.0  (0xC056800000000000)   -> b.lt  fail
         fcmp d0, #+90.0  (0x4056800000000000)   -> b.hi  fail
2b88e98  ldr  d0, [x0, #0x10]         ; UavValue+0x10 = longitude (degrees, f64)
         fcmp d0, #-180.0 (0xC066800000000000)   -> b.lt  fail
         fcmp d0, #+180.0 (0x4066800000000000)   -> b.hi  fail
         mov  w21, #-0xa              ; returns -10 on range failure
```

So the caller-facing API takes **degrees**; `lat ∈ [-90,+90]`, `lon ∈ [-180,+180]`.
`uav::sdk::LocationCoordinate2D` is `{vtable@0, double latitude@0x08, double longitude@0x10}`
(ctor `_ZN3uav3sdk20LocationCoordinate2DC1Edd`, i.e. `(lat, lon)`).

### 2.2 Payload assembly — the decisive block

```asm
2b88ec8  mov   w9,  #2
2b88ecc  stp   xzr, xzr, [x29, #-0x18]   ; zero 16 B  \  18-byte payload buffer
2b88ed0  sturh wzr, [x29, #-8]           ; zero  2 B  /  at x29-0x18
2b88ed4  mov   x10, #0x2d18
2b88ed8  sturb w9,  [x29, #-0x18]        ; payload[0] = 2      <-- set type
2b88edc  movk  x10, #0x5444, lsl #16
2b88ee0  ldur  q0,  [x0, #8]             ; load {lat, lon} as v0.2d
2b88ee4  movk  x10, #0x21fb, lsl #32
2b88ee8  movk  x10, #0x4009, lsl #48     ; x10 = 0x400921FB54442D18 = M_PI
2b88eec  dup   v2.2d, x10                ; PI in both lanes
2b88ef0  dup   v1.2d, x8                 ; x8 still = 180.0 from last fcmp
2b88ef4  fmul  v0.2d, v0.2d, v2.2d       ; *= PI
2b88ef8  mov   w8,  #0x64
2b88efc  fdiv  v0.2d, v0.2d, v1.2d       ; /= 180.0   => RADIANS
2b88f00  stur  q0,  [x29, #-0x17]        ; payload[1..16] = {lat_rad, lon_rad}  (UNALIGNED)
2b88f04  sturb w8,  [x29, #-7]           ; payload[17] = 0x64 (100)
...
2b88f14  mov   w8, #0x31
2b88f18  mov   w9, #3
2b88f20  sturb w8, [x29, #-0x46]         ; uav_cmd_req+0x02 = 0x31   cmd_id
2b88f24  sturb w9, [x29, #-0x41]         ; uav_cmd_req+0x07 = 0x03   receiver dev type = FC
2b88f28  sturb w9, [x29, #-0x44]         ; uav_cmd_req+0x04 = 0x03   cmd_set
2b88f30  mov   w2, #0x12                 ; payload length = 18
2b88f38  bl    #0x4a0eb00                ; append payload bytes to the request buffer
```

### 2.3 Byte layout (Variant A)

```
off  size  type   value / meaning
0x00   1   u8     0x02   set-type: use the supplied GPS coordinate
0x01   8   f64LE  latitude  in RADIANS   (deg * pi / 180)
0x09   8   f64LE  longitude in RADIANS   (deg * pi / 180)
0x11   1   u8     0x64 (100)  constant emitted by the SDK
total 18 bytes
```

Note the doubles are stored at **odd offsets 1 and 9** — unaligned on purpose. Pack with
`struct.pack("<B dd B", ...)` only if you disable alignment; safest is explicit concatenation
(`bytes([2]) + struct.pack("<dd", lat, lon) + bytes([0x64])`).

`0x64` at `[17]`: this is the field `mInterval` (a settable `byte` in the MSDK class); the SDK
never sets it and it defaults to **100 (`0x64`)**. Never derived from an argument, never read
back. Best reading is a fixed confidence/accuracy field or an "interval". Send `0x64` because that
is exactly what the shipping app sends.

### 2.4 CONFIRMED against DJI's own bytecode (primary ground truth)

`DataFlycSetHomePoint.doPack()` from the public MSDK v4.18 (`javap -p -c`) — this is DJI's own
serializer and it matches the native reverse exactly:

```
 4: bipush 18; newarray byte                 ; payload length = 18
16: getfield mHomeType; .value(); bastore@0  ; payload[0]  = HOMETYPE byte
25: getfield mLantitue (double D)
   dgh.fdd(D)->[B ; arraycopy dst=1 len=8     ; payload[1..8]  = latitude  (8-byte double)
43: getfield mLongtitue (double D)
   dgh.fdd(D)->[B ; arraycopy dst=9 len=8     ; payload[9..16] = longitude (8-byte double)
62: getfield mInterval (byte); bastore@17     ; payload[17] = mInterval (defaults 0x64)
```

`dgh.fdd(double)` = `Double.doubleToLongBits` → 8 LE bytes (a raw IEEE-754 double, no unit
conversion at this layer). `start()` sets sender `DeviceType.APP`, receiver `DeviceType.FLYC`,
`CmdSet.FLYC` (value 3), `CmdIdFlyc.SetHomePoint` (value 49 = 0x31), NEEDACK=YES, EncryptType=NO.
The o-gs Wireshark dissector independently labels `flyc [0x31] = "UAV Home Point Set"`.

Units: the doubles are radians **on the wire**. The MSDK's `FlightController.setHomeLocation`
takes `LocationCoordinate2D` in **degrees**, then `LocationUtils.DegreeToRadian(x) = x*π/180` is
applied to both before `setGpsInfo(latRad, lonRad)`. Confirmed three ways: this deg→rad call, the
native `fmul PI / fdiv 180.0` in §2.2, and the push getters dividing by π and multiplying by 180.

## 3. Variant B — set home to the aircraft's current location

Symbol: `_Z46HomeLocationUsingCurrentAircraftLocationAction...` @ **VA `0x2B889D0`**, size `0x2D4`.

```asm
2b889f8  stp   xzr, xzr, [x29, #-0x18]   ; 16 zero bytes
2b889fc  sturh wzr, [x29, #-8]           ;  2 zero bytes   -> payload is ALL ZERO
2b88a04  bl    #0x4a34420                ; uav_cmd_req ctor
2b88a08  mov   w8,  #0x31
2b88a0c  mov   w9,  #0x203
2b88a10  mov   w10, #3
2b88a14  mov   w11, #0x2bc
2b88a1c  sturb w8,  [x29, #-0x46]        ; +0x02 = 0x31  cmd_id
2b88a20  sturh w9,  [x29, #-0x41]        ; +0x07 = 0x03, +0x08 = 0x02
2b88a24  sturb w10, [x29, #-0x44]        ; +0x04 = 0x03  cmd_set
2b88a28  stur  w11, [x29, #-0x34]        ; +0x14 = 0x2BC = 700 ms timeout (vs 500 default)
2b88a30  mov   w2,  #0x12                ; payload length = 18
```

### 3.1 Byte layout (Variant B)

```
off  size  type   value
0x00   1   u8     0x00   set-type: use aircraft's current location
0x01   8   f64LE  0.0    (ignored)
0x09   8   f64LE  0.0    (ignored)
0x11   1   u8     0x00
total 18 bytes, all zero
```

**It is the same command with the same 18-byte length — only `payload[0]` selects the mode,
and the lat/lon are zeroed rather than omitted.** Do not send a 1-byte payload.

The action variant also sets `uav_cmd_req+0x08 = 0x02` (variant A leaves it at the ctor
default) and lengthens the ack timeout to 700 ms. `+0x07`/`+0x08` sit in the request-routing
part of `uav_cmd_req`, not in the DUML payload; on the wire they select receiver dev type 0x03
(FC). Treat 700 ms as the ack timeout to use for Variant B — it is slower because the FC has
to sample GNSS.

## 4. Response and preconditions — `uav_fc_set_homepoint_rsp`

The FC's rejection reasons are enumerated verbatim in the app DEX
(`com/uav/flymodel/generated/api/error/UpdateHomePointError`, in
`unpacked_app_dex/classes_0855200c.dex`). Ordinals and the shipped user-facing strings:

| val | name | app message (translated) |
|---|---|---|
| 0 | `NO_ERROR` | no error |
| 1 | `UNKNOWN_REASON` | "home point update failed, please retry" |
| 2 | `INVALID_GPS_COORIDINATE` | "the new home point's lat/lon is invalid" |
| 3 | `HOME_POINT_NOT_BE_RECORD` | "initial home point is still being recorded, retry shortly" |
| 4 | `GPS_NOT_READY` | "aircraft GPS signal weak, home point update failed" |
| 5 | `DISTANCE_TOO_FAR` | distance too far |
| 6 | `UNKNOWN_ERROR` | — |
| 7 | `UNKNOWN` | — |

This answers the precondition question directly, from the FC's own error vocabulary. The MSDK v4
`FlightControllerAbstraction` (decompiled, un-obfuscated) shows DJI enforces these **client-side
too, before transmitting**, which pins the exact numbers:

1. **An initial home point must already have been recorded** (`HOME_POINT_NOT_BE_RECORD`).
   You cannot rewrite home before the FC has recorded one. Poll the "home recorded" bit (§5)
   and only then issue the rewrite.
2. **The aircraft needs a usable GNSS fix.** For the *current-aircraft-location* variant, MSDK
   fails immediately with `GPS_SIGNAL_WEAK` if `DataOsdGetPushCommon.getGpsLevel() < 4`. So
   **GPS signal level ≥ 4 is required** (FC also enforces via `GPS_NOT_READY`). Gate on OSD GPS
   level ≥ 4 before sending Variant B.
3. **There is a ~30 m distance gate** (`DISTANCE_TOO_FAR`). For the *explicit-coordinate*
   variant, MSDK requires the point be within **30.0 m** (haversine) of one of: the current
   recorded home, the aircraft's current OSD location, or the last-best mobile location — else
   it returns `INVALID_PARAMETER`/`HOME_POINT_TOO_FAR` (SDK error −1005) without transmitting.
   This is DJI's documented rule ("valid if within 30 m of take-off / aircraft / mobile / RC
   location"). If you hand-build the packet you bypass the client check, but the FC still
   enforces its own limit and answers `DISTANCE_TOO_FAR` (5). Probe the FC's actual threshold
   empirically (§9) — it need not equal the app's 30 m.
4. **Also a ±90/±180 range gate** (`INVALID_GPS_COORIDINATE` / SDK `INVALID_PARAMETER`), present
   both client-side (native §2.1) and FC-side.
5. **No control-authority / virtual-stick requirement.** `HomeLocationSet` performs no permission
   check and no `PermissionCheckerHelper` call before building the packet — unlike several other
   FC commands in this binary. Setting home does not require taking control.

`HomeLocationSet` returns `-10` locally, without transmitting, if lat/lon are out of range,
and `-6` if the value/characteristics lookup fails.

## 5. Verification from telemetry

Two push handlers read the *same* response buffer (`[x2+0x28]`, the DUML payload):

### 5.1 `KeyHomeLocationPush` @ VA `0x2B88CA4`

```asm
2b88cbc  ldr  x8, [x2, #0x28]          ; payload
2b88cc0  mov  x9, #0x800000000000
2b88cc4  movk x9, #0x4066, lsl #48     ; 180.0
2b88ccc  ldp  d1, d0, [x8]             ; d1 = payload[0x00], d0 = payload[0x08]
2b88cd4  ldr  d2, [x8, #0xe48]         ; PI
2b88cdc  fmul d0, d0, d3               ; *180
2b88ce0  fmul d1, d1, d3
2b88cec  fdiv d8, d0, d2               ; /PI  -> degrees
2b88cf0  fdiv d9, d1, d2
...
2b88d18  mov  v0.16b, v8.16b           ; arg1 = latitude  <- payload[0x08]
2b88d1c  mov  v1.16b, v9.16b           ; arg2 = longitude <- payload[0x00]
2b88d20  bl   #0x4a16620               ; LocationCoordinate2D(double lat, double lon)
```

### 5.2 `KeyIsHomeLocationSetPush` @ VA `0x2BB6D94`

```asm
2bb6dac  ldr  x8, [x2, #0x28]
2bb6dbc  ldrh w23, [x8, #0x14]         ; u16 at payload +0x14
2bb6de0  and  w1, w23, #1              ; bit 0  -> "is home location set"
```

### 5.3 Resulting layout of the home-point push — **cmd_set `0x03` / cmd_id `0x44`**

The push is `DataOsdGetPushHome`, **FLYC cmd_set `0x03`, cmd_id `0x44` (68)** — resolved
authoritatively from the MSDK and the o-gs dissector (`flyc [0x44] = "OSD Home Point Get"`; also
re-broadcast as HD-Link `0x09`/`0x02`).

```
off    size  type    meaning
0x00     8   f64LE   home LONGITUDE, radians   (DataOsdGetPushHome.getLongitude reads bytes 0..7)
0x08     8   f64LE   home LATITUDE,  radians   (getLatitude reads bytes 8..15, then *180/PI)
0x10     4   f32LE   home altitude (0.1 m)
0x14     2   u16     flags; bit0 = home recorded/set, bit1 = go-home mode, bit3 = dynamic home,
                     bit4 = reached distance limit, bit5 = reached height limit, bit7 = has go-home
0x16     2   u16     go-home height ("fixed altitude")
```

That is the classic `flyc_osd_home_point` record. The `lon, lat, alt(4), flags@0x14` packing is
self-consistent (8+8+4 = 20 = `0x14`) and matches DJI's `DataOsdGetPushHome` getters byte-for-byte:
`getLongitude()` reads bytes 0-7, `getLatitude()` reads bytes 8-15, `isHomeRecord()`/`hasGoHome()`/
`getGoHomeMode()`/`getGoHomeHeight()` read the flag/height fields. The aircraft's *current* position
(for cross-checking Variant B) is in the separate general OSD push, cmd_id `0x43`
(`DataOsdGetPushCommon`), also lon/lat radian doubles.

> **CONFIRMED (independently):** the prior loose note's "OSD `+0x14` bit0 = home recorded" is
> correct — `ldrh [x8,#0x14]` + `and #1` is exactly that.

> **CONTRADICTED:** the prior note's claim that home lat is at `+0x00` and home lon at `+0x08`
> is **backwards**. In the push, **`+0x00` is LONGITUDE and `+0x08` is LATITUDE.** The
> disassembly is unambiguous: `ldp d1, d0, [x8]` puts `payload[0x00]` in `d1`, which becomes
> `d9`, which is passed as the *second* argument (longitude) of
> `LocationCoordinate2D(double latitude, double longitude)`.
>
> Note this is the **opposite order from the SET command**, where `payload[1]` is latitude and
> `payload[9]` is longitude (§2.2). Getting these two mixed up is the single most likely way to
> ship a bug here.

## 6. The `0x22 / 0xCB` claim — resolved

> **CONTRADICTED (partially):** the prior loose note said "dynamic homepoint = cmd_set `0x22`
> id `0xCB`". That command **exists**, but it is **not** the set-home-point command:
>
> ```
> _ZN3uav4core16uav_cmd_base_reqILh1ELh34ELh203E34uav_fc2_RC_DYN_HOMEPOINT_INFO_push
>                                                 38uav_fc2_push_RC_DYN_HOMEPOINT_INFO_rsp
>   → cmd_type 1, cmd_set 34 (0x22), cmd_id 203 (0xCB)
> ```
>
> It is `RC_DYN_HOMEPOINT_INFO`, a **push** carrying the *remote controller's* dynamic home
> point ("follow me"-style home tracking the RC's own GPS), consumed by
> `RcDynamicHomePointSendGPSAction` / `KeyDynamicHomePointStatePush`. It is on the FC2 command
> set, not FLYC, and it does not set the drone's home point to an arbitrary coordinate.
> **It is irrelevant to this feature.** Use `0x03 / 0x31`.

Related keys in the same family, for completeness: `DynamicHomePointEnabled`,
`DynamicHomePointState`, `RcDynamicHomePointExisted`, `RcDynamicHomePointSendGPS`,
`RcDynamicHomePointGPSData` (ctor `C1Elddddddi` → `long, 6×double, int`).

`HomeLocationType` (what the aircraft reports the home source as) enumerates:
`NOT_SET, AIRCRAFT_LOACTON, APP_LOCATION, RC_LOCATION, SDK_LOCATION, DYNAMIC_RC,
DYNAMIC_BEACON, STATIC_BEACON, BACKUP_LOCATION, DRONE_NEST_LOCATION, AUTOPLATFORM_LOCATION,
UNKNOWN` (sic, `AIRCRAFT_LOACTON` is misspelled in DJI's source).
`ConvertHomePointSetTypeToHomeLocationType` @ VA `0x2B78AE4` maps
`UAV_HOME_POINT_SET_TYPE` 1..12 through a jump table at `0x15FA944`:
`{1→2, 2→2, 3→1, 4→0, 5→2, 6→4, 7→5, 8→0xFFFF, 9→0xFFFF, 10→7, 11→8, 12→9}`.

## 7. `setGoHomeHeight` / RTH altitude — while we were in there

`_Z15GoHomeHeightSet...` @ VA `0x2B7C254` does **not** build a dedicated DUML command. It:

1. loads the string `"GoHomeHeightRange"` (rodata `0x140940C`, built as a 17-char libc++ SSO
   string: `strb #0x22` = `len<<1` = 17, then a 16-byte `ldr q0` + `'e'`),
2. looks that range up in the key-value cache to clamp the requested height,
3. writes the value through the **flyc hash-param path**.

So RTH altitude is a *parameter write*, not a command. **Valid range 20–500 m** (MSDK
`setGoHomeHeightInMeters`, relative to takeoff). The relevant WM160 param is
`g_config.go_home.fixed_go_home_altitude_0`, hash **`0x38CC63DC`** (from `params_table.txt`),
written with the generic hash-param write command that also appears in the descriptor dump:

```
uav_cmd_base_req<1, 3, 249, uav_fc_set_write_hash_param_req, uav_fc_set_write_hash_param_rsp>
  → cmd_set 0x03, cmd_id 0xF9 (249)     (read counterpart: cmd_id 0xF8 / 248)
```

`drone.py` already has `set_param()` / `read_param()`, so this needs no new transport work —
just the param name. Related knobs discovered alongside: `g_config.go_home.go_home_method_0`
(`0x97FBC173`), `g_config.go_home.go_home_heading_option_0` (`0x6E280D61`),
`g_config.go_home.avoid_enable_0` (`0x9C044CCA`).

## 8. What to change in our code

### 8.1 `drone.py` — `set_home_point()` is subtly wrong today

Current (lines 245-252):

```python
def set_home_point(self, lat_deg: float, lon_deg: float, home_type: int = 0) -> None:
    lat = math.radians(lat_deg); lon = math.radians(lon_deg)
    self._cmd(0x03, 0x31,
              bytes([home_type]) + struct.pack("<dd", lat, lon) + bytes([0]),
              receiver=DEV_FC)
```

cmd_set/cmd_id/radians/length are all **CONFIRMED (independently)** correct. Two byte values
are not:

- `home_type` defaults to **0**, which per §3 means *"ignore the coordinates, use the aircraft's
  current location."* Calling `set_home_point(lat, lon)` today most likely sets home to the
  drone, silently ignoring the arguments. It must be **`0x02`** for an explicit coordinate.
- trailing byte is `0x00`; the SDK sends **`0x64`**.

Suggested replacement:

```python
# --- home point: FLYC cmd_set 0x03 / cmd_id 0x31, 18-byte payload ---
# payload[0]=0x00 -> use aircraft's current location (lat/lon zeroed)
# payload[0]=0x02 -> use the supplied coordinate
# lat/lon are f64 LE RADIANS at offsets 1 and 9 (unaligned); trailing byte 0x64.
HOME_SET_CURRENT  = 0x00
HOME_SET_EXPLICIT = 0x02

def set_home_point(self, lat_deg: float, lon_deg: float) -> None:
    """Rewrite the home point to an explicit GPS coordinate."""
    if not (-90.0 <= lat_deg <= 90.0):
        raise ValueError(f"latitude out of range: {lat_deg}")
    if not (-180.0 <= lon_deg <= 180.0):
        raise ValueError(f"longitude out of range: {lon_deg}")
    payload = (bytes([self.HOME_SET_EXPLICIT])
               + struct.pack("<dd", math.radians(lat_deg), math.radians(lon_deg))
               + bytes([0x64]))
    assert len(payload) == 18
    self._cmd(0x03, 0x31, payload, receiver=DEV_FC)

def set_home_to_current_location(self) -> None:
    """Rewrite the home point to wherever the aircraft is right now."""
    self._cmd(0x03, 0x31, bytes(18), receiver=DEV_FC)   # 18 zero bytes
```

Response decoding (`uav_fc_set_homepoint_rsp`, first byte is the result code):

```python
HOME_POINT_ERRORS = {
    0: "NO_ERROR", 1: "UNKNOWN_REASON", 2: "INVALID_GPS_COORIDINATE",
    3: "HOME_POINT_NOT_BE_RECORD", 4: "GPS_NOT_READY",
    5: "DISTANCE_TOO_FAR", 6: "UNKNOWN_ERROR", 7: "UNKNOWN",
}
```

Use a **500 ms** ack timeout for the explicit variant and **700 ms** for the current-location
variant, matching the SDK.

### 8.2 `drone.py` — `set_home_to_aircraft()` already exists by another route

Line 175 has `set_home_to_aircraft()` → `_fc_function(0x03)` (FC function-control `HOMEPOINT_NOW`).
That is a *different* command from `0x03/0x31`-with-zero-payload. Both plausibly work on WM160;
the MSDK's own "use current aircraft location" path is the `0x31` one, so prefer
`set_home_to_current_location()` and keep `_fc_function(0x03)` as a documented fallback. Test
both (§9) — the function-control route may not return a structured error code.

### 8.3 `telemetry.py` — `parse_home_location()` has lat/lon swapped

Current (lines 230-234):

```python
def parse_home_location(self, p: bytes) -> None:
    """Home lat/lon push: f64 radians @+0x00/+0x08."""
    self.state.home_lat = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
    self.state.home_lon = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])
```

Per §5.3 this is **inverted**. The push is FLYC cmd_set `0x03` / cmd_id **`0x44`**
(`DataOsdGetPushHome`); recognise it by that id. Should be:

```python
def parse_home_location(self, p: bytes) -> None:
    """OSD Home push (cmd_set 0x03 / cmd_id 0x44): f64 radians LON@0x00, LAT@0x08;
       f32 alt@0x10; u16 flags@0x14 (bit0=recorded); u16 go-home height@0x16."""
    self.state.home_lon = self.rad_to_deg(struct.unpack_from("<d", p, 0)[0])
    self.state.home_lat = self.rad_to_deg(struct.unpack_from("<d", p, 8)[0])
    if len(p) >= 0x14 + 2:
        self.state.home_set = bool(struct.unpack_from("<H", p, 0x14)[0] & 1)
```

Sanity check that will catch the swap immediately in the field: |latitude| can never exceed 90.
If the value at `+0x00` converts to something beyond ±90°, the fields are the other way round.
Add that assertion rather than trusting either this document or the old one.

## 9. HOW TO VERIFY ON HARDWARE

Prerequisites: drone powered, GNSS fix acquired, **home already recorded once** (bit0 of the
u16 at push `+0x14` reads 1). Props off is fine — none of this requires flight.

1. **Baseline.** Run the telemetry stream, log `home_lat`/`home_lon`/`home_set`. Confirm
   `home_set == True` and that the home coordinate is a plausible location near you. Verify the
   |lat| ≤ 90 assertion passes — this validates §5.3 before you rely on it.

2. **Set to an explicit coordinate.** Pick a target ~200 m away (well inside any plausible
   `DISTANCE_TOO_FAR` limit) and send Variant A. Expect a `0x03/0x31` response; decode byte 0
   against the table in §8.1. `0` = accepted.

3. **Confirm the rewrite.** Watch the home push. `home_lat`/`home_lon` must converge to the
   coordinate you sent, within GPS noise. **This is the real test** — the ack only means the
   FC accepted the frame.

4. **Prove the type byte matters** (this is the check that catches the current `drone.py` bug).
   Send the *same* command but with `payload[0] = 0x00` and non-zero lat/lon. Home must snap
   back to the *aircraft's* position and ignore the coordinates. If home instead moves to the
   coordinates, then `0x00` is not "current location" and §3 is wrong — say so loudly.

5. **Set to current aircraft location.** Send 18 zero bytes. Home should become the aircraft's
   present GPS position; cross-check against the aircraft lat/lon from the general OSD push.

6. **Probe the distance limit.** Walk the target coordinate outward (1 km, 5 km, 20 km, 50 km)
   until byte 0 of the response returns `5` (`DISTANCE_TOO_FAR`). Record the threshold — it is
   FC firmware policy and is not discoverable statically. Do this on the ground.

7. **Probe the GPS precondition.** Indoors / with no fix, attempt a set. Expect `4`
   (`GPS_NOT_READY`) or `3` (`HOME_POINT_NOT_BE_RECORD`). This confirms the error decoding path
   end to end and tells you which gate fires first on WM160.

8. **RTH altitude.** Read `g_config.go_home.fixed_go_home_altitude_0` (hash `0x38CC63DC`) via
   the existing `read_param()`, write a new value with `set_param()`, read it back. Only then
   consider an actual RTH test.

Order matters: never fly an RTH test until steps 1-5 show the home point reads back as what you
set. A wrong home point plus RTH is how aircraft are lost.

## 10. Citations

**Native (`libsdk_jni.so`, aarch64, file offset == vaddr):**

| VA | Symbol |
|---|---|
| `0x2A04AD8` | `uav::core::uav_cmd_base_req<1,3,49,uav_fc_set_homepoint_req,uav_fc_set_homepoint_rsp>::ctor` |
| `0x2B88DF8` | `HomeLocationSet(...)` — explicit-coordinate builder |
| `0x2B889D0` | `HomeLocationUsingCurrentAircraftLocationAction(...)` — current-location builder |
| `0x2B8918C` | `HomeLocationWithTypeSet(...)` |
| `0x2B88CA4` | `KeyHomeLocationPush(...)` — home lat/lon read-back |
| `0x2BB6D94` | `KeyIsHomeLocationSetPush(...)` — home-recorded bit |
| `0x2B896C4` | `KeyHomeLocationTypePush(...)` |
| `0x2B78AE4` | `ConvertHomePointSetTypeToHomeLocationType(UAV_HOME_POINT_SET_TYPE)` |
| `0x15FA944` | jump table for the above |
| `0x2B7C254` | `GoHomeHeightSet(...)` |
| `0x140940C` | rodata `"GoHomeHeightRange"` |
| `0x2A13868` | `BaseAbstraction::SendActionPack<set_home_point_req>` vtable thunk |
| `0x29E3624` | `FlightControllerAbstraction::ActionHomeLocationUsingCurrentAircraftLocation` |
| `0x29E3808` | `FlightControllerAbstraction::ActionHomeLocationUsingCurrentRemoteController` |
| `0x201D1C8` | `FlightControllerDiagnosticsHandler::CheckHomePointUpdate` |
| `0x2FD35F0` | `is_set_homepoint` |
| symtab `0x2F8`–`0x1A9128`, strtab `0x2414A0` (+`0x978AB4`) | recovered symbol table, 72,513 syms |

**MSDK v4.18 Java (primary ground truth — `scratchpad/msdk/all/`, `javap -p -c`):**

- `dji/midware/data/model/P3/DataFlycSetHomePoint` — `doPack()` (18-byte layout: type@0, lat
  double@1, lon double@9, interval@17), `start()` (APP→FLYC, CmdSet.FLYC, CmdIdFlyc.SetHomePoint),
  `setHomeType()`, `setGpsInfo(double,double)`.
- `dji/midware/data/model/P3/DataFlycSetHomePoint$HOMETYPE` — `AIRCRAFT=0, RC=1, APP=2, FOLLOW=3`.
- `dji/midware/data/config/P3/CmdSet` — `FLYC` value 3.
- `dji/midware/data/config/P3/CmdIdFlyc$CmdIdType` — `SetHomePoint` value 49 (0x31).
- `dji/midware/data/model/P3/DataOsdGetPushHome` — `getLongitude()` (bytes 0-7), `getLatitude()`
  (bytes 8-15, ×180/π), `isHomeRecord()`, `getGoHomeHeight()`; the verify push (cmd_id 0x44).
- `dji/midware/data/model/P3/DataAppUIOperateSetHome` (+`$HomeSourceFrom`) — General/App-UI
  set-home path (cmd_set 0x00), alternative to FLYC 0x31.
- `dji/midware/data/model/P3/DataFlyc2SetLimitLiftedGoHomeHeight` — limit-lifted RTH altitude.
- `dji/midware/util/dgh.fdd(double)` — double→8-byte-LE encoder (`Double.doubleToLongBits`).
- `FlightControllerAbstraction` (setHomeLocation: 30 m gate + ±90/±180; UsingAircraftCurrentLocation:
  GPS level ≥ 4), `LocationUtils.DegreeToRadian`, `LocationCoordinate2D`.

**Web / dissector (see the web-research sub-report for full URLs):**

- o-gs dji-firmware-tools `comm_dissector/wireshark/dji-dumlv1-flyc.lua` — `[0x31]` set,
  `[0x44]` OSD home push, param-hash table (`fixed_go_home_altitude_0` = `0x38cc63dc`).
- MSDK v4 iOS `DJIFlightController` docs — 30 m home rule, RTH 20-500 m.
- MSDK v5 `FlightControllerKey`: `KeyHomeLocation` (get/set, degrees),
  `KeyHomeLocationUsingCurrentAircraftLocation` (action, empty), `KeyGoHomeHeight` (int, m),
  `KeyIsHomeLocationSet` (bool).

**DEX (`reverse_docs/unpacked_app_dex/`):**

- `classes_0855200c.dex` → `com/uav/flymodel/generated/api/error/UpdateHomePointError` — the
  8-value rejection enum in §4, with DJI's own user-facing strings.
- `classes_0855200c.dex` → `com/uav/flymodel/generated/api/flight/HomeLocationType`,
  `HomeLocationInfo`, `HomeLocation2D`.
- `classes_08fe100c.dex` → `impl/flight/returntohome/ReturnToHomeSettingsModelImpl$homeLocation`,
  `RcDynamicHomePointModelImpl`.

**Project files:** `dji_link_beta/drone.py:245-252` (`set_home_point`), `drone.py:175`
(`set_home_to_aircraft`), `dji_link_beta/telemetry.py:230-234` (`parse_home_location`),
`dji_link_beta/params_table.txt` (go-home param hashes).

## 11. Confidence

- **cmd_set `0x03` / cmd_id `0x31`, 18-byte payload, f64 radians** — very high. Read directly
  out of a template instantiation name *and* independently out of the ctor's immediate stores
  *and* out of two separate payload-assembly routines.
- **`payload[0]`: `0x02` = explicit, `0x00` = current aircraft location** — high. The two
  builders differ in exactly this byte and in whether lat/lon are populated. Step 4 of §9 is
  designed to falsify it if wrong.
- **`payload[17]`** — this is `mInterval`, default `0x64` (100). High on value (confirmed in
  DJI's own `doPack()`), low on *meaning*. Send `0x64`.
- **Push layout lon@`0x00` / lat@`0x08`, recorded bit @`0x14` bit0, cmd_id `0x44`** — very high.
  Confirmed three ways: native register dataflow into a `(lat, lon)` ctor, DJI's
  `DataOsdGetPushHome` getters (lon reads bytes 0-7, lat reads 8-15), and the o-gs dissector.
  Contradicts the prior note's lat/lon order; still worth the |lat| ≤ 90 assertion on first run.

All primary facts are now cross-confirmed across the stripped native (MSDK v5), the un-obfuscated
MSDK v4.18 Java bytecode (DJI's own serializer), and the o-gs Wireshark dissector. There are no
remaining statically-unresolved items in the command or push layout.
