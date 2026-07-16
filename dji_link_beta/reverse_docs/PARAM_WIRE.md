# PARAM_WIRE.md — exact FC-parameter read/write wire protocol (WM160, DJI Fly v1.21.4)

Traced from the DJI Fly app itself, decompiled from
`reverse_docs/unpacked_app_dex/*.dex` (baksmali). Every frame field below is read
straight out of `DataFlyc*.start()` / `SendPack.c()` — nothing is guessed.

> **BOTTOM LINE (why the drone is silent):** The frame the app puts on the wire for a
> param read/write is **byte-for-byte identical to what the report says we already send**
> (sender `0x02`, receiver `0x03`, `cmd_type 0x40`, cmd_set `0x03`, cmd_id `0xF0/0xF8/0xF9`).
> `0x02` is **APP** and it is the exact sender the real app uses — it is **not** the bug.
> The zero-response is therefore **not** a frame-construction error; the differentiators are
> runtime/link-state (encryption / framing / correct hash), all named in §6 with Frida hooks.

---

## 0. Device / cmd-set / cmd-id constants (cited)

`uav/midware/data/config/P3/DeviceType` (`classes_016b200c.dex`), field `data` = wire id,
returned by `value()`:

| DeviceType | wire id | | DeviceType | wire id |
|---|---|---|---|---|
| CAMERA | `0x01` | | RC | `0x06` |
| **APP** | **`0x02`** | | WIFI | `0x07` |
| **FLYC** | **`0x03`** | | PC | `0x0a` |
| CENTER | `0x05` | | OSD | `0x0e` |

`CmdSet` (`016b200c`): COMMON=`0x00`, SPECIAL=`0x01`, CAMERA=`0x02`, **FLYC=`0x03`** (`CmdSet.d`).

`CmdIdFlyc$CmdIdType` (`016b200c`), field `data` = wire cmd_id (the small number before it is
just the enum ordinal — ignore it):

| symbol | name | cmd_id | class that uses it |
|---|---|---|---|
| `lb` | GetParamInfoByIndex | **`0xF0`** | `DataFlycGetParamInfo` |
| `ac` | SetParamsByIndex | `0xF2` | `DataFlycSetParams` (legacy, dead) |
| `Rc` | GetParamInfoByHash | **`0xF7`** | `DataFlycGetParamInfoByHash` |
| `ad` | GetParamsByHash | **`0xF8`** | `DataFlycGetParams` (read VALUE) |
| `fd` | SetParamsByHash | **`0xF9`** | `DataFlycSetParams` (write VALUE) |
| `id` | ResetParamsByHash | `0xFA` | `DataFlycResetParams` |

`isNew()` is hard-coded `true` (`UAVFlycParamInfoManager`), so the app **always** uses the
by-hash ids (`0xF7/0xF8/0xF9/0xFA`); the by-index `0xF2` write branch is never taken.

---

## 1. The cmd_type byte — decoded from `SendPack.c()` (THE thing to get right)

`SendPack.c()` (`classes_0451d00c.dex`) builds the header byte at frame offset **8** as:

```
cmd_type = (Pack.j << 7) | (Pack.k << 5) | Pack.l
             ^CMDTYPE        ^NEEDACK        ^EncryptType
```

Enum `data` values (`DataConfig$CMDTYPE/$NEEDACK/$EncryptType`, `016b200c`):

| field | app uses | `.c()` data | contributes |
|---|---|---|---|
| `Pack.j` CMDTYPE | `a` = **REQUEST** | `0` | bit7 = 0 (request, not ack) |
| `Pack.k` NEEDACK | `a` = **YES** | **`2`** | bits6-5 = `0b10` |
| `Pack.l` EncryptType | `a` = **NO** | `0` | bits4-0 = 0 |

⇒ **`cmd_type = (0<<7)|(2<<5)|0 = 0x40`** for every param request (read *and* write).
The response the FC sends back is an ACK: bit7=1 ⇒ `cmd_type = 0xC0` (CMDTYPE `ACK`.c()=1).
(NEEDACK `YES`=2, `NO`=0, `YES_BY_PUSH`=1 — note `YES`≠0.)

## 1b. Full standard DUML frame the app emits (`SendPack.c()`, `Pack.<init>`)

`Pack.a = 0x55` (SOF), version `Pack.b = 1`, seq auto-assigned by `PackUtil.getSeq()` if 0,
CRC8 via `GroudStation.native_calcCrc8` over bytes[0..2], CRC16 via `native_calcCrc16` over
the whole frame minus the 2 CRC bytes:

```
off:  0    1        2                     3     4     5     6   7    8       9     10     11..     N-2 N-1
byte: 55  LENlo  (LENhi&0x3)|(ver<<2)   CRC8  SND   RCV  SEQlo SEQhi CMDTYPE CMDSET CMDID  PAYLOAD  CRC16lo CRC16hi
      55  ..     0x04                   ..    0x02  0x03 ..    ..    0x40    0x03   0xF8   ....     ..      ..
```
* `SND` = `(Pack.e<<5)|Pack.f` = `(0<<5)|APP` = **`0x02`** (index nibble 0).
* `RCV` = `(Pack.g<<5)|Pack.h` = `(0<<5)|FLYC` = **`0x03`** (`receiverID` default `-1` ⇒ `g`
  untouched ⇒ 0; `DataBase.<init>`).
* `LEN` = total frame length incl. 13-byte header + payload + 2 CRC16, in the low 10 bits;
  version(=1) in the high 6 bits of the 16-bit little-endian field at [1..2].

(There is also an *extended* 8-byte-prefixed variant — `55 CC x_lo x_hi len32` — emitted when
`SendPack.d()` = `UAVLinkDaemonService...UAVServiceInterface.a()` returns true; see §6.)

---

## 2. READ a parameter VALUE — `0x03 / 0xF8`, request/response (NOT a push subscription)

Class `DataFlycGetParams` (`classes_0451d00c.dex`), the only value-reader the app uses.
Its single caller is `uav/logic/mc/UAVMcHelper.h()` (`classes_03a5700c.dex`).

* **`start()`**: sender=`APP(0x02)`, receiver=`FLYC(0x03)`, CMDTYPE=`REQUEST`, NEEDACK=`YES`,
  Encrypt=`NO`, cmd_set=`FLYC(0x03)`, cmd_id=`ad(0xF8)` (isNew branch).
* **`doPack()`** (isNew): `_sendData` = the param hashes back-to-back, **no count/length prefix**:
  ```
  payload = [hash0 u32 LE][hash1 u32 LE] ... [hashK u32 LE]     (BytesUtil.o0 = 4-byte LE)
  ```
  For a single param the payload is exactly **4 bytes** = that one hash, little-endian.
* **Response** (`setRecData`, isNew): ACK `0x03/0xF8`, FLYC→APP, payload is the mirror layout
  `[hash u32 LE][value (ParamInfo.size bytes, LE)]` per requested param; the app matches each
  returned hash (`BytesUtil.i0`) against `UAVFlycParamInfoManager.readByHash`.

**Param METADATA (type/size/range/name), if you want it from the drone:**
* `0x03/0xF0` `DataFlycGetParamInfo` — request `[index u16 LE]`; response
  `[1..2]typeId u16 [3..4]size u16 [5..6]attr u16 [7..10]min [11..14]max [15..18]def
  [19..]name` — **no hash field**.
* `0x03/0xF7` `DataFlycGetParamInfoByHash` — request `[hash u32 LE]`, same response layout.

**These are plain request→ACK exchanges.** The `DataFlycGetPushParams` / `…ByHash` / `…ByIndex`
classes are **passive push RECEIVERS** (`setPushRecData`, empty `doPack`, no request-sending
`start()`); they only parse *unsolicited* param pushes the FC may emit (e.g. after a write).
**You do NOT have to send a subscribe to read.**

---

## 3. PRECONDITION / handshake — none on the drone, only app-side sequencing

`UAVMcHelper.h()` gates its param read on:
1. `UAVFlycParamInfoManager.isInited()` — local JSON param table loaded (app-side only), and
2. `DataOsdGetPushCommon.getInstance().isGetted()` — the app has already received at least one
   OSD/telemetry push (`0x03/0x43`).

Both are **app-internal** ordering (wait for telemetry, then read); **neither is a drone-side
"enter config" / control-auth / subscribe handshake.** There is no `0x03/0xFx` "start push" the
app must send before `0xF8` answers. Telemetry flowing on our hardware already satisfies (2).

---

## 4. WRITE a parameter — `0x03 / 0xF9`

Class `DataFlycSetParams` (`classes_0451d00c.dex`).

* **`start()`**: sender=`APP(0x02)`, receiver=`FLYC(0x03)`, CMDTYPE=`REQUEST`, NEEDACK=`YES`,
  Encrypt=`NO`, cmd_set=`FLYC(0x03)`; isNew ⇒ cmd_id=`fd(0xF9)`; also `SendPack.u=1000`
  (ack timeout ms), `SendPack.v=3` (retries). ⇒ **cmd_type byte = `0x40`**, expects ACK.
* **`doPack()`** (isNew, `DataFlycSetParams$1` type switch): for each `(name,value)`
  ```
  [hash u32 LE] [ value : ParamInfo.size bytes, little-endian ]
  ```
  Value encoding per `DataFlycGetParamInfo$TypeId` (= JSON `typeID`):
  `0 INT08U(1) · 1 INT16U(2) · 2 INT32U(4) · 3 INT64U(8) · 4 INT08S(1) · 5 INT16S(2) ·
   6 INT32S(4) · 7 INT64S(8) · 8 FLOAT(4) · 9 DOUBLE(8) · 10 BYTE · 11 STRING`.
  There is **no** per-field type/length byte on the wire — length is implied by the param's
  `size` from the bundled `flyc_param_infos.json`.
* No response body needed beyond the ACK; retries 3× at 1 s.
* `0x03/0xFA` `DataFlycResetParams` resets a param to default by `[hash u32 LE]`.

---

## 5. Which sender must WE use — and does the FC only answer the RC?

The app talks to the FC as **APP = `0x02`** from every one of these classes and it works in
production. So **the FC answers sender `0x02`** for params; it is **not** RC-only, and our
`sender=0x02 → receiver=0x03` addressing is correct. (Do not switch to `0x06`/RC or `0x0a`/PC.)

---

## 6. So why is the drone silent? — runtime differentiators (Frida-decidable)

Since the constructed frame matches the app's exactly, the failure is one of:

1. **DUML link ENCRYPTION** (`UAVEncryManager`, `016b200c`). Default **OFF**
   (`a=true` ⇒ `d()=true` ⇒ `preprocessPack` skips encryption). BUT it flips **ON** when the
   receive path sees an encryption-handshake frame (`UAVPackManagerBase` … `encryManager.b()`
   → `e(false)`). Once on, `UAVEncryManager.c()` returns **true for every cmd_set `0x03`
   (FLYC)** frame (COMMON `0x00` is exempt; CAMERA `0x02` exempt only for ids `0x10/0x11/0x70/0x71`),
   so the app encrypts the payload and sets `EncryptType=SIMPLE(3)` → the `cmd_type` low bits
   become non-zero (`0x43`). If the RC/drone negotiated encryption for this session, it will
   **silently drop our plaintext `0x40` param frames.**
   → **Frida:** hook `uav.midware.data.manager.P3.UAVEncryManager.d()` (is it false at param
   time?), `.c([B)` and `.g([B I)`; and dump `SendPack`’s final `Pack.r` in `DataBase.start` to
   see whether the real app’s bytes are encrypted.
2. **Extended vs standard framing** (`SendPack.d()` = `UAVServiceInterface.a()`): true ⇒ the app
   prepends the 8-byte `55 CC …len32` wrapper before the inner `0x55` DUML frame. If our link
   expects the wrapped form (or vice-versa) the FC won’t parse param frames — even though a
   coincidentally-tolerated command path might.
   → **Frida:** hook `SendPack.c()` return / `DataBase.start(SendPack,cb)` and hexdump `Pack.r`.
3. **Wrong hash** (silent no-op, not an error): the 4-byte key must be
   `param_hash(name) = polynomial base-256 mod (2^32-5)` over GBK bytes (see `PARAM_HASH.md`,
   `param_hash.py`). A hash that matches no param yields no value in the ACK.

**Single most decisive capture:** Frida-hook `DataBase.start(SendPack, UAVDataCallBack)` (or the
USB/AOA writer) while the app opens Settings and reads/writes a param (e.g. move the Max-Altitude
slider), hexdump the outgoing `Pack.r`, and diff it against our frame byte-for-byte. That
immediately shows whether encryption/extended-framing is in play.

---

## 7. Step-by-step recipe (Python-implementable)

**Read `g_config.flying_limit.max_height_0` (or `flying_limit.max_height`):**
1. `h = param_hash("g_config.flying_limit.max_height_0")` (u32; `param_hash.py`).
2. Build DUML: SOF `0x55`, ver `1`, sender `0x02`, receiver `0x03`, fresh seq,
   `cmd_type=0x40`, `cmd_set=0x03`, `cmd_id=0xF8`, payload = `struct.pack("<I", h)` (4 B),
   CRC8 over first 3 bytes + CRC16 over all-but-last-2 (DJI polynomials).
3. **Expect:** ACK `cmd_type=0xC0`, sender `0x03`→receiver `0x02`, same seq, `cmd_set=0x03`,
   `cmd_id=0xF8`, payload `[hash u32 LE][value size-bytes LE]` (size=2, INT16S for max_height).
   Silence ⇒ go to §6 (encryption/framing capture).

**Write it to e.g. 500 m:**
1. Same `h`. Payload = `struct.pack("<I", h) + struct.pack("<h", 500)` (INT16S, size 2).
2. `cmd_id=0xF9`, `cmd_type=0x40`, receiver `0x03`; expect ACK `0xC0/0x03/0xF9`; retry ≤3 @1 s.

**Enumerate names (optional, gives names+ranges, NOT hashes):** loop `cmd_id=0xF0`,
payload `[index u16 LE]`, index `0..686`.
