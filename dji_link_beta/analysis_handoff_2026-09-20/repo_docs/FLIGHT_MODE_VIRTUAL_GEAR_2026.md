# WM160 flight-mode switching (Cine / Normal / Sport): gear in the virtual-RC stream

Status 2026-09-19: **for WM160, superseded by `FLIGHT_MODE_SOFTSWITCH_FIX_2026.md`.**
Sending gear through 0x01/0x02 was a useful fallback experiment, but the bench log showed no
change in gear or FLYC_STATE even while authority was held. It remains here as research history;
`drone.py` no longer uses it in `set_flight_mode`.

The earlier standalone-RC conclusion was incomplete: DJI Fly v1.21.4's native
`OPR59RCAbstraction::SetSoftSwitchMode` path for UAV59 builds `0x06/0x59` addressed to FLYC.
The corrected command is described in `FLIGHT_MODE_SOFTSWITCH_FIX_2026.md`.

The tilt and speed research in `FLIGHT_MODE_SPEED_RESEARCH_2026.md` still defines the maximum
horizontal speed and is not a mode switch.

## Historical virtual-RC experiment

The original experiment assumed the switchless Mini's flight mode was the **RC gear channel** the FC samples from the RC
stream: `g_config.control.control_mode[0..2]` = 12/8/7 (live WM160 values,
`PARAM_TABLE_WM160.md`) maps gear position 0/1/2 onto the pre-stored config blocks. The wire
values are the app's own `RcSoftSwitchMode` ordinals: **SPORT = 0, POSITION = 1, TRIPOD = 2**
(dex `classes_0451d00c`, `uav/sdk/keyvalue/value/remotecontroller/RcSoftSwitchMode`).

The old hypothesis said the RC exposed **no** app command to set the gear (§2), so the gear rides INSIDE the
virtual-RC stick frame — the mobile-RC fallback `uav_action_virtual_rc_joystick`
(**cmd_set 0x01, cmd_id 0x02**, receiver FLYC 0x03, 13-byte payload) carries it in
**byte[12] bits 2-3** (`[11..12] u16 LE = 0x0200 | ((mode & 3) << 10)`). Switching a mode =
a short burst (30 frames × 50 ms ≈ 1.5 s) of centered-stick frames with the target gear —
emulating a physical switch flip. Confirmation = the OSD `FLYC_STATE` leaving the Normal
codes (Sport 31, Cine 19 / Tripod 38).

## 1) Why the mode field is byte-exact (three independent sources agree)

1. **App Java** — `uav/midware/data/model/P3/DataFlycSetJoyStickParams.doPack`
   (dex `classes_0451d00c`): payload 13 B; `[0]` flags (menu/playback/record);
   `[1..10]` 7 packed RC channels; `[11]` button/symbol/change; `[12]` =
   shutter b0 | focus b1 | **mode_sw b2-3** | transform_sw b4 | gohome b6.
2. **Native SDK** — `MobileRCHandler::SendCmd` @0x21a8764 (`uav_action_virtual_rc_joystick`,
   `<1,1,2>`), already documented in `VIRTUAL_STICK_NATIVE.md` §5:
   `[11..12] u16 LE = 0x0200 | ((mode & 3) << 10)` with mode from the joystick config —
   the same bits 10-11 of the LE u16 = bits 2-3 of byte[12].
3. **Cross-frame consistency** — the app's default stick stream (TLV frame 0x01/0x0A,
   `VIRTUAL_STICK_NATIVE.md` §1b) carries TLV1 value byte[12] = `0x06`, which is exactly
   `0x02 | (1 << 2)` = mode 1 (Position) with the base flag — i.e. DJI Fly streams sticks
   with the gear permanently set to Position. Both frame families place the gear in the
   same bits.

`0x03/0x8E` (the verified float joystick) has **no** gear slot — that is why virtual sticks
alone never changed the mode.

## 2) Firmware proof: the RC160 has no mode command (why T8 could never work)

The RC160 (Mavic Mini RC) firmware module 2700 is a uImage → LZMA → cpio initramfs
(`reverse/unpacked/decompiled/firmware`); the app-facing service is `/usr/bin/apsrv` —
ELF 32-bit MIPS MSB, **not stripped**, with debug_info. Its DUML dispatch
(`DealProtocolCmd`, table @VMA 0x466650, 71 entries) is the complete set of app→RC
commands this firmware accepts. For cmd_set `0x06` it registers only:

```
0x03 Rc_Cal_Set · 0x04 Get_Rc_Channel · 0x19 Set_Mode(STUB) · 0x1a Get_Mode(STUB)
0x27 Get_Key_Status · 0x2b/0x2c Gimbal_Ctrl_Speed · 0x2d/0x2e Self_Defined_Key
0x2f Rc_Pair_Freq · 0x33/0x34 Gimbal_Ctrl_Pitch_Speed · 0x48 Rc_Get_param
0x49 Rc_Stick_Mix_Push · 0x55 Rc_Set_Test_Mode · 0x1e Mcu_Push_Battery
0xfa Get_Middle_Value · 0xf5 Get_Push_Stick_Enable · 0xb0 Get_Push_Roll_Enable
0xf8 Rc_Stick_Verify · 0xfb Rc_Disable_Country
```

- **`0x06/0x19` (Client_Pro_Rc_Set_Mode) is a stub**: the handler body is
  `li v0,-1; jr ra` — it returns -1 and does nothing. `0x06/0x1a` likewise.
- **`0x06/0x06` and `0x06/0x11` are not registered at all** (no entry → no handler).

So the three T1–T8 candidate cmd_ids are dead on this hardware: two never reach a handler,
one reaches a handler that ignores the frame. The auto-detector could only ever mis-attribute
osd noise or a physical-gear move to a candidate.

## 3) Bench result (2026-09-11, grounded, no flight) and what is still open

**Observed on the bench.** In the flight UI the top-left HUD "MODE" reads the live
`FLYC_STATE` (OSD byte @0x1e; `src/core/telemetry.cpp:160`, `dji_link_beta/telemetry.py:232`).
When the mode is changed **in the DJI Fly app, that HUD value changes** (and the change is
what our client displays) — so the HUD mode is a live, ground-valid indicator of the real
mode block. When the mode is changed **by our gear burst (`fmode`), nothing in the HUD
changes** — the `0x01/0x02` mode_sw frame is not being applied by this FC.

**Interpretation.** The readback side is proven (FLYC_STATE moves when the real mechanism
moves the block), so the failure is on the send side: either (a) the FC ignores
`0x01/0x02` without control authority — the documented precondition for the whole
mobile-RC emulation (`0x49/0x80 [0x01]` first, see `VIRTUAL_STICK_NATIVE.md` §2a) — or
(b) the mode_sw bits in this frame are not the gear on WM160. The OSD gear readback
(`[mode] RC gear channel -> …`, OSD dword @0x20 bits 13-14) separates the two: if the
gear channel never moved during a burst, the frame was rejected outright (case (a) is
testable: wrap the burst in authority request/release); if it moved while the HUD mode
stayed, mode_sw carried something else (case (b)).

**Still open (hardware):**
1. Whether the FC accepts `0x01/0x02` at all on WM160, and with which preconditions
   (control authority first? motors armed?).
2. Gear→block order (expect 0=Sport, 1=Normal, 2=Cine per RcSoftSwitchMode ordinals +
   `control_mode[0..2]`=12/8/7), confirm via FLYC_STATE once a burst is accepted.
3. Cine ↔ TRIPOD: `FLYC_STATE` 19 (Cinematic) or 38 (TRIPOD_GPS).
4. Burst length: 30 × 50 ms emulates a held switch; raise `MODE_BURST_FRAMES` if the FC
   debounces longer.

### Bench procedure (props off first)

```bash
python3 dji_link_beta/test_flight_mode_gear.py      # 36 byte/behaviour checks, no hardware
# client up and telemetry visible, then per gear:
fmode sport            # non-blocking burst; watch "[mode] RC gear channel -> 0"
fmodeauth tripod       # BENCH variant: authority 0x49/0x80 [0x01] -> burst -> release;
fmodeauth normal       #   logs every frame ([mode] + [mode-dbg] rx dump for 10 s)
fmodeauth sport
# cross-check that hspeed (tilt param) and fmode are independent:
hspeed 10              # speed changes, gear/FLYC_STATE unchanged
# then send logs/latest.log — it contains the whole auditable sequence.
```

`[mode] RC gear channel -> N` (OSD dword @0x20 bits 13-14) = the frame was accepted.
`[mode] FLYC_STATE a -> b` = the active block actually changed (what the HUD shows).
No gear movement at all → the FC rejected the frame even with authority; that is a
falsifying result for the 0x01/0x02 vehicle and points back to a capture of the real app.
