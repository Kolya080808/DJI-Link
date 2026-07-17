# PARAM_WRITE_TRUTH.md — how DJI FC params are NAMED, HASHED, WRITTEN, and GATED (WM160)

Ground-truth reverse of DJI Fly v1.21.4 (`unpacked_app_dex/*.dex`, baksmali) plus the
native `hashFromString` in `libGroudStation.so` (`param_hash.py`). Answers the 5 open
questions about writing FC config params over our plaintext `0x03/0xF8` (read),
`0x03/0xF9` (write), `0x03/0xF0` (info-by-index), `0x03/0xF7` (info-by-hash) link.

---

## 1. NAME → HASH convention — FULL dotted name, `_0` suffix included (SOLVED)

**The app hashes the ENTIRE string exactly as it appears in `flyc_param_infos.json`,
including the `g_config.` prefix and the trailing `_0`.** No stripping, no leaf-only form.

Proof against the drone's ground truth for `g_config.flying_limit.max_height_0`:

```
param_hash("g_config.flying_limit.max_height_0") = 0x0371238A   (u32)
  wire bytes, little-endian (as sent in the 0xF8/0xF9 payload) = 8A 23 71 03
```

`8A 23 71 03` is **exactly** the "0x8a237103" the drone reports — the FC value and the
app hash are the SAME number; the only apparent difference was byte order. So:

* Hash string = the full JSON `name` verbatim, GBK-encoded (ASCII for these names).
* Wire field = `struct.pack("<I", param_hash(name))` — 4 bytes little-endian.
* Reading "0x8a237103" as a big-endian hex literal is the trap; on the wire it is the
  LE encoding of `0x0371238a`.

Leaf/stripped forms are wrong (all miss): `flying_limit.max_height_0` → 0x139603bc,
`max_height_0` → 0x20f42fbb, dropping `_0` → 0xf412036c. **Always hash the full
`g_config.<section>.<field>_0`.** (`fc_dark_need_gps_0`, the one you already wrote, is a
*hidden* param not present in the JSON, so its short leaf name IS its full name — that is
why the leaf form worked there; it is not evidence for stripping the g_config params.)

Algorithm (unchanged, from `hashFromString`, `param_hash.py`):
`h = 0; for b in name.encode("gbk"): h = (b + (h<<8)) % (2**32 - 5)`.

---

## 2. WRITE VALIDITY / PERMISSION — per-param `attribute` bit0, NO global write-enable (SOLVED)

**There is no factory/engineering unlock, no "config-write-enable" command, and no
authority/level byte in the write frame.** The dark-no-GPS write proved this: the app
routes it through a confirm dialog only, and the doc note states it is *"not
engineering-mode-gated"*. A write is just `[hash][value]`; nothing precedes it.

**The one thing that makes a valid, readable param silently reject a WRITE is the
per-param `attribute` bitfield** returned in the `0xF0`/`0xF7` info struct. Decoded from
the enum `DataFlycGetParamInfo$Attribute` (`classes_0451d00c.dex`) — the `data` field is
the on-wire value and it is a **bitmask**:

| attribute value | enum name        | bits            | meaning |
|-----------------|------------------|-----------------|---------|
| `0x00` (0)      | `READ_ONLY`      | —               | **readable, WRITE IGNORED** |
| `0x01` (1)      | `READ_WRITE`     | bit0            | writable to **live RAM only** (not persisted) |
| `0x02` (2)      | `EEPROM_WRITE`   | bit1            | persists to EEPROM/flash |
| `0x03` (3)      | `EEPROM_RW`      | bit0\|bit1      | writable **and** persisted |
| `0x04` (4)      | `EEPROM_SPECIFIC`| bit2            | — |
| `0x08` (8)      | `IMPORT_EXPORT`  | bit3            | included in config import/export |
| `0x0B` (11)     | `EEPROM_RW_IE`   | bit0\|bit1\|bit3| writable + persisted + import/export |
| `0x64` (100)    | `OTHER`          | —               | sentinel/unknown |

(`EEPROM_RW = READ_WRITE.value | EEPROM_WRITE.value`; `EEPROM_RW_IE = READ_WRITE |
EEPROM_WRITE | IMPORT_EXPORT` — computed by OR in the enum `<clinit>`, so these ARE bit
flags, confirmed in the smali.)

**Rule for us:**
* `attribute & 0x01 == 0` (value 0 = `READ_ONLY`) → the FC accepts `0xF8` reads but
  **silently drops `0xF9` writes**. This is the exact "reads but ignores write" case.
* `attribute & 0x01 == 1` → writable. If `& 0x02` is also set it survives reboot; if only
  bit0 (value `0x01`, e.g. `wind_speed[*]_0`) the write is live-only and reverts on reset.

Real verified WM160 values (see **`PARAM_TABLE_WM160.md`** for the full captured list):
`max_height_0`, `max_radius_0`, `min_height_0`, `mode_normal_cfg.tilt_atti_range_0`,
`mode_sport_cfg_tilt_atti_range_0`, all present `g_config.control.*` gains, the voltage
protections and `novice_func_enabled_0` all report `attribute = 0x0B` (writable + persisted
+ import/export). READ_ONLY (`0x00`) params — e.g. `global.status`, `default_gps_*`,
`wind_speed[*]_0`, `g_config_airport_limit_cfg_cfg_1860_limit_switch` — accept `0xF8` reads
but never a write.

Second, orthogonal gate — **DUML link encryption** (`UAVEncryManager`): FLYC cmd-set
`0x03` ids `0xF0/0xF7/0xF8/0xF9/0xFA` get `EncryptType=SIMPLE(3)` *only if* the session
negotiated encryption. Our AOA link did not, and `fc_dark_need_gps_0` wrote fine in
**plaintext** (`cmd_type 0x40`); `pc_client.py:981` records that forcing encryption made
the FC drop the frame. So on our link: **send plaintext, do not encrypt.**

---

## 3. COMMIT / SAVE — none needed; `0xF9` is immediate and self-persisting (SOLVED)

`DataFlycSetParams.doPack` emits only `[hash u32 LE][value size-bytes LE]` under cmd_id
`0xF9` (`fd`), ack timeout 1000 ms, 3 retries. **There is no follow-up save/flush/commit
command, and no `0xFA` is sent after a write.** Persistence is decided entirely by the
param's `attribute` bit1 (`EEPROM_WRITE`): when set, the FC writes the value straight to
EEPROM as part of servicing `0xF9`. That is why `fc_dark_need_gps_0` took effect
immediately and stuck — no commit step exists or is required. (`0x03/0xFA`
`DataFlycResetParams` = reset one param to default by `[hash]`; it is a reset, not a save.)

---

## 4. `0x03/0xF0` (and `0x03/0xF7`) PARAM-INFO response layout — decoded (SOLVED)

From `DataFlycGetParamInfo.getInfo()` / `setRange()` (`classes_0451d00c.dex`), which read
the ACK payload via `DataBase.get(offset, size)`. Offsets are 0-based into the response
payload; byte 0 is a leading status/verify byte, the struct starts at offset 1:

```
off  size  field        parse
[0]   1    status/ret   leading byte (0 = OK); NOT parsed into ParamInfo
[1]   2    typeId  u16  -> DataFlycGetParamInfo$TypeId.find()  (value width table below)
[3]   2    size    u16  -> ParamInfo.size = WRITE width in bytes on the wire
[5]   2    attribute u16-> DataFlycGetParamInfo$Attribute.find() (writability, §2)
[7]   4    min          typed as the param's numeric class (read as 4 bytes, cast)
[11]  4    max          typed
[15]  4    default      typed
[19]  ..   name         ASCII/GBK string, offset 0x13 to end of payload
```

* **`index` is NOT in the response** — it is echoed from the request (`[index u16 LE]`).
* **No hash field** in this response. To map name→hash you still hash the name yourself
  (§1); `0xF7` `GetParamInfoByHash` takes `[hash u32 LE]` and returns this same layout.
* min/max/default are always read as 4 raw bytes then reinterpreted per `typeId` (so for a
  size-2 param only the low 2 bytes are meaningful).

`TypeId` enum (`data` = ordinal), giving the value byte-width for `0xF8`/`0xF9`:

| id | name  | width | id | name   | width | id | name   | width |
|----|-------|-------|----|--------|-------|----|--------|-------|
| 0  | INT08U| 1     | 4  | INT08S | 1     | 8  | FLOAT  | 4     |
| 1  | INT16U| 2     | 5  | INT16S | 2     | 9  | DOUBLE | 8     |
| 2  | INT32U| 4     | 6  | INT32S | 4     | 10 | BYTE   | 1     |
| 3  | INT64U| 8     | 7  | INT64S | 8     | 11 | STRING | var   |

e.g. `max_height_0` typeId=1 (INT16U) size=2 → write `struct.pack("<H", value)`;
`horiz_vel_atti_range_0` typeId=5 (INT16S) size=2 → `struct.pack("<h", value)`.
The `0xF8`/`0xF9` payload carries **no per-field type/length byte** — width is implied by
`size` from this info struct / the bundled JSON.

---

## 5. WM160 real param set — CAPTURED (132 valid), speed = tilt/atti-range group (SOLVED)

The bundled `flyc_param_infos.json` is a *merged/generic* flyc table (dji-firmware-tools
style), NOT WM160-specific — many of its 687 entries are absent from Mini 1 firmware, and
WM160 does **not** answer `0x03/0xF0` (get-info-by-index). So the authoritative set was
pulled the other way: **sweep every JSON name by `0x03/0xF8` (read-by-hash) and keep the
hashes that return a value.** Result: **132 of 686 names are live on WM160** — the full
table with hashes, types, access bits, current values and min/max/def is in
**`PARAM_TABLE_WM160.md`** (generated from `params_table.txt`). `horiz_vel_atti_range_0`,
for instance, is in the JSON but **absent on WM160** (its hash returns nothing).

How the app raises max horizontal speed on this platform: **there is no single "max speed"
param and no single mode command.** Speed is bounded by the max attitude/tilt angle. The
verified writable (attr `0x0B`) speed knobs actually present on WM160 are:

| Param | current | min | max | note |
|---|---|---|---|---|
| `g_config.mode_normal_cfg.tilt_atti_range_0` | 20.0 | -360 | 360 | f32, Normal-mode tilt (°) |
| `mode_sport_cfg_tilt_atti_range_0` | 30.0 | 5 | 40 | f32, Sport-mode tilt (°) |
| `mode_sport_cfg_vert_vel_up_0` | 4.0 | 1 | 10 | f32, Sport vertical climb (m/s) |
| `g_config.control.atti_vertical_0` | 100 | 70 | 130 | s16 |

Raising the tilt-range value raises the achievable horizontal speed (writes are immediate,
§3). Note the limits themselves — `max_height_0`=500 (max 500), `max_radius_0`=2000 (max
5000), `min_height_0`=20 — are ALSO writable via `0xF9` here even though the app's UI path
for altitude is the separate `0x03/0x2D` SetLimits command.

---

## TL;DR
1. Hash the **full** `g_config.<section>.<field>_0` string (GBK); send hash **little-endian**
   — `max_height_0` = `0x0371238a` = wire `8A 23 71 03` = drone's "0x8a237103".
2. Writability = `attribute & 0x01` (bit0 READ_WRITE); `attribute==0` (READ_ONLY) params
   accept reads but silently ignore writes. No global unlock/factory/authority gate exists.
3. No commit/save command — `0xF9` is immediate; persistence = `attribute & 0x02`
   (EEPROM_WRITE), handled by the FC during the write.
4. `0xF0` struct: `[0]status [1..2]typeId u16 [3..4]size u16 [5..6]attribute u16 [7..10]min
   [11..14]max [15..18]default [19..]name`; index echoed from request, no hash field.
5. Send param frames **plaintext** on our AOA link (encryption is session-negotiated and off).
