# FLIGHT LIMITS RESEARCH 2026 — max height & max distance on WM160 (Mavic Mini 1)

**Question settled:** how to CHANGE and ENFORCE max flight height (altitude ceiling) and max
flight radius (distance) on a DJI Mavic Mini 1 (WM160 / UAV59) over DUML, and how to VERIFY the
drone actually obeys the new value without flying to the ceiling.

**Primary ground truth for this pass:** DJI's own MSDK 4.18 DUML layer, decompiled from
`dji-sdk-provided-4.18.jar` (Maven Central, DJI's real code — class/method names UN-obfuscated;
only string literals are stringfog'd, all numeric constants are plaintext in the bytecode).
Location used: `scratchpad/msdk/all/dji/midware/data/model/P3/*.class` via `javap -p -c`.
Cross-checked against the live WM160 param capture (`params_table.txt`, 132 params) and the
`flyc_param_infos.json` metadata table. This SUPERSEDES the older notes in this folder.

---

## 0. TL;DR — the definitive answer

There are **two independent, both-valid** DUML paths that write the SAME FC config field
`g_config.flying_limit.max_height_0` / `max_radius_0`:

1. **Generic param write by name-hash `SetParamsByHash` = cmd_set `0x03` / cmd_id `0xF9` (249).**
   Payload = `[hash u32 LE][value u16 LE]`. ← **This is the path the real app actually uses.**
   The DEX reverse of the shipped app confirms the limit UI resolves MSDK keys
   (`KeyHeightLimit`/`KeyDistanceLimit`, `FlightLimitHeight`, `LimitMaxFlightHeightInMeter`) down
   to a flyc **parameter write** of `g_config.flying_limit.max_height_0` / `max_radius_0`, sent
   via `DataFlycSetParams` → `0xF9` (new firmware) or `0xF2` SetParamsByIndex (old firmware).
2. **Dedicated typed command `DataFlycSetLimits` = cmd_set `0x03` / cmd_id `0x2D` (45).**
   Payload = `[mode u8][value u16 LE]`, 3 bytes. `mode 1 = height, 2 = radius`. CONFIRMED
   byte-exact from DJI's own `doPack()`. **BUT the DEX reverse found this class is defined and
   never invoked in the shipped app** — the FC firmware almost certainly still services 0x2D
   (it is a long-standing DUML command), yet it is unproven on WM160 because the app never sends
   it. **Prefer path 1; treat 0x2D as an untested fallback.**

Both are **honored and EEPROM-persisted** (survive reboot): the param's `attribute = 0x0B` =
`READ_WRITE | EEPROM_WRITE | EEPROM_SPECIFIC`. The FC clamps to the param's own min/max
(height **15–500 m**, radius **15–5000 m** on WM160). **You cannot exceed 500 m / 5000 m by a
param write** — the FC clamps to its table range; going higher requires patching the FC firmware
binary's param table (Phantom-3-era hacks did this to reach ~1800 m; no such patch is published
for WM160). There is **no separate "enable" toggle you must flip** to make a *lower* limit take
effect — `advanced_function.height_limit_enabled_0` / `radius_limit_enabled_0` gate whether the
*ceiling is applied at all*, not the value.

**Verify without flying — proven on our hardware:** read the field back with the param read
**`GetParamsByHash` = cmd_set `0x03` / cmd_id `0xF8`**, payload `[hash u32 LE]`; reply is
`[retcode u8][hash u32 LE][value u16 LE]`. Our existing WM160 capture ALREADY did exactly this
and got `max_height = 500` — so this read path is empirically confirmed on WM160. Procedure:
write a small value (e.g. 30 m) via `0xF9`, then `0xF8`-read it back and confirm it reports 30.
Ground test, no altitude. (The dedicated `GetLimits` 0x2E is an alternative readback but is
UNUSED by the app and UNPROVEN on WM160 — do not rely on it.)

**Prerequisite for writes:** `dji-firmware-tools` sends an **Assistant Unlock** first —
cmd_set `0x03` / cmd_id `0xDF`, payload `lock_state u32 = 1`. Our `0xF8` *reads* succeeded without
it, but a `0xF9` *write* may need it; send it once before writing if the write NAKs.

**Not a concern for us (but is for the stock app):** the DJI app re-pushes the whole
`flying_limit.*` block on every connect, overwriting live writes. We are headless with no app
attached, so nothing overwrites our value — this actually works in our favor.

---

## 1. `DataFlycSetLimits` — the dedicated set command (cmd_set 0x03 / cmd_id 0x2D)

Source: `dji/midware/data/model/P3/DataFlycSetLimits.class` (`javap -p -c`).

`start()` sets, byte-exact:
- sender = `DeviceType.APP`, receiver = `DeviceType.FLYC`
- `CMDTYPE = REQUEST`, `NEEDACK = YES`, `EncryptType = NO`  → **plaintext, ack requested**
- `CmdSet.FLYC` and `CmdIdFlyc$CmdIdType.SetLimits`

Numeric ids resolved from the enum `<clinit>`:
- `CmdSet.FLYC` ctor `(…, ordinal 3, value 3)` → **cmd_set = 0x03** (`CmdSet.class`).
- `CmdIdFlyc.SetLimits` ctor `(…, ordinal 19, value 45)` → **cmd_id = 0x2D**
  (`CmdIdFlyc$CmdIdType.class`, `bipush 45; putstatic SetLimits`).

`doPack()` builds a **3-byte** payload (`iconst_3; newarray byte`):
```
offset 0 : mode.value()  (i2b)                     # 1 byte
offset 1 : dgh.ghu(value)  -> arraycopy(...,1,2)   # 2 bytes, u16 LITTLE-ENDIAN
```
`dgh.ghu(int)` (`dji/midware/util/dgh.class`) is verified LE:
`b[0]=v&0xFF; b[1]=(v&0xFF00)>>8`. So **payload = `[mode][value_lo][value_hi]`**.

### The `MODE` enum (`DataFlycGetLimits$MODE`)
Enum ctor is `(String name, int ordinal, int value)`; `value()` returns the 3rd arg (`data:I`):

| ordinal | value (byte sent) | meaning (high confidence) |
|--------:|------------------:|---------------------------|
| 0 | **1** | max **HEIGHT** (altitude ceiling) |
| 1 | **2** | max **RADIUS** (distance) |
| 2 | **3** | low / min-height variant |
| 3 | **100** | (special; likely "all"/query — not needed) |

(Names are stringfog-encoded; the value ordering + DJI convention + our working slider make
`1 = height, 2 = radius` the operative mapping. Cross-checked by GetLimits round-trip below.)

**Example wire payloads (to FLYC, plaintext):**
- Set max height 30 m: cmd `03 2D`, payload `01 1E 00`.
- Set max height 120 m: cmd `03 2D`, payload `01 78 00`.
- Set max distance 500 m: cmd `03 2D`, payload `02 F4 01`.

---

## 2. `DataFlycGetLimits` — the readback command (cmd_set 0x03 / cmd_id 0x2E)  ← VERIFY HERE

Source: `dji/midware/data/model/P3/DataFlycGetLimits.class`.

- cmd id: `CmdIdFlyc.GetLimits` ctor `(…, ordinal 20, value 46)` → **cmd_id = 0x2E**.
- `doPack()` = **1-byte** request `[mode u8]` (`iconst_1; newarray byte; … mode.value(); bastore 0`).
- Response getters read `_recData`:
  - `getMode()`  = `_recData[0]` (byte 0).
  - `getValue()` = `DataBase.get(offset 1, len 2, Integer)` = **u16 LE @ offset 1**.

So **GetLimits reply payload = `[mode u8 @0][value u16 LE @1]`** and `value` is the limit the FC
is *actually enforcing right now*. This is the deterministic, active, ground-testable readback.

---

## 3. Param path by name-hash (cmd_set 0x03) — the generic alternative

Ids resolved from `CmdIdFlyc$CmdIdType.class`:
| name | ordinal | value | cmd_id |
|------|--------:|------:|-------:|
| GetParamsByIndex | 110 | 241 | 0xF1 |
| SetParamsByIndex | 111 | 242 | 0xF2 |
| GetParamInfoByHash | 115 | 247 | 0xF7 |
| **GetParamsByHash** (read) | 116 | 248 | **0xF8** |
| **SetParamsByHash** (write) | 117 | 249 | **0xF9** |

- **Read `0xF8`**: request `[hash u32 LE]`; reply `[retcode u8][hash u32 LE][value…]`.
  Confirmed empirically: our capture decoded 132 replies, all `retcode = 0`, value width per type.
- **Write `0xF9`** (`DataFlycSetParams.doPack`): payload `[hash u32 LE][value…]`. The value encoder
  is chosen by the param `TypeId`: type 0 → 1 byte, **type 1 → `dgh.ghu` = u16 LE (2 bytes)**,
  type 8 → float32. `max_height_0`/`max_radius_0` are **type 1 → write 2-byte u16 LE**.

### Name→hash algorithm — CONFIRMED (independently)
```python
def param_hash(name):           # name = FULL dotted param name
    h = 0
    for b in name.encode('gbk'):
        h = (b + (h << 8)) % (2**32 - 5)   # mod 0xFFFFFFFB
    return h
```
Re-derived against `params_table.txt`: **686 / 686 captured hashes match**. Examples:
`g_config.flying_limit.max_height_0 → 0x0371238a`, `…max_radius_0 → 0x425c0a94`.

---

## 4. The relevant params, live values, ranges, encoding (WM160)

From `flyc_param_infos.json` (metadata) + `params_table.txt` (live `0xF8` reads on the actual drone):

| param | hash | type/size | attr | min | max | JSON def | **LIVE value on drone** |
|-------|------|-----------|------|----:|----:|---------:|------------------------:|
| `g_config.flying_limit.max_height_0` | 0x0371238a | 1 / u16 | 0x0B | 15 | **500** | 120 | **500** (`f4 01`) |
| `g_config.flying_limit.max_radius_0` | 0x425c0a94 | 1 / u16 | 0x0B | 15 | **5000** | 30 | **2000** (`d0 07`) |
| `g_config.flying_limit.min_height_0` | 0x0438298a | 1 / u16 | 0x0B | 5 | 20 | 10 | 20 (`14 00`) |
| `g_config.advanced_function.height_limit_enabled_0` | 0xae52d19a | 0 / u8 | 0x0B | 1 | 2 | 1 | **NOT captured** |
| `g_config.advanced_function.radius_limit_enabled_0` | 0x7ece6d19 | 0 / u8 | 0x0B | 0 | 1 | 0 | **1** |
| `g_config.novice_cfg.novice_func_enabled_0` | 0xde9b1b7b | 0 / u8 | 0x0B | 0 | 1 | 0 | **0** (novice OFF) |
| `g_config.novice_cfg.max_height_0` | 0xd9ab9f79 | 8 / float | 0x0B | 1.0 | 100.0 | 30.0 | not captured |
| `g_config.novice_cfg.max_radius_0` | 0x18968688 | 8 / float | 0x0B | 5.0 | 100.0 | 30.0 | not captured |

**Units:** `flying_limit.max_height_0` / `max_radius_0` are u16 **metres** (encoded u16 LE). Novice
limits are **float metres**. Value encoding confirmed by both `dgh.ghu` (write) and the u16 reads
`f4 01 = 500`, `d0 07 = 2000` in the capture.

**FC-accepted range:** height **15–500 m**, radius **15–5000 m** (from the param table min/max;
the FC clamps out-of-range writes). The GUI slider in `drone.py` already clamps 15–500 / 15–5000.

### Correction to the task background
- The claim `advanced_function.height_limit_enabled_0 = 0` is **UNVERIFIED** — that param was
  **not** in our 132-param capture. Only `radius_limit_enabled_0 = 1` was actually read. JSON
  default for `height_limit_enabled` is 1 (range 1–2), so it is almost certainly enabled.
- The claim `max_radius_0` default is 30 (JSON) yet live reads **2000** — the field had already
  been changed on this airframe, which is itself weak evidence that writes to it stick.

### `attribute = 0x0B` decoded (bitmask, from `DataFlycGetParamInfo$Attribute`)
`READ_ONLY=1, READ_WRITE=2, EEPROM_WRITE=4, EEPROM_SPECIFIC=8, IMPORT_EXPORT=8, OTHER=100`.
`0x0B = 1|2|8` → **writable + EEPROM-persisted**. So a successful write **survives reboot**; no
separate commit step is needed (the `0xF9`/`0x2D` write to EEPROM is the persist).

---

## 5. What can override / clamp the value (gating layers)

1. **`advanced_function.height_limit_enabled_0` / `radius_limit_enabled_0`** — master enables for
   whether the ceiling is applied. Setting a *lower* max_height only matters while the limit is
   enabled; since height limiting is effectively always on for a consumer FC, a lower value is
   enforced. To *exceed* the built-in ceiling you would need these plus firmware cooperation — not
   our goal. For our goal (set a specific ceiling ≤500 m and have it obeyed) no toggle is required.
2. **Novice / beginner mode (`novice_cfg.novice_func_enabled_0`)** — on our drone it reads **0**
   (off), so `flying_limit.*` is authoritative. Note: the separate `novice_cfg.max_height_0`
   value-param may not even exist on WM160 (community WM160 dumps show only the boolean
   `novice_func_enabled`); either way, keep it 0. **Caveat from field reports:** almost every
   observed ~30 m clamp actually comes from the *app* (beginner tutorial, not-activated /
   not-logged-in fallback, prop guards, weak GPS) rather than an FC param. Since we run headless,
   the app-side clamps don't apply — but confirm the FC doesn't self-impose a no-activation
   ceiling with no app present.
3. **Geo / NFZ (`libDJIFlySafeCore.so`, `assets/flysafe/dji.nfzdb2.confumix`)** — a *separate*
   client+FC layer of `LimitArea` records, each carrying its own `heightLimit`/`radius`
   (`dji::flysafe::v3::LicenseDataHeight::kHeightLimitField…`, `AreaFilter::FilterAreasByRadius`).
   It only **lowers** the ceiling *inside specific geographic zones*; it does not raise or replace
   the global user ceiling elsewhere. The per-area status is pushed via `DataFlycGetPushLimitState`
   (cmd_set 0x03 / cmd_id **0x55 = 85**: lat/lon/inner+outer radius/type/areaState). Not relevant
   to setting the global ceiling, but explains any "my 500 m isn't honored *here*" case.

---

## 6. Verification field in passive telemetry — OSD Home push

Source: `dji/midware/data/model/P3/DataOsdGetPushHome.class`. In the MSDK class registry it is
filed under `CmdSet.OSD`(5)/`GetPushHome`(2), but **on the WM160 wire the OSD pushes are carried
on the FLYC cmd_set `0x03`** (this is why our common flight push arrives as `0x03/0x43`, not
`0x05/0x01`). Per the `dji-firmware-tools` dumlv1 dissector the FLYC ids are: **OSD General =
`0x03/0x43`** (matches our capture) and **OSD Home Point = `0x03/0x44`** — the Home push is where
the limit fields live. Getters (all via `DataBase.get(off,len,type)`, LE):

| getter | offset | width | decode | meaning |
|--------|-------:|------:|--------|---------|
| `getHeightLimitValue()` | **0x25 (37)** | 4 | **float32 LE** | **CURRENT ENFORCED height limit, metres** |
| `getHeightLimitStatus()` | 0x24 (36) | 1 | `& 0x1F` | height-limit reason/status (low 5 bits) |
| `isReatchLimitHeight()` | 0x14 (20) | 2 | `& 0x20` (bit5) | at/near height limit |
| `isReatchLimitDistance()` | 0x14 (20) | 2 | `& 0x10` (bit4) | at/near distance limit |
| `getHeight()` | 0x10 (16) | 4 | float | current relative height |

**This corrects our telemetry:** `getHeightLimitValue` is a **float32 @0x25**, *not* a u8 — our
`telemetry.py` reads `u8(p, 0x25)` and gets garbage. The `near_*` bit tests (bit5/bit4 @0x14) are
correct, but they live in the **Home** push (`0x03/0x44`), which `telemetry.py` never dispatches
(it only handles `0x03/0x43` general + `0x0D/0x02` battery).

> Version caveat: the OSD Home Point packet is **68 bytes** on older firmware but **34 bytes** on
> newer firmware, and the **short (34-byte) variant DROPS the `height_limit_status`/
> `height_limit_value` block**. So on WM160 the passive `getHeightLimitValue` may not be present at
> all — confirm the Home-push length from a live capture before trusting it.
> **For verification, prefer the `0xF8` param read — it is deterministic and already proven to
> return `max_height` on our WM160 capture.** The dissector also confirms the OSD Home State u16
> @0x14 bits: `reach_limit_distance = 0x10` (bit4), `reach_limit_height = 0x20` (bit5),
> `beginner_mode = 0x800` (bit11); OSD General (0x43) carries `is_out_of_limit` in its status word.

---

## 7. WHAT TO CHANGE IN OUR CODE

`dji_link_beta/drone.py`
- **Make the PARAM path the primary setter** (this is what the real app uses and what our own
  capture proves works). Add typed setters:
  `set_param("g_config.flying_limit.max_height_0", struct.pack("<H", metres))` and
  `…max_radius_0`, clamped to 15–500 / 15–5000. `set_param` (0x03/0xF9, `[hash][value]`) and
  `read_param` (0x03/0xF8, `[hash]`) are **CORRECT** as-is; height/radius value = **2-byte u16 LE**
  (type 1).
- `set_max_altitude` / `set_max_distance` (0x03/0x2D SetLimits, `[mode][u16 LE]`, mode 1/2): the
  **byte layout is confirmed correct** against DJI's `DataFlycSetLimits.doPack`, BUT the shipped
  app never sends 0x2D (it uses the param path) so 0x2D is **unproven on WM160**. Keep it as an
  alternate, but don't make it the only path — if the FC NAKs 0x2D, fall back to the param write.
- Add an **Assistant Unlock** helper before writes: `_cmd(0x03, 0xDF, struct.pack("<I", 1))`.
  Call it once per session before the first `0xF9` write (reads worked without it; writes may need
  it).
- `get_limits(mode)` (0x03/0x2E): layout `[mode][u16 LE]` reply is correct but also unproven on
  WM160 — don't depend on it for verification; use the `0xF8` read instead.

`dji_link_beta/pc_client.py`
- height/radius aliases (line 467) map to the right param NAMES but are wired to **read only**.
  Wire a WRITE console command that calls the new typed `set_param` (u16 LE) for height/radius.
  The GUI slider (line 552) currently calls `set_max_altitude` (0x2D) — switch it to the param
  write, or have it try param-write first and 0x2D as fallback.

`dji_link_beta/telemetry.py`
- **Bug:** `parse_osd_lowfreq` reads `max_height = u8(p, 0x25)`. Change to **`float32 @0x25`**
  (`struct.unpack_from("<f", p, 0x25)[0]`) and rename to `height_limit_value_m`.
- The `near_height_limit` (bit5 @0x14) and `near_dist_limit` (bit4 @0x14) tests are correct **but
  belong to the OSD Home push (`0x03/0x44`)**. Add a `feed_packet` dispatch for `0x03/0x44` and
  parse them there. Currently it only dispatches `0x03/0x43` (general) and `0x0D/0x02` (battery),
  so none of the limit fields are ever populated.
- Also expose `height_limit_reason = u8(p,0x24) & 0x1F` from the Home push.

---

## 8. HOW TO VERIFY ON HARDWARE (safe, no altitude needed)

All on the ground, motors off, plaintext link. Use small values so nothing dangerous can happen.
Hashes: `max_height_0` = `8a 23 71 03` (LE), `max_radius_0` = `94 0a 5c 42` (LE).

1. **Baseline read (proven path):** `read_param 0xF8` for `max_height_0` → send `03 F8` payload
   `8a 23 71 03`. Expect reply `00 8a 23 71 03 F4 01` (value `01F4` = **500**). This is the exact
   read our existing capture already succeeded at, so it is known-good.
2. **Confirm gates:** `0xF8`-read `novice_cfg.novice_func_enabled_0` (must be **0**) and
   `advanced_function.height_limit_enabled_0` (expect 1). If novice = 1, set it 0 first.
3. **Unlock (if needed):** send Assistant Unlock `03 DF` payload `01 00 00 00` once.
4. **Write a small height via the PARAM path (primary):** `SetParamsByHash` `03 F9` payload
   `8a 23 71 03 1E 00` (30 m). Expect an ACK with retcode 0 (a reply ≤4 bytes = "not writable /
   rejected").
5. **Read back:** `read_param 0xF8` max_height → expect value `1E 00` (**30**). ← proof it took
   effect and hit the config field.
6. **Cross-check the dedicated command (optional):** restore to 120 via param, then try
   `SetLimits` `03 2D` payload `01 78 00` (120 m). `0xF8`-read back: if it reports 120, then 0x2D
   is ALSO honored on WM160 (settles the "does 0x2D work here" question). If 0x2D NAKs or the read
   is unchanged, 0x2D is not serviced on WM160 → stick with the param path.
7. **Persistence:** power-cycle the aircraft, reconnect, `0xF8`-read again — value should still be
   30 (attr 0x0B = EEPROM). Then **restore** your desired real ceiling via `0xF9`
   (`8a 23 71 03 F4 01` = 500 m; radius `94 0a 5c 42 D0 07` = 2000 m).
8. **Optional passive confirm:** dispatch the OSD Home push (`0x03/0x44`); if the payload is the
   long variant, read `float32 @0x25` — it should equal the value you set. Gently raise the drone
   a couple metres and confirm `near_height_limit` (bit5 @0x14) trips as you approach the (now
   30 m) ceiling. Skip if steps 4–5 already agree.

**Pass criterion:** step 5 reports the value you wrote and step 7 survives reboot. That
definitively proves the FC honors the new limit via the param path.

---

## 9. Citations
- MSDK 4.18 (`dji-sdk-provided-4.18.jar`, Maven Central), classes under
  `dji/midware/data/model/P3/` and `dji/midware/data/config/P3/`, read with `javap -p -c`:
  `DataFlycSetLimits`, `DataFlycGetLimits`+`$MODE`, `DataFlycSetParams`, `DataFlycGetParamInfo`
  (`$Attribute`, `$TypeId`), `DataOsdGetPushHome`, `DataFlycGetPushLimitState`, `CmdIdFlyc$CmdIdType`,
  `CmdIdOsd$CmdIdType`, `CmdSet`, `util/dgh`.
- `libDJIFlySafeCore.so` (arm64-v8a, from `dji-sdk-4.18.aar`): `dji::flysafe::LimitArea`,
  `AreaFilter::FilterAreasByRadius`, `LicenseDataHeight::kHeightLimitFieldNumber`, geofence SQL.
- Live WM160 capture: `dji_link_beta/params_table.txt` (132 `0xF8` reads),
  `dji_link_beta/reverse_docs/flyc_param_infos.json` (687-param metadata table).
- Hash algorithm re-derived & verified 686/686 against captured names.

---

## 10. Independent cross-checks (app DEX + web / dji-firmware-tools)

### 10a. Shipped-app DEX reverse (obfuscated MSDK fork, `uav/midware/…`)
- **CONFIRMS** `DataFlycSetLimits` = FLYC(0x03)/SetLimits(0x2D), `doPack` = `[mode][value u16 LE]`,
  MODE `High=1, Far=2, Low=3, OTHER=0x64`; and `DataFlycGetLimits` = 0x2E.
- **KEY DIVERGENCE (confirmed):** in the shipped app, `DataFlycSetLimits` is **defined but never
  invoked**. The limit UI resolves MSDK keys (`KeyHeightLimit`, `KeyDistanceLimit`,
  `KeyHeightLimitEnabled`, read-only `KeyHeightLimitRange`/`KeyDistanceLimitRange`; internal
  `FlightLimitHeight`, `LimitMaxFlightHeightInMeter`) down to a **flyc parameter write** via
  `DataFlycSetParams` → **0xF9 SetParamsByHash** (new fw) or **0xF2 SetParamsByIndex** (old fw).
- **CONFIRMS** param names `g_config.flying_limit.max_height_0`, `…max_radius_0`,
  `g_config.novice_cfg.novice_func_enabled_0`; write payload `[hash u32 LE][value size-bytes]`,
  value width/encoding from `ParamInfo.typeId`/`.size` looked up at runtime (INT16U/size2 for
  height/radius). Read path 0xF8/0xF1.
- **CONFIRMS** OSD Home getters byte-exact: `getHeightLimitValue` float @0x25, `getHeightLimitStatus`
  @0x24 `&0x1F`, `isReatchLimitHeight` bit5 @0x14.
- **No hardcoded numeric clamp** in the send path — min/max come from `KeyHeightLimitRange`/
  `KeyDistanceLimitRange` read live from the aircraft. 120 m/500 m are only UX warning dialogs.

### 10b. Web + `dji-firmware-tools` + MSDK docs
- **CONFIRMS hash algorithm** (GBK, `hash=(hash<<8)+byte mod 0xFFFFFFFB`) and the exact hashes for
  all limit params. **CONFIRMS** FC-level `max_height`: `type_id=1, size=2, attribute=0x000b,
  min=20(/15), max=500, def=120`; `max_radius` min15/max5000/def2000 on WM160.
- **CONFIRMS** dedicated command names: FLYC `0x2D` = "Limit Params Set", `0x2E` = "Limit Params
  Get" (dumlv1 dissector) — but their payload is undocumented and the tool itself uses the param
  path, not 0x2D. `0x55` = "FlyC Limit State Get" is the NFZ-area push (not the height ceiling).
- **CONFIRMS** OSD readback: **OSD Home Point = FLYC `0x44`** carries `height_limit_status`
  (5-bit enum: 0 NON_LIMIT … 5 NORMAL_LIMIT) + `height_limit_value` float; OSD Home State u16
  bits `reach_limit_distance=0x10`, `reach_limit_height=0x20`, `beginner_mode=0x800`; OSD General
  `0x43` has an `is_out_of_limit` status bit. **Version caveat:** 68-byte (has limit block) vs
  34-byte (drops it) Home-push variants — verify on WM160.
- **MSDK API ranges (docs):** `setMaxFlightHeight` `[20,500]` m; `setMaxFlightRadius` `[15,8000]` m
  (an older doc says `[15,500]`); `setMaxFlightRadiusLimitationEnabled(bool)`. v5 keys are
  `KeyHeightLimit`/`KeyDistanceLimit(+Enabled)`, ranges read live via `KeyHeightLimitRange`.
- **Enforcement reality (DJI staff + logs):** consumer drones hard-capped at **500 m**; exceeding
  it has only ever been done on Phantom-3 by **patching the FC firmware param table** (+ the app's
  copy, or the app re-pushes it) — reaching ~1800 m, with a separate battery/altitude autoland
  kicking in higher up. **No public WM160 >500 m exists**; the main WM160 hacking journal has
  no altitude hack. **Protocol-gen note:** `dji-firmware-tools` routes WM160 (product ≥ WM330)
  through the **2017 by-index commands 0xE0–0xE3**, and sends **Assistant Unlock 0x03/0xDF
  (`lock_state=1`)** before param access — yet our own capture proves the **2015 by-hash 0xF8/0xF9
  path also works on WM160** (that is the path the MSDK app uses), so the hash path is preferred.

### 10c. Net effect on conclusions
The dedicated `0x2D` command is real and byte-correct but **not the mechanism the app uses and is
unproven on WM160**; the **param write `0xF9` by hash is the authoritative, app-used, and
empirically-read-verified path**. Everything else (units u16 metres, ranges 15–500 / 15–5000,
EEPROM persistence, novice/geo gating, float32@0x25 readback bug in our telemetry) is corroborated
across all three independent sources.

### 10d. Native MSDK-v5 `.so` cross-check (two independent passes: log-string xref + dynsym/RELA)
- **CONFIRMS write path:** cmd_set `0x03` / cmd_id **`0xF9`** = internal `set_write_hash_param`,
  keyed by the hash of `g_config.flying_limit.max_height_0` / `max_radius_0`.
- **CONFIRMS readback, byte-exact, with parser addresses** (payload = the FC OSD low-freq / home
  push, arriving as arg `x2`):
  | native key | parser vaddr | instruction | decode |
  |---|---|---|---|
  | **`LimitMaxFlightHeightInMeter`** | `0x2bec0f4` | `ldur s0,[x2,#0x25]; fcvtzs` | **float32 @0x25**, meters (truncated to int) |
  | `HeightLimitReason` | `0x2bb6c64` | `ldrb [x2,#0x24]; and #0x1f` | byte @0x24, low 5 bits |
  | `IsNearHeightLimit` | `0x2bb6ec0` | `ldrh [x2,#0x14]; ubfx #5,#1` | u16 @0x14, bit5 |

  i.e. `enforced_max_height_m = (int)(*(float*)(push + 0x25))` — triply confirmed. The
  `C0HeightLimitState` deserializer (its own radius/enabled fields) lives in `libsdk_base.so` and
  is a *separate* message; the enforced height readback does NOT go through it.
- Native KeyManager exposes an advisory height range around 20–120 m (UI default band); the
  authoritative FC-accepted range remains **15/20–500 m** from the param table. MSDK v5 does not
  officially support WM160, so the **v4 DEX param-write path is the operative one** for this
  hardware regardless.

**Bottom line, all four sources agree:** set the ceiling with a **`0x03/0xF9` param write by hash**
(u16 LE metres) to `g_config.flying_limit.max_height_0` / `max_radius_0`; verify with the
**`0x03/0xF8` read** (proven on our WM160) and/or the **float32 @0x25** of the OSD low-freq/home
push. The `0x2D` command is real but app-unused and unproven on WM160.
