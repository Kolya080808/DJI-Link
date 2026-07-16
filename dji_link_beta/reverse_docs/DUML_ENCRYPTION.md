# DUML "SIMPLE" encryption (cmd_type 0x43) — WM160 FC config frames

Reverse of the encryption the DJI Fly app applies to FLYC config/param DUML frames
(cmd_set `0x03`, cmd_id `0xF0/0xF7/0xF8/0xF9/0xFA`), which is why the FC ignores our
plaintext `0x40` frames. **Verdict up front: this is FULLY DOABLE OFFLINE.** The cipher
is a self-inverse keystream XOR with a **static hardcoded 21-byte key** and the frame's
own sequence number as nonce. No session key, no handshake key-exchange. Every fact below
is cited to smali (baksmali of `unpacked_app_dex/*.dex`) or to the native
`libGroudStation.so` (ARM64, analyzed with radare2/capstone; addresses are file/vaddr).

---

## 0. TL;DR recipe

To turn a plaintext param frame into one the FC accepts:

1. Build the normal DUML frame with `cmd_type = 0x40`, correct `cmd_set/cmd_id/payload`,
   real `seq`, and **valid CRC8 + CRC16** (exactly what `duml.py` already emits).
2. **Encrypt the region `frame[9 : len-2]`** (= `cmd_set` byte, `cmd_id` byte, and the
   whole payload — NOT the 9-byte header, NOT the trailing 2 CRC16 bytes) with the
   `simple_filter()` keystream-XOR below, keyed by the frame's `seq`.
3. Set the encrypt bits: `frame[8] |= 0x03`  → `cmd_type` becomes `0x43`.
4. **Recompute only CRC16** over `frame[0 : len-2]` and rewrite the last 2 bytes.
   (CRC8 at offset 3 covers only bytes 0..2, which never change, so leave it.)

Decryption of FC replies is the *same* `simple_filter()` (self-inverse) using the reply's
own `seq`.

---

## 1. What gets encrypted, and the call chain

### 1a. `UAVEncryManager` (smali, `classes_016b200c.dex`, `uav/midware/data/manager/P3/UAVEncryManager.smali`)

* **`g([B I)[B`** (encrypt, out) and **`f([B I)[B`** (decrypt, in) are **byte-identical**.
  Both do (lines 314–464):
  ```
  v0 = frame.length - 9 - 2                       ; region length
  region = copyOfRange(frame, 9, 9+v0)            ; System.arraycopy from offset 9
  region = GroudStation.native_rcDataDeal(region, p2)   ; p2 = the int arg
  System.arraycopy(region, 0, frame, 9, v0)       ; write back at offset 9
  ```
  ⇒ **encrypted span = `frame[9 .. len-2)`** = `cmd_set`(9) + `cmd_id`(10) + `payload`(11..).
  The header (`0..8`) and CRC16 (`len-2, len-1`) are in cleartext. `f`/`g` being identical
  is the first hint the cipher is a symmetric (self-inverse) keystream, confirmed below.

* **`a([B)V`** (line 117): `frame[8] |= 0x03; ` — sets EncryptType = SIMPLE in `cmd_type`.
* **`b([B)Z`** (line 133): returns `(frame[8] & 0x7) == 3` — "is this frame SIMPLE-encrypted".
* **`c([B)Z`** (line 160): "should this frame be encrypted" — reads `cmd_set=frame[9]`,
  `cmd_id=frame[10]`; returns **true for every cmd_set except** `CmdSet.a`=COMMON(`0x00`),
  and except `CmdSet.c`=CAMERA(`0x02`) with cmd_id ∈ {`0x10,0x11,0x70,0x71`}.
  ⇒ **all FLYC (`0x03`) frames are encrypted** when encryption is on.
* Field `a:Z` (default **true** in `<init>`), `d()` returns it, `e(Z)` sets it — the link-state flag (see §3).

### 1b. Send path — `DataBase.preprocessPack()` (`.../P3/DataBase.smali` line 1895)

Order of operations (lines 1929–2023), the exact recipe:
```
SendPack.c()                      ; build full plaintext frame incl. CRC8+CRC16 -> Pack.r
if (!encryManager.d() && encryManager.c(Pack.r)) {     ; encryption ON and frame is encryptable
    Pack.r = encryManager.g(Pack.r, Pack.i)            ; encrypt region [9..len-2], Pack.i = seq
    Pack.l = EncryptType.SIMPLE.data (=3)              ; low bits of cmd_type
    encryManager.a(Pack.r)                             ; Pack.r[8] |= 0x03
    SendPack.e()                                       ; recompute CRC16 only
}
```
`Pack.i` is the **sequence number**: in `SendPack.c()` (`.../P3/SendPack.smali` line 794–811)
`Pack.i` (or `PackUtil.getSeq()` if 0) is written little-endian to wire offset 6–7 **and** is
the exact int handed to `native_rcDataDeal`. So *encrypt with the same seq you put on the wire.*

`SendPack.e()` (line 1069) recomputes **CRC16 only**: `native_calcCrc16(Pack.r, len-2)` →
overwrite bytes `[len-2],[len-1]`. It does **not** touch CRC8. Confirmed CRC8 stays valid
because it covers only `frame[0..2]`.

---

## 2. The cipher and key — `native_rcDataDeal` in libGroudStation.so

`libGroudStation.so` is ARM64, stripped dynsym but radare2 recovered the symbol table.
`GroudStation.native_rcDataDeal([B I)[B` = **`native_rcDataDeal`** @ **vaddr 0x23bc**.

It (0x23bc–0x24a4): `GetArrayLength`→len, `GetByteArrayElements`→in, `malloc(len)`→out,
then calls the real worker with `(in, len, seq, out, mode=1)`, `NewByteArray`, `SetByteArrayRegion`,
`free`, `ReleaseByteArrayElements`, returns the new array. The worker is reached through a
PLT stub (`0x1a20` → GOT `0x14f78`), whose JUMP_SLOT reloc binds to **dynsym #51 =
`simple_stream_filter` @ vaddr 0x38e0** (confirmed: `r_info` symidx 51, type R_AARCH64_JUMP_SLOT).

> Note: the sibling functions **`tea_encrypt`@0x2574 / `tea_decrpyt`@0x2750 / `key_tea`
> (16 bytes @ 0x15110)** are a DIFFERENT code path — they back `native_encodeData/
> native_decodeData`, NOT the `cmd_type=0x43` frame path. **FLYC config frames use
> `simple_stream_filter`, not TEA.** (This corrects the earlier "key_tea" guess.)

### 2a. `simple_stream_filter(in, len, seq, out, mode)` @ 0x38e0 — exact transcription

* Guard: `if (mode != 1) return 0;` — `native_rcDataDeal` always passes `mode = 1`.
* It is a **byte-wise XOR keystream**, block size = 1, no endianness concerns (byte cipher):
  ```
  out[i] = simple_key[keyidx] ^ in[i] ^ ( (i & 1) ? (seq>>8)&0xff : seq&0xff )
  ```
* `keyidx` starts at **1** (the `mode` value is reused as the initial index) and evolves:
  ```
  before use:   if (keyidx >= 22) keyidx = 0            ; wrap  (cmp #0x16 / csel)
  after byte i: keyidx = ((i+1) & 0xf) ^ (keyidx + 1)   ; and #0xf / add #1 / eor
  ```
* `seq` only contributes its low 16 bits: even byte-positions XOR `seq & 0xff`, odd positions
  XOR `(seq >> 8) & 0xff`. `i` is the index **within the encrypted region** (i=0 ⇒ `cmd_set`).

### 2b. THE KEY (static, hardcoded)

* dynsym #46 **`simple_key`** (an 8-byte pointer @ 0x15128) → relocates to **vaddr 0x4228**
  (rodata). dynsym #7 **`simple_key_len` = 21** (@0x4224).
* Bytes @ 0x4228 (21 bytes): `78 4f 24 33 28 2d 32 40 23 6c 64 2a 76 69 41 51 7e 69 78 46 45`
  = ASCII **`"xO$3(-2@#ld*viAQ~ixFE"`**.
* The wrap threshold is 22 while len is 21, so the code can legitimately read index 21 =
  the string's NUL terminator @0x423d = **`0x00`**. Use a **22-entry table** with index 21 = 0x00.

This key is a build-time constant in the shared library — **not negotiated, not per-session.**

---

## 3. Handshake — there is none you must perform

Receive path `UAVPackManagerBase` (`.../P3/UAVPackManagerBase.smali` lines 1295–1396):
```
if (needCheckEncrypt && enabledSetDataEvent && encryManager.b(frame)) {   ; got a SIMPLE frame
    encryManager.e(false);                        ; flip app into "encryption ON" state
    seq = BytesUtil.M(frame[6..8]);               ; frame's own seq
    frame = encryManager.f(frame, seq);           ; decrypt in place
} else if (encryManager.c(frame)) {               ; got plaintext on an encryptable set
    encryManager.e(true);                          ; flip app "encryption OFF"
}
```
So the app's encrypt flag simply **mirrors what the peer sends** — the moment it receives one
encrypted frame it starts encrypting its own outgoing FLYC frames, and vice-versa. Critically,
`f`/`g` take only `(data, seq)`; **no key is exchanged anywhere** — the key is the static
`simple_key`. Therefore there is **no DUML key-exchange command to replay**: a correctly
`simple_filter`-encrypted `0x43` frame with a valid CRC16 is self-sufficient. The FC will
also encrypt its replies (SIMPLE); decrypt them with the same routine using the reply's seq.

---

## 4. cmd_type math (confirmed)

`cmd_type = (CMDTYPE<<7) | (NEEDACK<<5) | EncryptType` (`SendPack.c()`, offset 8).
`DataConfig$EncryptType` (`classes_016b200c.dex`): **NO=0, DIC=1, OTHER=2, SIMPLE=3** (low 3 bits).

| frame | CMDTYPE | NEEDACK | EncryptType | cmd_type |
|---|---|---|---|---|
| plaintext config request | REQUEST(0) | YES(2) | NO(0) | **0x40** |
| **encrypted config request** | REQUEST(0) | YES(2) | **SIMPLE(3)** | **0x43** |
| encrypted ACK from FC | ACK(1) | YES(2) | SIMPLE(3) | 0xC3 |

`b()` tests `& 0x7 == 3`; `a()` does `|= 0x3`. "SIMPLE" is the only encrypt type used for
FLYC config; DIC/OTHER (1/2) are unused on this path. There is **no AES** on the config path
(AES/white-box libs `libmtmd_crypto`/`libwaes` are for media, not DUML frames).

---

## 5. Complete Python recipe (drop-in for duml.py)

```python
# Static keystream table from libGroudStation.so simple_key @0x4228 (21 bytes) + NUL @index 21.
_SIMPLE_KEY = bytes.fromhex("784f2433282d3240236c642a766941517e69784645") + b"\x00"  # 22 bytes

def simple_filter(buf: bytes, seq: int) -> bytes:
    """Self-inverse DUML 'SIMPLE' cipher (native_rcDataDeal/simple_stream_filter).
    Encrypt == decrypt. `buf` is the region cmd_set+cmd_id+payload; `seq` is the frame seq."""
    out = bytearray(len(buf))
    keyidx = 1
    slo, shi = seq & 0xFF, (seq >> 8) & 0xFF
    for i, b in enumerate(buf):
        if keyidx >= 22:
            keyidx = 0
        out[i] = (_SIMPLE_KEY[keyidx] ^ b ^ (shi if (i & 1) else slo)) & 0xFF
        keyidx = ((i + 1) & 0xF) ^ (keyidx + 1)
    return bytes(out)

def encrypt_frame(frame: bytes) -> bytes:
    """Take a fully-built plaintext DUML frame (cmd_type 0x40, valid CRC8+CRC16) and return
    the SIMPLE-encrypted 0x43 frame the FC accepts. crc8/crc16 from duml.py."""
    f = bytearray(frame)
    seq = f[6] | (f[7] << 8)
    region = simple_filter(bytes(f[9:len(f) - 2]), seq)   # encrypt cmd_set+cmd_id+payload
    f[9:len(f) - 2] = region
    f[8] |= 0x03                                          # cmd_type 0x40 -> 0x43
    crc = crc16(bytes(f[:len(f) - 2]))                    # recompute CRC16 only (CRC8 unchanged)
    f[-2], f[-1] = crc & 0xFF, (crc >> 8) & 0xFF
    return bytes(f)
```

**Worked example — param write `0x03/0xF9`, hash 0x12345678, INT16 value 500, seq 7:**
plaintext region `03 f9 78 56 34 12 f4 01` → encrypted region `4b ca 4d 7e 7c 52 b2 22`.
`simple_filter(encrypted, 7)` returns the plaintext (self-inverse, verified).

**Usage:** build with `DumlPacket(sender=2, receiver=3, cmd_set=3, cmd_id=0xF9, payload=...,
seq=7, cmd_type=0x40).encode()`, then `encrypt_frame(that)`.

---

## 6. Offline-feasibility verdict

**FULLY OFFLINE. No live session key, no handshake to capture.** Everything needed is static:
the 21-byte `simple_key` (+ NUL), the exact keystream recurrence, and the frame's own seq.
`encrypt_frame()` above produces bytes the FC will accept (assuming the earlier premise that
the FC drops plaintext config is correct). The only residual runtime unknowns are *link-level*,
not crypto: (a) whether this particular RC↔FC session has encryption enabled at all (if the FC
also accepts plaintext, no encryption is needed), and (b) the extended `55 CC …` framing wrapper
(`SendPack.d()`), which is orthogonal to this cipher. If a byte-level app capture is ever taken,
diff one encrypted param frame against `encrypt_frame()` to confirm the seq/region boundaries.

### Evidence index (for re-verification)
* `libGroudStation.so` (36256 B ELF; the repo `lib/arm64-v8a/*.so` are git-lfs stubs — a real
  copy was in the analysis scratchpad): `native_rcDataDeal`@0x23bc → PLT 0x1a20/GOT 0x14f78
  → `simple_stream_filter`@0x38e0 (dynsym #51); `simple_key`@0x4228 / `simple_key_len`=21@0x4224;
  unrelated TEA: `tea_encrypt`@0x2574, `key_tea`@0x15110.
* smali (`unpacked_app_dex`): `UAVEncryManager` a/b/c/f/g; `DataBase.preprocessPack`@L1895;
  `SendPack.c`@L423 / `SendPack.e`@L1069; `UAVPackManagerBase`@L1295; `DataConfig$EncryptType`.
