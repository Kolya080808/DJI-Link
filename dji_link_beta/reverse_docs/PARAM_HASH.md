# DJI FC parameter-name hash (`hashFromString`) — reverse-engineering notes

**Goal.** Recover the exact 32-bit hash that DJI uses to address FC config
parameters by hash in DUML `0x03 / 0xF9`, i.e. reverse
`GroudStation.native_hashFromString(byte[])` and reimplement it in Python.

**Result (TL;DR).** The hash is a base-256 polynomial hash over the GBK-encoded
name, seeded at 0, reduced modulo the prime `D = 2**32 - 5 = 4294967291
(0xFFFFFFFB)`:

```
h = 0
for b in name.encode("gbk"):
    h = (b + (h << 8)) % (2**32 - 5)
return h            # already fits in u32 since D < 2**32
```

Implementation: [`../param_hash.py`](../param_hash.py).

---

## 1. Locating the function

The library ships as a git-LFS pointer, so the real ELF was pulled from the
local LFS store:

```
# pointer:  decompiled/lib/arm64-v8a/libGroudStation.so  (130 bytes, LFS)
#   oid sha256:1961660f5d485db1161140875ac1a24d2faed6368a9ae4c162c6d79433ed3bf2  size 36256
cp .git/lfs/objects/19/61/1961660f5d485db1161140875ac1a24d2faed6368a9ae4c162c6d79433ed3bf2 /tmp/libGroudStation.so
file /tmp/libGroudStation.so
#   ELF 64-bit LSB shared object, ARM aarch64, ... stripped
```

The binary keeps a few C++ exported symbols (the JNI methods are wired up by
`RegisterNatives` in `JNI_OnLoad`, mapping Java `native_hashFromString` → the
native `hashFromString`). radare2 symbol dump:

```
r2 -A libGroudStation.so ; is~hash
38  0x00001cd8  GLOBAL FUNC  208  _Z14hashFromStringP7_JNIEnvP8_jobjectP11_jbyteArray
                                     hashFromString(_JNIEnv*, _jobject*, _jbyteArray*)
```

Reproduce the disassembly:

```
r2 -q -A -c 'pdf @ 0x1cd8' libGroudStation.so
# or:
objdump -d --start-address=0x1cd8 --stop-address=0x1da8 -b binary -m aarch64 libGroudStation.so
```

---

## 2. Disassembly evidence (VA 0x1cd8, size 208)

Prologue / JNI plumbing:

```
0x1cd8  stp  x29, x30, [sp, -0x30]!
0x1cec  ldr  x2, [x0]                ; JNIEnv vtable
0x1cf8  ldr  x2, [x2, 0x558]         ; JNINativeInterface->GetArrayLength
0x1cfc  blr  x2                      ; w0 = length
0x1d00  mov  w19, w0                 ; len
0x1d14  ldr  x3, [x3, 0x5c0]         ; JNINativeInterface->GetByteArrayElements
0x1d18  blr  x3                      ; x0 = byte*
0x1d20  cbz  w19, 0x1da0             ; len == 0  -> hash = 0
```

Loop setup:

```
0x1d24  sub  w6, w19, 1
0x1d28  mov  x5, x0                  ; p   = bytes
0x1d2c  add  x6, x6, 1               ; len
0x1d30  mov  x1, 0                   ; hash = 0
0x1d34  add  x6, x0, x6              ; end = bytes + len
```

Per-byte loop body (the algorithm):

```
0x1d38  mov   x0, #0xd                ; \
0x1d3c  ldrb  w4, [x5], #1            ;  |  b = *p++
0x1d40  movk  x0, #0x8000, lsl 16     ;  |
0x1d44  movk  x0, #2,      lsl 32     ;  }  x0 = 0x800000028000000D  = magic M
0x1d48  add   x4, x4, x1, lsl #8      ;  |  t = b + (hash << 8)
0x1d4c  movk  x0, #0x8000, lsl 48     ; /
0x1d50  cmp   x5, x6
0x1d54  umulh x1, x4, x0              ; q_hi = (t * M) >> 64
0x1d58  lsr   x1, x1, #0x1f           ; q    = q_hi >> 31   == floor(t / D)
0x1d5c  lsl   x3, x1, #0x20           ; \
0x1d60  sub   x3, x3, x1, lsl #2      ;  }  x1 = q*(2^32) - q*4 - q = q*(2^32 - 5)
0x1d64  sub   x1, x3, x1              ; /
0x1d68  sub   x1, x4, x1             ; hash = t - q*(2^32-5)  ==  t mod (2^32-5)
0x1d6c  b.ne  0x1d38                 ; while p != end
```

Epilogue: `x19 = x1` (or `0` if empty), `ReleaseByteArrayElements`
(vtable off 0x600, mode 2 = JNI_ABORT), return `w0 = hash`.

### Why this is `mod (2**32 - 5)`

`M = 0x800000028000000D` is the standard compiler magic for unsigned division
by `D = 2**32 - 5`. `umulh` takes the high 64 bits of `t*M` (an implicit `>>64`),
the extra `lsr #31` finishes the shift, giving `q = floor(t / D)`. The three
`lsl/sub` reconstruct `q*D` because `D = 2^32 - 5` (`q<<32` minus `q<<2` minus
`q`), and the final `sub` yields the remainder `t - q*D = t mod D`.

Input range: `t = b + (hash<<8)` with `b < 256`, `hash < D < 2^32`, so
`t < 2^40` — well inside the magic's valid range.

---

## 3. Verification

**Instruction-level (strong).** The exact 64-bit ARM operations of the loop body
were emulated and compared against `t % D` for 200,000 random `(hash, byte)`
pairs — **all matched**, and the full-string emulation matched the plain
`(b + (h<<8)) % (2**32-5)` formula:

| name | hash (dec) | hash (hex) |
|------|-----------:|:-----------|
| `g_config.flying_limit.max_height_0`      |   57746314 | `0x0371238a` |
| `g_config.flying_limit.max_radius_0`      | 1113328276 | `0x425c0a94` |
| `g_config.control.horiz_vel_atti_range_0` | 3725590272 | `0xde0fff00` |
| `g_config.control.horiz_vel_gain_0`       | 2688496919 | `0xa03f3517` |
| `DarkNoGpsLockEnable`                      | 1509653673 | `0x59fb7ca9` |
| `max_height_0`                            |  552873915 | `0x20f42fbb` |
| `max_radius_0`                            | 1608455877 | `0x5fdf16c5` |

**Vector-level (NOT yet done).** No `(name, hash)` ground-truth pair was found
embedded in the `.so` or app resources, so the numbers above are **unverified
against a live wire value**. The algorithm itself is derived directly from the
instructions (not assumed), so confidence in the *algorithm* is high; only the
name→wire mapping is unconfirmed.

To confirm with one live capture: take a single DUML `0x03 / 0xF9`
(config param read/write) frame off USB/AOA where the app touches a known
parameter (e.g. toggle height limit → `g_config.flying_limit.max_height_0`),
read the 32-bit little-endian hash field out of the payload, and check it equals
`param_hash("g_config.flying_limit.max_height_0") = 0x0371238a`. Alternatively,
Frida-hook `hashFromString` (`libGroudStation.so` + `0x1cd8`) and log
`(arg bytes, retval)`.

Caveat on `DarkNoGpsLockEnable`: per `CAMERA_AND_NOGPS.md` /
`INTELLIGENT_AND_PARAMS.md`, this is a **KeyManager-backed setting, not one of
the 687 FC params**, so `0x59fb7ca9` is just the hash of that name string and
may never appear as a `0x03/0xF9` key.

---

## 4. Cross-check against other libraries

* `hashFromString` (mangled `_Z14hashFromString...`) is exported **only** by
  `libGroudStation.so`.
* `libsdk_jni.so` (MSDK JNI; LFS oid `017d65e3...`) does **not** export
  `hashFromString`, and does **not** contain the loop's constant-build sequence
  (`movz x0,#0xd` → `movk 0x8000,lsl16` → `movk 2,lsl32` → `movk 0x8000,lsl48`).
  Instruction search: `movz x0,#0xd` occurs exactly **once** in
  libGroudStation.so (inside this function) and **zero** times in libsdk_jni.so.

So the FC parameter-name hash lives in `libGroudStation.so` alone; it is the
DUML/GroundStation-side helper, distinct from the MSDK key-value machinery.

---

## 5. Confidence

* **Algorithm:** HIGH — read straight from the disassembly and confirmed by
  exhaustive-random ARM-op emulation.
* **Encoding (GBK):** matches the Java caller contract (GBK `byte[]`); for the
  ASCII parameter names in scope GBK == ASCII, so it is not load-bearing here.
* **Live wire mapping (name → 0x03/0xF9 key hash):** UNVERIFIED — needs one
  real capture or Frida hook as described in §3.
