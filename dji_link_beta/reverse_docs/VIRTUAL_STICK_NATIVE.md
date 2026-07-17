# VIRTUAL STICK — native SDK truth (byte-exact) for WM160 / DJI Fly

Reversed **from the real `libsdk_jni.so`** (git-LFS blob resolved out of
`reversing/.git/lfs/objects/01/7d/017d65e3…`; the checked-in `lib/arm64-v8a/*.so` are 130-byte
LFS pointers — same method as `KEYVALUE_DUML_TRANSPORT.md`). The ELF has corrupt section
headers **but a full 78 419-entry symbol table survives** at file 0x1018 (Elf64_Sym, 24 B each,
`st_value` = VA, `st_name` → STRTAB @ 0x2414a0). First LOAD segment is R-E with **VA == file
offset**, so every VA below is directly disassemblable. Every claim cites a symbol + VA.

---

## 0. VERDICT (read first)

Your reconstruction of the **stick frame is essentially CORRECT** — `cmd_set 0x01`,
`cmd_id 0x0A`, receiver `FLYC 0x03`, payload `TLV1[0x01,0x0D]{…}` + `TLV2[0x55,0x01,0x04]`,
channels packed at bit offsets 8/19/30/41 with bit 62 set, value `round(axis·660+1024)`.
**The framing is not why the drone ignores the sticks.** Two preconditions are missing:

1. **Control authority is never requested.** Before the FC honours app sticks you must send
   **`RequestJoystickControlAuth` = `cmd_set 0x49 / cmd_id 0x80`, payload `[0x01]`** (release =
   `[0x00]`). Response type `uav_sdk_get_or_release_control_auth_ack_t` → it is ACKed. Without
   this the physical RC keeps authority and joystick frames are dropped.
2. **The frame must be repeated at 20 Hz forever.** `VirtualJoyStickHelper::StartTimer`
   schedules `AssemblePack`→send on a **50 ms (0x32) repeating timer**. A one-shot or slow
   send is treated as no input / times out.

Everything else (`AssemblePack`, the scale function, the enable key) is byte-verified below.

---

## 1. The stick command builder — `VirtualJoyStickHelper::AssemblePack()` @ **VA 0x22da6f0**

Symbol: `_ZN3uav3sdk3key21VirtualJoyStickHelper12AssemblePackEv` (1920 B). Its embedded log
string (rodata @0x1482d19) is literally
`"core::special_tlv_cmd_pack uav::sdk::key::VirtualJoyStickHelper::AssemblePack()"` — the pack
type is **`uav::core::special_tlv_cmd_pack`**, and the matching command descriptor symbol is:

```
_ZN3uav4core16uav_cmd_base_reqILh1ELh1ELh10E
     32uav_special_SPECIAL_TLV_CMD_push 36uav_special_push_SPECIAL_TLV_CMD_rsp E
   = uav_cmd_base_req< hasResp=1 , CMD_SET=1 , CMD_ID=10 , … >
```

→ **cmd_set = 0x01, cmd_id = 0x0A.** (`uav_special_SPECIAL_TLV_CMD_push`.)

### 1a. Header bytes written by AssemblePack

```
22da71c mov w8,#0xa ; 22da72c strb w8,[x19,#2]   → obj[2] = cmd_id   = 0x0A
22da720 mov w9,#3   ; 22da730 strb w9,[x19,#4]   → obj[4] = receiver = 0x03  (FLYC)
22da724 mov w10,#9  ; 22da734 strb w10,[x19,#7]  → obj[7] = attr     = 0x09  (internal)
```

**Field-offset map is CALIBRATED, not guessed** — the `<1,73,128>` control-auth ctor
(`@0x2a051c8`) does `mov w8,#0x4901 ; movk w8,#0x280,lsl#16 ; str w8,[obj]` writing
`obj[0]=01 obj[1]=0x49(cmd_set) obj[2]=0x80(cmd_id) obj[3]=02`, and the mobile-RC
`<1,1,2>` command writes `obj[2]=cmd_id`, `obj[4]=receiver(3=FLYC)` even though its
cmd_set is 1 — proving **obj[1]=cmd_set, obj[2]=cmd_id, obj[4]=receiver**. (The old
`KEYVALUE_DUML_TRANSPORT.md` guess "obj[4]=cmd_set" was ambiguous only because FLYC has
cmd_set==receiver==3.) `obj[7]` is an internal attr/flags byte (joystick=9, auth=0x12,
param-write=3); it is **not** the wire cmd_type — see §4.

### 1b. Payload (18 bytes) — built by appending TLVs to obj+0x20

```
TLV1 type   : 0x01
TLV1 length : 0x0D  (13)
TLV1 value  (13 bytes):
   [0..7]  u64 LE = packed channels (see 1c)      stur x9 ,[x29,#-0x10]
   [8..11] u32 LE = 0x00000200                    stur w8(=0x200),[x29,#-8]
   [12]    u8     = 0x06                           sturb w8(=6)  ,[x29,#-4]
TLV2 type   : 0x55
TLV2 length : 0x01
TLV2 value  : 0x04
```

Byte string = `01 0D <8B packed> 00 02 00 00 06 55 01 04`.

### 1c. The 8-byte packed-channel u64 (exact shifts, @0x22da808–0x22da830)

```
bfi  w9 , ch0 , #8  , #0xb     ; bits [18:8]  = ch0
ubfiz w9, ch1 , #0x13,#0xb     ; bits [29:19] = ch1   (<<19)
bfi  x9 , ch2 , #0x1e,#0xb     ; bits [40:30] = ch2   (<<30)
bfi  x9 , ch3 , #0x29,#0xb     ; bits [51:41] = ch3   (<<41)
orr  x9 , x9 , #0x4000000000000000   ; bit 62 = 1   (frame-valid flag)
;  bits [7:0]=0 , bits [61:52]=0 , bit 63=0
```

`packed = (ch0&0x7ff)<<8 | (ch1&0x7ff)<<19 | (ch2&0x7ff)<<30 | (ch3&0x7ff)<<41 | (1<<62)`

Channels are read from `VirtualJoyStickMsg` at **`[msg+0x28],[+0x2c],[+0x30],[+0x34]`** as
four **int32** (`ldp s1,s2 / ldp s3,s4 ; sshll…scvtf`) → ch0,ch1,ch2,ch3 in that positional
order. Positional order matches the RC-report ground truth (`cmd 0x06/0x05`,
ch0=roll ch1=pitch ch2=throttle ch3=yaw), so use **ch0=roll, ch1=pitch, ch2=throttle,
ch3=yaw** (confirm empirically; swap if a hardware test disagrees).

### 1d. Value scale — `ConvertVirtualStickValueToRcStickValue(int)` @ **VA 0x22da164**

Verified constants (`0x408f4000…`=1000.0, `0x4084a000…`=660.0, `0x4090…`=1024.0):

```
out = (int)( (double)in / 1000.0 * 660.0 + 1024.0 )
```

i.e. **`ch = round(axis · 660 + 1024)`** for `axis ∈ [-1,1]` (native uses per-mille int
`in ∈ [-1000,1000]`, `axis = in/1000`). Range **[364 … 1684]**, centre **1024**. The
mobile-RC path (`MobileRCHandler::SendCmd` @0x21a8764) additionally **clamps to
[0x16C=364 … 0x694=1684]** (`csel`), so clamp your output to that window.

---

## 2. ENABLE / control-authority sequence

### 2a. Request control authority (THE missing precondition)

`FlightControllerAbstraction::SendJoystickControlAuthPack(uint8 h)` @ **VA 0x29ea204** builds
the `uav_sdk_get_or_release_control_auth` command:

```
descriptor ctor <1, 73, 128>  →  cmd_set = 0x49 (73) , cmd_id = 0x80 (128)
builder @29ea234:  obj[2]=0x80(cmd_id) , payload = 1 byte = h
callers:
   ActionRequestJoystickControlAuth @0x29ea3f4 :  mov w1,#1  → h = 0x01  (OBTAIN)
   ActionReleaseJoystickControlAuth @0x29ea4f8 :  h = 0x00           (RELEASE)
```

**REQUEST:** `cmd_set 0x49, cmd_id 0x80, payload [0x01]` → expect ACK
(`…control_auth_ack_t`). **RELEASE:** same, payload `[0x00]`. (This is the MSDK "SDK adapter"
command set 0x49; the native `obj[4]` receiver byte is 0x00 for this pack — if receiver 0x00
is not routed on your link, retry with receiver `0x03` FLYC.)

### 2b. Mode key & sender loop

The KeyValue that turns the feature on is **`VirtualStickControlModeEnabled`** (rodata; also
`RequestJoystickControlAuth` / `ReleaseJoystickControlAuth` / `JoystickControlMode` /
`JoystickControlSpeed`). Setting it starts `VirtualJoyStickHelper::StartTimer` @0x22da554,
which arms a **repeating 50 ms (20 Hz)** timer (`mov w2,#0x32 ; mov w3,#1 ; bl <sched>`) that
calls `AssemblePack` and sends. There is **no separate "enter" DUML** beyond the auth request —
enabling == (auth granted) + (streaming §1 frames at 20 Hz). `AbsDidSetup` @0x22da19c also
queries the **`IsSupportVirtualJoyStick`** capability key; if the FC reports unsupported the SDK
falls back to the mobile-RC frame in §5.

---

## 3. Preconditions that make the FC IGNORE correct joystick frames

> **HW status (2026-07-17):** all five preconditions below are now satisfied — authority requested
> to receiver 0x00 **and** 0x03, byte-perfect payload (bit62 + `0x00000200` + `0x06` + `55 01 04`),
> 20 Hz, airborne, correct sender/receiver — and the FC **still does not move**. So the block is
> NOT in this list. RC-authority is specifically ruled out: the working dbaldwin MSDK sample runs
> with the phone plugged INTO the RC (phone→RC→drone), so app sticks are honored *with the RC
> connected*. Remaining suspect = an FC-side joystick/arm STATE the MSDK sets internally (candidate:
> the `g_config.control.control_mode[*]` config, or a mode that surfaces as `FLYC_STATE=JOYSTICK(17)`).
> **Next diagnostic: dump OSD `FLYC_STATE` while streaming — does it ever become JOYSTICK(17)?**

1. **No control authority** → send §2a `0x49/0x80 [0x01]` first and get the ACK. #1 cause.
2. **Not streaming at 20 Hz** → resend the §1 frame every 50 ms; stop = neutralised/timeout.
3. **bit 62 of the packed u64 must be set** (frame-valid) and the `0x00000200` u32 + `0x06`
   byte + `TLV2 55 01 04` trailer must be present verbatim — they are the mode/valid flags.
4. Drone must be **airborne / motors on and out of RTH-homing** (`joystick_invaild_in_homing_push`,
   cmd `0x??` push from FC, marks sticks invalid during auto-homing).
5. Same **sender** device id as your working takeoff/param frames (transport fills it; not in
   the pack object). Keep receiver **0x03 (FLYC)** for the stick frame.

---

## 4. Python-ready builders

```python
import struct

DEV_APP  = 0x02          # your working sender (use whatever your takeoff/param uses)
DEV_FC   = 0x03          # FLYC receiver

def _ch(axis):                       # axis in [-1,1] -> 11-bit RC value
    v = int(round(axis * 660.0 + 1024.0))
    return max(364, min(1684, v)) & 0x7FF

def build_virtual_stick(roll, pitch, throttle, yaw):
    """SPECIAL_TLV_CMD  cmd_set=0x01 cmd_id=0x0A  receiver=FLYC(0x03).
    ch0=roll ch1=pitch ch2=throttle ch3=yaw (positional; matches RC report)."""
    ch0, ch1, ch2, ch3 = _ch(roll), _ch(pitch), _ch(throttle), _ch(yaw)
    packed = ((ch0 << 8) | (ch1 << 19) | (ch2 << 30) | (ch3 << 41) | (1 << 62))
    tlv1_val = struct.pack("<Q", packed) + struct.pack("<I", 0x00000200) + bytes([0x06])
    payload  = bytes([0x01, len(tlv1_val)]) + tlv1_val      # TLV1
    payload += bytes([0x55, 0x01, 0x04])                    # TLV2
    # -> send_duml(cmd_set=0x01, cmd_id=0x0A, receiver=DEV_FC, sender=DEV_APP,
    #              cmd_type=0x40, payload=payload)          # try 0x40 (needack); 0x00 if noisy
    return payload   # 18 bytes: 01 0D <8B> 00 02 00 00 06 55 01 04

def build_request_control_auth(obtain=True):
    """cmd_set=0x49 cmd_id=0x80 ; payload [0x01]=obtain / [0x00]=release."""
    return bytes([0x01 if obtain else 0x00])
    # -> send_duml(cmd_set=0x49, cmd_id=0x80, receiver=DEV_FC, sender=DEV_APP,
    #              cmd_type=0x40, payload=...)  ; expect ACK before streaming sticks
```

### Sequence to fly
1. Takeoff (your working `0x03/0x2A`).
2. `send(build_request_control_auth(True))` → wait for ACK.  *(optional: set KeyValue
   `VirtualStickControlModeEnabled`=1 via `0x03/0xF9` if your stack exposes it.)*
3. **Loop at 20 Hz:** `send(build_virtual_stick(roll,pitch,thr,yaw))` every 50 ms, forever,
   even when centred (send neutral = all axes 0 → all channels 1024).
4. On stop: `send(build_request_control_auth(False))`.

---

## 5. Fallback — mobile-RC virtual-stick frame (`cmd_id 0x02`, different packing)

If `IsSupportVirtualJoyStick` is false for WM160, DJI Fly uses `MobileRCHandler::SendCmd`
@0x21a8764 (`uav_action_virtual_rc_joystick`, `<1,1,2>` → **cmd_set 0x01, cmd_id 0x02**,
receiver FLYC 0x03). Payload 13 bytes, **tight** packing (offsets 0/11/22/33, NOT 8/19/30/41):

```
byte0 = 0x00
[1..8] u64 LE = chA | chB<<11 | chC<<22 | chD<<33      (chA=[cfg+8] chB=[cfg+0]
                                                        chC=[cfg+0xC] chD=[cfg+4])
[9..10] = 0x0000
[11..12] u16 LE = 0x0200 | ((mode & 3) << 10)          (mode byte = [cfg+0x10])
```
Same value scale/clamp (`·660+1024`, [364,1684]). Try §1 first; use this only if §1 is ignored
even after auth is granted.

---

## 6. Symbol / VA index (for re-checking)

| what | symbol | VA |
|---|---|---|
| stick pack builder | `…VirtualJoyStickHelper12AssemblePackEv` | **0x22da6f0** |
| value scale | `…38ConvertVirtualStickValueToRcStickValueEi` | 0x22da164 |
| 20 Hz timer | `…VirtualJoyStickHelper10StartTimerEv` | 0x22da554 |
| setup / capability check | `…VirtualJoyStickHelper11AbsDidSetupEv` | 0x22da19c |
| SPECIAL_TLV descriptor `<1,1,10>` | `…uav_special_SPECIAL_TLV_CMD_push…C2Ev` | (rodata symbol) |
| auth pack builder | `…FlightControllerAbstraction27SendJoystickControlAuthPackEh` | **0x29ea204** |
| auth descriptor `<1,73,128>` | `…uav_sdk_get_or_release_control_auth_t…C2Ev` | 0x2a051c8 |
| request-auth (h=1) | `…32ActionRequestJoystickControlAuth…` | 0x29ea3f4 |
| release-auth (h=0) | `…32ActionReleaseJoystickControlAuth…` | 0x29ea4f8 |
| mobile-RC fallback send | `…MobileRCHandler7SendCmdERKNS1_14JoystickConfigE` | 0x21a8764 |
| mobile-RC descriptor `<1,1,2>` | `…uav_action_virtual_rc_joystick_req…C2Ev` | 0x21a89a4 |
