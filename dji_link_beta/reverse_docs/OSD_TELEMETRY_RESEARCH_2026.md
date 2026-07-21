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

<!-- PROGRESS: 35% -->
