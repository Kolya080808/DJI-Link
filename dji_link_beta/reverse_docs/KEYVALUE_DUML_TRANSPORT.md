# KEYVALUE → DUML TRANSPORT — the exact wire path for `DarkNoGpsLockEnable = FALSE`

Reversed **from the real native SDK binaries** (git-LFS blobs resolved out of
`reversing/.git/lfs/objects/` — the checked-in `lib/arm64-v8a/*.so` are 130-byte LFS
pointers). Primary target: `libsdk_jni.so` (MSDK-v5 JNI core, 80 MB, AArch64, `VA == file
offset` in the first R-E LOAD segment). Cross-checked against `libGroudStation.so`.
Every claim cites a string offset / disassembly VA.

---

## 0. VERDICT (read first)

**The app does NOT use a separate "KeyValue set-by-key-id" DUML command. It uses the
hashed flyc-param write — `cmd_set 0x03 / cmd_id 0xF9` — exactly the transport our Python
already uses.** The KeyValue key name `DarkNoGpsLockEnable` is resolved *inside the native
SDK* to the flyc config-parameter name **`fc_dark_need_gps_0`**, that name string is hashed
with the **same** base-256 polynomial `mod (2^32-5)` as `libGroudStation.hashFromString`,
and the result is shipped as `uav_fc_set_write_hash_param`. So:

* Transport question (a) vs (b): **(a) — hashed flyc param `0x03/0xF9`.** There is no
  key-id-based KeyValue frame for config params on the wire.
* Our frame identity (cmd_set/cmd_id/hash/name/value) is **already correct.**
* The remaining differentiators are **link-state**, not framing: **(1) encryption is
  CONDITIONAL** (the app only SIMPLE-encrypts these when the session negotiated it — our
  own *plaintext* `0x03/0x80` works, so our link is almost certainly NOT encrypted, meaning
  our forced `encrypt_config=True` is likely what makes the FC drop our frames), and **(2)
  the app learns the param's byte-width from the drone via `0xF7` before writing** (the
  param is not in the bundled `flyc_param_infos.json`).

---

## 1. The transport is the templated FC "hash param" command family (decisive)

The MSDK core defines every DUML command as
`uav::core::uav_cmd_base_req<hasResp, CMD_SET, CMD_ID, req_struct, rsp_struct>`.
The 2nd/3rd template args are literally `cmd_set` and `cmd_id` (verified: camera cmds =
`<1,2,…>`, general/COMMON = `<1,0,…>`, gimbal = `<1,4,…>`). The **flightcontroller config**
family (`cmd_set = 3 = FLYC`), pulled from the RTTI/type-name strings in `libsdk_jni.so`:

| type-name string | template `<h,set,id>` | cmd_set | cmd_id | meaning |
|---|---|---|---|---|
| `uav_fc_get_get_cfg_item_info_by_hash_req` | `<1,3,247>` | **0x03** | **0xF7** | get param TYPE/SIZE/attr by hash |
| `uav_fc_read_hash_param_req`               | `<1,3,248>` | **0x03** | **0xF8** | read param VALUE by hash |
| `uav_fc_set_write_hash_param_req`          | `<1,3,249>` | **0x03** | **0xF9** | **WRITE param VALUE by hash** |
| `uav_fc_set_reset_cfg_item_by_hash_req`    | `<1,3,250>` | **0x03** | **0xFA** | reset param by hash |
| `uav_fc_reset_cfg_item_req`                | `<1,3,243>` | **0x03** | **0xF3** | reset param by index |
| `uav_fc_get_product_config_req`            | `<1,3,175>` | **0x03** | **0xAF** | product config |

These are the same opcodes documented in `PARAM_WIRE.md` — confirming the MSDK-v5 KeyValue
path and the legacy `DataFlyc*` path converge on the identical FC command set. **There is
no `<*, ComponentType=4, …>` "set key by component id" DUML** — the component/subcomponent
ints from the Java KeyValue layer are used only to route to the right native *abstraction*,
which then emits the flyc hash-param command.

---

## 2. `DarkNoGpsLockEnable` → `fc_dark_need_gps_0` binding (in the native registry)

Both strings live in `libsdk_jni.so`:

* `"DarkNoGpsLockEnable"` @ file/VA **0x134caaa** — inside the FlightController-abstraction
  key-name list (siblings: `NormalModeThrottleExpRange`, `FCIsInDisplayMode`,
  `[FlightControllerAbstraction]…` @ 0x134cad0).
* `"fc_dark_need_gps_0"` @ file/VA **0x138689b** — inside the flyc `g_config` param-name
  table (siblings: `g_config.mode_sport_cfg.rc_scale_0` @0x1386817,
  `g_config.flying_limit.roof_limit_enable_0` @0x138683a, `gnss_source_mode_0` @0x1386905).
  Note: **no `g_config.` prefix** on this one — the literal hashed string is
  `fc_dark_need_gps_0` (like siblings `RC_STOP_MOTOR_TYPE_0`, `gnss_source_mode_0`).

The registration routine at **VA 0x29db9c0–0x29dbcf0** constructs, into one descriptor
record, both the KeyValue name and the param name:

```
029dbc2c adrp x10,#0x134c000 ; }
029dbc30 add  x10,x10,#0xaaa  ; } x10 = &"DarkNoGpsLockEnable"
029dbc38 ldr  q0,[x10]        ; load the key-name string
...
029dbc48 adrp x9,#0x1386000   ; }
029dbc54 add  x9,x9,#0x89b     ; } x9  = &"fc_dark_need_gps_0"
029dbc68 ldr  q0,[x9]         ; load the param-name string  → stored into same record
```

At runtime this record populates `uav::sdk::FCConfigHelper`'s
`unordered_map<std::string /*param-name*/, ConfigKeyInfo>`. A KeyValue `set(DarkNoGpsLockEnable,
bool)` is routed to `FCConfigHelper`, which looks up `ConfigKeyInfo` for `fc_dark_need_gps_0`
and emits the `0xF9` write below.

---

## 3. The `0xF9` WRITE builder — byte-exact (VA 0x2b72670)

`uav_fc_set_write_hash_param_req` builder. Header + name-hash + value append:

```
02b726a0 mov   w8,#0xf9         ; cmd_id  = 0xF9
02b726a4 mov   w9,#3            ; cmd_set = 0x03 (FLYC)
02b726a8 sturb w8,[x29,#-0x2e]  ; hdr[2] = cmd_id
02b726ac sturb w9,[x29,#-0x29]  ; hdr[7] = encrypt-type field  (see §5)
02b726b0 sturb w9,[x29,#-0x2c]  ; hdr[4] = cmd_set
      ; ---- name hash: h = (b + (h<<8)) mod (2^32-5), over the null-terminated name ----
02b726c4 mov   x24,#-0x7fff7fff7fff8000
02b726c8 movk  x24,#0xd
02b726cc movk  x24,#2,lsl#32    ; x24 = 0x800000028000000D  (div-magic for D = 2^32-5)
02b726d0 mov   w25,#-5          ; w25→x25 = 0x00000000FFFFFFFB = 2^32-5 = D
02b726e8 ldrb  w9,[x8,#0x20]!   ; b = *p
02b72704 bfi   x9,x8,#8,#0x38   ; t = b | (h<<8)
02b72708 umulh x8,x9,x24        ; }
02b7270c lsr   x8,x8,#0x1f      ; } q = floor(t / D)
02b72710 msub  x8,x8,x25,x9     ; h = t - q*D  = t mod (2^32-5)
02b72714 ldrb  w9,[x10],#1      ; next byte; loop while != 0  (C-string)
      ; ---- append [hash u32 LE] then [value] ----
02b72730 str   w8,[sp,#0x60]    ; the 4-byte hash
02b72738 mov   w2,#4            ; append 4 bytes (hash, little-endian)
02b72744 ldr   x1,[x26,#0x40]   ; value pointer  (from ConfigKeyInfo)
02b72748 ldr   w2,[x26,#0x3c]   ; value LENGTH   (from ConfigKeyInfo)  ← width is per-param
02b72750 bl    #0x4a154d0       ; append value bytes
```

* **Hash algorithm = identical to `libGroudStation.hashFromString`** (`PARAM_HASH.md`):
  `h=0; for b in name: h=(b+(h<<8)) % (2**32-5)`. Verified instruction-for-instruction here
  (`libsdk_jni` has 36 sites building the same `0x800000028000000D` magic; the READ/INFO/WRITE
  builders all use it). So there is **no second hash function** — our Python hash is correct.
* **`hash("fc_dark_need_gps_0") = 0x7cb89194`** (GBK==ASCII here).
* **Payload layout (per param):** `[hash u32 LE][value : ConfigKeyInfo.size bytes, LE]`,
  concatenated for a batch, **no count/length prefix** (matches legacy `DataFlycSetParams`).
* **Value width is NOT hardcoded** — it is `ConfigKeyInfo.size` (offset 0x3c). For the
  boolean key `DarkNoGpsLockEnable` this is **1 byte**; unlock value = **0x00 (FALSE)**.

The READ builder (VA 0x2b71c40, `cmd_id 0xF8`) and the INFO builder (VA 0x2b722f0,
`cmd_id 0xF7`) are structurally identical (same hash loop, `mov w8,#0xf8` / `#0xf7`,
`mov w9,#3`, append `[hash]`). The app issues **0xF7 first** to fetch the param's
type/size (because `fc_dark_need_gps_0` is *not* in the bundled `res/raw/flyc_param_infos`
JSON — that table has 687 params and does not include it), then writes with 0xF9.

---

## 4. Sender / receiver / cmd_type

* **sender = APP = 0x02**, **receiver = FLYC = 0x03** (same as `PARAM_WIRE.md`; the FC
  answers sender 0x02 in production).
* **cmd_type**: `REQUEST | NEEDACK` = **0x40** when plaintext, **0x43** when SIMPLE-encrypted
  (low 3 bits = EncryptType SIMPLE = 3). The response is an ACK `0xC0`/`0xC3`.

---

## 5. Encryption — CONDITIONAL, not mandatory

The header byte written at `hdr[7]` (the EncryptType field) is initialised to `3`
(SIMPLE) but is **conditionally rewritten** at the end of the builder:

```
02b727a0 and  w8,w24,w22       ; w22/w24 = per-call encrypt flags
02b727a4 mvn  w8,w8
02b727a8 tst  w8,#0xff
02b727ac b.eq …                ; skip
02b727b0 sturb w22,[x29,#-0x29] ; conditionally overwrite hdr[7] (encrypt-type)
```

i.e. encryption of the FC config frame is gated by a runtime flag, matching
`DUML_ENCRYPTION.md` (`UAVEncryManager.d()` = link-state: encryption stays OFF until an
encryption-handshake frame flips it ON; when ON, *every* cmd_set-0x03 frame is
SIMPLE-encrypted). **Consequence for us:** whether to encrypt depends on our link, not on
the command. Since our **plaintext `0x03/0x80` ground-station command is accepted on our
link** (virtual-stick flight works), our link is **not** in encrypted mode → we should send
the param write **plaintext (`cmd_type 0x40`)**. Forcing SIMPLE-encryption
(`encrypt_config=True`) on an unencrypted link makes the FC silently drop the frame — the
most likely cause of "no effect / reads never answer".

The SIMPLE cipher itself (if the link *is* encrypted): XOR-keystream over `frame[9:len-2]`
keyed by `seq`, then `frame[8]|=0x03`, recompute CRC16 — see `DUML_ENCRYPTION.md`.

---

## 6. General recipe: KeyValue `set(key,value)` → DUML (port any config key)

For any FLIGHTCONTROLLER config-backed key (unlock / limits / novice / etc.):

1. Resolve the key name → its flyc **config-param name** (the `*_0` snake_case string the
   native registry pairs with it; e.g. `DarkNoGpsLockEnable → fc_dark_need_gps_0`,
   `max height → g_config.flying_limit.max_height_0`).
2. `h = param_hash(config_name)` — base-256 poly `mod (2^32-5)` over the bytes
   (`param_hash.py`, byte-identical to the native SDK).
3. (Optional but what the app does) send **`0xF7`** `[h]` → read back
   `typeID/size/attribute` to know the value width, min/max, and confirm the param exists.
4. **WRITE:** `cmd_set 0x03`, `cmd_id 0xF9`, payload `struct.pack("<I",h) + value[size LE]`,
   sender 0x02 → receiver 0x03, `cmd_type 0x40` (plaintext) or `0x43` (SIMPLE, only if link
   encrypted). Expect ACK `0xC0/0x03/0xF9`. Batch = concat `[h_i][val_i]` (no count prefix).
5. **READ back:** `cmd_id 0xF8`, payload `[h]` → ACK payload `[h][value size-bytes]`.
6. Reset to default: `cmd_id 0xFA`, payload `[h]`.

---

## 7. Concrete Python — clear the dark / no-GPS lock

`hash("fc_dark_need_gps_0") = 0x7cb89194`, value FALSE = `0x00`, width 1 byte.

```python
DEV_APP, DEV_FC = 0x02, 0x03
h = 0x7cb89194                       # == param_hash("fc_dark_need_gps_0")
payload = struct.pack("<I", h) + b"\x00"   # [hash u32 LE][value=0/FALSE, 1 byte]

# 1) (recommended) probe size/existence first — matches the app:
#    cmd_set=0x03, cmd_id=0xF7, payload=struct.pack("<I", h)  -> expect ACK with type/size
# 2) WRITE the unlock (send PLAINTEXT first on our link):
send_duml(cmd_set=0x03, cmd_id=0xF9, payload=payload,
          sender=DEV_APP, receiver=DEV_FC, cmd_type=0x40, encrypt=False)  # try 0x40 first
# 3) verify:
#    cmd_set=0x03, cmd_id=0xF8, payload=struct.pack("<I", h)  -> ACK payload [hash][value]
```

**Order of things to try (frame is already correct — this is link-state tuning):**
1. **Turn OFF `encrypt_config`** and send the `0xF9` write **plaintext `0x40`**
   (our plaintext `0x03/0x80` already works → link is unencrypted).
2. If still silent, first send **`0xF7 [hash]`**; if it answers you have the real `size` —
   use that width for the value (still probably 1). If `0xF7` also stays silent while
   plaintext `0x03/0x80` works, the block is upstream of the param subsystem (framing /
   control-authority), not the hash.
3. Only if the link turns out to be encrypted (plaintext `0xF7`/`0xF8` silent but other
   evidence of encryption) switch to `cmd_type 0x43` + `simple_filter` per `DUML_ENCRYPTION.md`.

**Polarity reminder (from `DARK_NOGPS_TRUTH.md`):** unlock = write **FALSE / 0** (the app's
"Unlock" writes `DarkNoGpsLockEnable = FALSE`). Writing 1 re-arms the lock.
