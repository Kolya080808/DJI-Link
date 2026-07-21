# Home Point — v2 reverse (fresh pass from DJI's own MSDK bytecode)

Source of truth this pass: baksmali of the shipped app DEX
(`unpacked_app_dex/classes_0451d00c.dex`, `classes_016b200c.dex`), i.e. DJI's
own serializers/getters — not prior notes. Confirmed byte-for-byte.

## 1. READ current home — FLYC `0x03` / cmd_id `0x44` = `DataOsdGetPushHome`

Registration (CmdIdFlyc$CmdIdType field L):
`"GetPushHome"`, ordinal 0x25, **cmd_id 0x44**, model `DataOsdGetPushHome`.
cmd_set = FLYC = `0x03` (CmdSet.d, ordinal 3).

Payload layout, taken directly from DJI's getters (offset, size, class via
`DataBase.get(offset, len, Class)`):

| field | offset | size | type | decode |
|-------|--------|------|------|--------|
| longitude | 0x00 | 8 | f64 | radians; ×180/π → degrees |
| latitude  | 0x08 | 8 | f64 | radians; ×180/π → degrees |
| height (go-home alt) | 0x10 | 4 | f32 | if FlycVersion≥8: ×0.1 m; else raw |
| flags | 0x14 | 2 | u16 | bit0 = isHomeRecord; bit7 = hasGoHome; bits4-6 = goHomeStatus; bit1 = goHomeMode |
| goHomeHeight | 0x16 | 2 | u16 | meters |

Note LON is first (offset 0), LAT second (offset 8). Both little-endian
(BytesUtil stores/reads LE; see §4).

## 2. Aircraft position — FLYC `0x03` / cmd_id `0x43` = `DataOsdGetPushCommon`

SAME lon@0x00 / lat@0x08 f64-radian layout (×180/π). This is the *aircraft's*
live position, not home:
- getLongitude f64@0x00 rad, getLatitude f64@0x08 rad
- getHeight s16@0x10, getGpsLevel @0x20 (>>0x12 &0xF), getGpsNum u8@0x24

## 3. SET home — FLYC `0x03` / cmd_id `0x31` = `DataFlycSetHomePoint`

Registration (CmdIdFlyc$CmdIdType field w): `"SetHomePoint"`, cmd_id **0x31**,
cmd_set FLYC `0x03`. Sender APP → receiver FLYC, CMDTYPE=a (request), needs ACK.

`doPack()` — payload 18 bytes (0x12):

| offset | size | field |
|--------|------|-------|
| 0x00 | 1 | homeType (enum, see below) |
| 0x01 | 8 | latitude  f64 (BytesUtil.x = LE) |
| 0x09 | 8 | longitude f64 (LE) |
| 0x11 | 1 | interval (byte) |

IMPORTANT: SET order is **LAT first (0x01), LON second (0x09)** — the opposite
of the READ push order. Set-side stores the raw double passed to
`setGpsInfo(double lat, double lon)`; no rad↔deg conversion inside doPack, so
caller units matter (see §5, still verifying caller).

HOMETYPE enum (mValue byte):
- AIRCRAFT = 0
- RC = 1  (constructor default)
- APP = 2
- FOLLOW = 3

## 4. Endianness (BytesUtil)

`BytesUtil.x(double)` → `doubleToLongBits` → `A(long)`. `A(long)` writes byte 0
= bits[0..7], byte1 = bits[8..15] … byte7 = bits[56..63] ⇒ **little-endian**.

## 5. The home push ALSO exists on the OSD cmd_set — `0x09`

`CmdSet` enum: FLYC=`0x03`, **OSD=`0x09`**. `CmdIdOsd$CmdIdType`:
- `GetPushCommon` = OSD `0x09` / cmd_id **`0x01`** → `DataOsdGetPushCommon`
- `GetPushHome`   = OSD `0x09` / cmd_id **`0x02`** → `DataOsdGetPushHome`

So the SAME two structs are reachable via **FLYC `0x03/0x43`+`0x03/0x44`** and
via **OSD `0x09/0x01`+`0x09/0x02`**. Identical byte layout either way (same
model classes). A given firmware/link may push on one path or the other; listen
on both.

## 6. RESOLVED — the "800000.0 + serial" anomaly was NOT proof 0x44 isn't home

- `800000.0` (f64) × 180/π = **45 836 623.6** ≈ the "lat==lon ~4.6e7 garbage"
  the old note saw. So the frame really did carry `800000.0` in the two f64
  slots at @0x00/@0x08 — i.e. the offsets were RIGHT; the value was an
  **uninitialised / home-not-yet-recorded sentinel** (no GPS lock, home never
  recorded → flags@0x14 bit0 = 0). It is NOT a coordinate and the existing
  range guard (`-90..90 / -180..180`) already rejects it.
- `DataOsdGetPushHome` has **no ASCII field anywhere** (f64,f64,f32,u16,u16…).
  The ASCII serial `SCCH7A0177DS9` in that dump therefore came from a DIFFERENT
  frame — a COMMON-set device/version frame (`GetVersion`/`GetDeviceInfo`, or
  `GetSerialNum`), not FLYC/OSD `0x44`. The old note conflated two frames.

Conclusion: **`0x03/0x44` (and `0x09/0x02`) IS the home push on WM160.** It just
reads the sentinel until home is recorded. The correct fix is to re-enable the
parse and gate the HUD on `home_recorded` (flags bit0) + range guard — not to
disable 0x44.

## 7. Fixes required (code left for the user to apply)

### telemetry.py
1. `feed_packet`: the `elif` comment block at ~L148-151 wrongly declares 0x44
   "not home". Re-enable dispatch:
   - `pkt.cmd_set == 0x03 and pkt.cmd_id == 0x44` → `parse_home_location(p)`
   - ALSO `pkt.cmd_set == 0x09 and pkt.cmd_id == 0x02` → `parse_home_location(p)`
   - (optionally accept OSD `0x09/0x01` into `_parse_osd`, mirror of `0x03/0x43`)
2. `_parse_osd` (L206-212): it currently writes the AIRCRAFT position (0x43,
   lon@0/lat@8) into `st.home_lat/home_lon`. That is the DRONE's position, not
   home. Route it to `st.drone_lat/drone_lon` instead; let `parse_home_location`
   own `home_lat/home_lon`. (Keeps HUD honest once 0x44 is live.)
3. `parse_aircraft_location` (L295-298) is BYTE-SWAPPED:
   it sets `drone_lat = f64@0x00` and `drone_lon = f64@0x08`, but per DJI's
   getters **@0x00 = LONGITUDE, @0x08 = LATITUDE**. Swap them.
4. `parse_home_location` (L277-293) offsets/units are CORRECT
   (lon@0x00, lat@0x08, rad×180/π, recorded=flags u16@0x14 bit0). Only the
   pessimistic comment about "wrong frame/firmware" should be dropped.

### drone.py
5. `set_home_point` / `set_home_to_current_location` (L280-292): payload order
   is CORRECT and matches `doPack()` exactly — [0]=type, [1..8]=**lat** f64 LE
   rad, [9..16]=**lon** f64 LE rad, [17]=interval. Types: APP=0x02 (explicit),
   AIRCRAFT=0x00 (current). No change needed to structure.
   - Caveat: app's default `mInterval` is **0**; drone.py hard-codes `0x64`.
     doPack just serialises the field with no validation; `mInterval` only
     matters for dynamic/FOLLOW home. For a one-shot APP/AIRCRAFT set it is
     almost certainly ignored, but `0x64` is unverified — 0x00 mirrors the app.

### Preconditions (SET)
- AIRCRAFT (0x00): needs GPS fix (app gates on GPS level; ~≥4 sats/health).
  Equivalent to the FC one-shot `HOMEPOINT_NOW` (`0x03/0x2A` sub `0x03`).
- APP (0x02) explicit coord: FC validates lat∈[-90,90], lon∈[-180,180]
  (native `fcmp` guards, §v1). A distance/`DISTANCE_TOO_FAR` limit vs current
  home was reported in v1 but NOT re-confirmed in this bytecode pass — treat as
  probable, verify on hardware.

<!-- PROGRESS: 100% — READ (0x03/0x44 + 0x09/0x02) + SET (0x03/0x31) fully confirmed from DJI bytecode; anomaly resolved (800000 sentinel, ASCII was a different frame); telemetry.py/drone.py fixes itemised -->
