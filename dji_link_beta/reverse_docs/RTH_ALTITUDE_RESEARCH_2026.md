# RTH / Go-Home Altitude on WM160 (Mavic Mini 1) — Fresh Research 2026

## Verified param-table facts (PARAM_TABLE_WM160.md + flyc_param_infos.json)

- Name: `g_config.go_home.fixed_go_home_altitude_0`
- Index: 212
- typeID: 1  -> unsigned int, **size = 2 bytes (u16)**
- attribute: 11 (0b1011) -> bit0=1 writable (RW), EEPROM-backed
- min = 20, max = 500, default = 20 (units: metres)
- name-hash (DJI param hash): `0x38cc63dc`

So width is **u16 little-endian, in metres**, range 20..500. `struct.pack("<H", m)` matches.

(continuing: verify app-side write path, hash algo, read-back, dedicated DUML cmd)

---

## App-side reversing (DEX + baksmali, 2026-07-22)

Tooling: `baksmali d` on the split DEX in `unpacked_app_dex/`. The apktool `decompiled/smali/`
tree only holds the `com.*` package; the DUML transport classes live under `uav.midware.*`
and are only in the DEX (disassembled to /tmp for this pass). `fixed_go_home_altitude` appears
in code **only** as the resource `decompiled/res/raw/flyc_param_infos` (the param table itself),
never as a hard-coded command — consistent with a generic param write.

### 1. WRITE path — generic hash-param write, NO dedicated go-home command  ✔

`uav/midware/data/model/P3/DataFlycSetParams` builds the payload as:
- if `UAVFlycParamInfoManager.isNew()` (new-style FW, WM160): writes the **param hash**
  via `BytesUtil.o0(J)` = 4-byte **little-endian** (verified: byte0 = hash & 0xFF, so
  `0x38cc63dc` → `dc 63 cc 38`); else writes the 2-byte index via `n0(I)`.
- then the value, packed by `typeId.ordinal()` then `System.arraycopy(..., ParamInfo.size)`.
  typeId 1 (unsigned int) → `BytesUtil.z(I)` = 4-byte LE int, trimmed to `size = 2` → the
  low 2 bytes = exactly `struct.pack("<H", metres)`.

cmd_set/cmd_id (resolved from enums):
- `CmdSet.d` = "FLYC", ordinal `data = 0x3` → **cmd_set 0x03**.
- `CmdIdFlyc$CmdIdType`: constructor `(name, id, data)` stores `data` as `value()`. Relevant:
  - `fd` = "SetParamsByHash", **0xF9** (write by hash)  ← used by DataFlycSetParams (new FW)
  - `ac` = "SetParamsByIndex", 0xF2 (legacy index write)
  - `ad` = "GetParamsByHash", **0xF8** (read by hash)   ← used by DataFlycGetParams (new FW)
  - `rb` = "GetParamsByIndex", 0xF1 (legacy index read)
  - `Rc` = "GetParamInfoByHash", 0xF7 (metadata: type/attr/min/max/default)

Grep of `CmdIdFlyc$CmdIdType` for GoHome/Height/Altitude command names → **only**
`GetPushGoHomeCountDown` (telemetry). There is **no** `DataFlycSetGoHomeAltitude` /
dedicated go-home-height command for WM160. This matches the native lib:
`_Z15GoHomeHeightSet...` @ VA 0x2B7C254 (see HOME_POINT_RESEARCH_2026.md §7) clamps against
`"GoHomeHeightRange"` from the KV cache and writes through the flyc hash-param path — no command.

### 2. READ-BACK path — 0x03 / 0xF8 by hash  ✔

`DataFlycGetParams.setRecData([B])` parses the reply for new-style FW with base offset `p1 = 5`:
- reads the echoed hash at `p1-4 = 1` via `BytesUtil.i0([B], off)` (u32 LE), compares to the
  requested `ParamInfo.hash`;
- reads the value at offset `p1 = 5` for `ParamInfo.size` (=2) bytes, LE, via `DataBase.get`;
- stride to next entry = `size + 4` (new FW) / `size + 2` (legacy).

So reply layout = **`[retcode u8][hash u32 LE][value u16 LE]`** — exactly as expected.
Read request payload = the 4-byte LE hash (`dc 63 cc 38`).

### 3. Hash reproducibility  ✔

`param_hash("g_config.go_home.fixed_go_home_altitude_0")` = **0x38cc63dc** — matches the table,
so the hash is algorithmically reproducible (GBK base-256 poly mod 2^32-5), not just copied.
Leaf/partial names do NOT match (must use the full dotted name).

### 4. Telemetry read-back of the set value  ✔ (for HW verification)

`DataOsdGetPushHome.getGoHomeHeight()` reads a **u16 at offset 0x16** (size=2, as Integer) of the
home push (cmd_set 0x03 / cmd_id **0x44**, `DataOsdGetPushHome`). After a successful param write,
this field should report back the metres we wrote — good HW cross-check. (Note: 0x16 here is the
offset inside the *home* push 0x44; do not confuse with vz@0x16 in the *common* push 0x43.)

### 5. Web cross-check  ✔

DJI docs / MSDK confirm RTH (go-home) altitude range **20 m .. max flight altitude (500 m)**,
relative to takeoff — matches the param min=20/max=500. MSDK getter/setter is
`getGoHomeHeightInMeters` / `setGoHomeHeightInMeters`.

## CONCLUSION

- **`set_rth_altitude` is CORRECT as-is**: write param hash `0x38cc63dc` via cmd_set 0x03 /
  cmd_id 0xF9, value = `struct.pack("<H", clamp(metres, 20, 500))` (u16 LE, metres). The
  low-2-byte trim of the app's 4-byte int-pack equals a straight u16 pack, so no change needed.
- **Read-back**: cmd_set 0x03 / cmd_id 0xF8, request = 4-byte LE hash; reply =
  `[retcode u8][hash u32 LE][value u16 LE]`. Live cross-check via home-push (0x03/0x44)
  `goHomeHeight` u16 @ 0x16.
- **Dedicated command**: none. WM160 uses only the generic hash param write/read.

<!-- PROGRESS: 100% — write 0x03/0xF9 by hash 0x38cc63dc (u16 LE metres), read 0x03/0xF8, telemetry echo 0x03/0x44 @0x16; set_rth_altitude correct, no dedicated cmd -->
