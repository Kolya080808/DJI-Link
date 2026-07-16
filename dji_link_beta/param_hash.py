#!/usr/bin/env python3
"""
DJI FC parameter-name hash (a.k.a. ``hashFromString``).

Reverse-engineered from the JNI native ``GroudStation.native_hashFromString(byte[])``
which dispatches to the exported C++ function::

    hashFromString(_JNIEnv*, _jobject*, _jbyteArray*)
    mangled: _Z14hashFromStringP7_JNIEnvP8_jobjectP11_jbyteArray

in ``decompiled/lib/arm64-v8a/libGroudStation.so`` (ARM64 ELF, stripped;
the checked-in file is a git-LFS pointer, real object under
``.git/lfs/objects/19/61/1961660f...``). Function VA = 0x1cd8, size 208 bytes.

Java feeds the parameter name encoded as GBK (ASCII for names such as
``g_config.flying_limit.max_height_0``). The returned u32 is used as the key
in DUML ``0x03 / 0xF9`` FC config-parameter reads/writes (address-by-hash).

--------------------------------------------------------------------------
Algorithm derived from the disassembly (loop body 0x1d38..0x1d6c)
--------------------------------------------------------------------------
The native code:
  * gets the byte[] length (JNIEnv->GetArrayLength, vtable off 0x558),
  * pins the bytes  (JNIEnv->GetByteArrayElements, vtable off 0x5c0),
  * runs, for each byte, accumulator starting at 0:

        0x1d38  mov   x0, #0xd
        0x1d3c  ldrb  w4, [x5], #1          ; b = *p++
        0x1d40  movk  x0, #0x8000, lsl 16
        0x1d44  movk  x0, #2,      lsl 32
        0x1d48  add   x4, x4, x1, lsl #8     ; t = b + (hash << 8)
        0x1d4c  movk  x0, #0x8000, lsl 48    ; x0 = 0x800000028000000D  (magic M)
        0x1d54  umulh x1, x4, x0             ; q_hi = (t * M) >> 64
        0x1d58  lsr   x1, x1, #0x1f          ; q    = q_hi >> 31  == floor(t / (2^32-5))
        0x1d5c  lsl   x3, x1, #0x20          ; \
        0x1d60  sub   x3, x3, x1, lsl #2     ;  } x1 = q * (2^32 - 5)
        0x1d64  sub   x1, x3, x1             ; /
        0x1d68  sub   x1, x4, x1             ; hash = t - q*(2^32-5)  == t mod (2^32-5)
        0x1d6c  b.ne  0x1d38

The magic constant 0x800000028000000D together with ``umulh``, ``lsr #31`` and the
``q*(2^32-4) - q  ->  q*(2^32-5)`` reconstruction is the compiler's exact
unsigned-division-by-constant lowering of a modulo by the prime

        D = 2**32 - 5 = 4294967291 = 0xFFFFFFFB

(Confirmed by exhaustive-random emulation of the exact 64-bit ARM ops:
``t - floor(t/D)*D`` == ``t % D`` for all sampled (hash, byte); see PARAM_HASH.md.)

So the whole function is a base-256 polynomial hash reduced modulo the prime
2**32 - 5, seeded at 0, over the GBK bytes; empty input hashes to 0. Because
D < 2**32 the result is already a u32.

    hash = 0
    for b in gbk_bytes:
        hash = (b + (hash << 8)) % (2**32 - 5)
"""

MOD = (1 << 32) - 5  # 4294967291 == 0xFFFFFFFB, the prime used at libGroudStation.so:0x1d4c


def param_hash(name: str) -> int:
    """Return the DJI FC 32-bit parameter-name hash for *name*.

    The name is GBK-encoded (matching the Java side, which passes a GBK byte[]),
    then reduced by the base-256 mod-(2**32-5) polynomial hash reversed from
    ``hashFromString`` in libGroudStation.so. Returns an unsigned 32-bit int.
    """
    h = 0
    for b in name.encode("gbk"):
        h = (b + (h << 8)) % MOD
    return h


def param_hash_bytes(raw: bytes) -> int:
    """Same hash over already-encoded bytes (what the native code actually sees)."""
    h = 0
    for b in raw:
        h = (b + (h << 8)) % MOD
    return h


if __name__ == "__main__":
    examples = [
        # flight limits (INTELLIGENT_AND_PARAMS.md / FLIGHT_GATING.md)
        "g_config.flying_limit.max_height_0",
        "g_config.flying_limit.max_radius_0",
        # max horizontal speed / tilt (INTELLIGENT_AND_PARAMS.md lines 322-326)
        "g_config.control.horiz_vel_atti_range_0",
        "g_config.control.horiz_vel_gain_0",
        # dark / no-GPS lock key-value name (CAMERA_AND_NOGPS.md).
        # NOTE: per the docs this is a KeyManager setting, NOT one of the 687 FC
        # params, so this is the hash of the *name string* only -- unverified as a
        # live 0x03/0xF9 key.
        "DarkNoGpsLockEnable",
        # short/leaf forms
        "max_height_0",
        "max_radius_0",
    ]
    width = max(len(n) for n in examples)
    for name in examples:
        h = param_hash(name)
        print(f"{name:<{width}}  {h:>10}  0x{h:08x}")
