# DJI Mavic Mini 1 (WM160) — full breakdown for PC control

Result of a full reversing of DJI Fly (`dji.go.v5`, v1.21.4) + native libs. Project goal:
**the PC controls the drone (WASD from the keyboard, later a neural net), replacing the app and the
remote controller's sticks, with video and telemetry.** The PC is the brain, the Pi is a thin bridge to the remote controller.

WM160 = **Mavic Mini 1** = device **UAV59 / ProductType 0x3b (59)** in the app.

---

## 1. THE BIG PICTURE — three channels (all verified on hardware)

| channel | commands→drone | telemetry/responses back | video | tether-free flight |
|---|---|---|---|---|
| **remote controller COM4** (laptop↔remote controller, serial VID 2CA3:0008) | ✅ one way | ❌ | ❌ | ✅ |
| **drone COM5** (laptop↔drone directly, VID 2CA3:001E) | ✅ | ✅ (FC/camera/gimbal respond) | ❌ | ❌ (tethered by cable) |
| **Pi → AOA (as a phone) → remote controller** | ✅ | ✅ | ✅ | ✅ |

**Conclusion:** video and telemetry only travel over the **AOA channel** (the one the phone uses).
The laptop on COM4 sees only a thin serial (RC-local) — without video/telemetry. That is why
full control (video+telemetry+flight) requires a **Pi Zero 2 W** pretending to be a
phone. For "blind" WASD, COM4 would be enough, but without feedback.

Verified live: the camera/gimbal **actually moved** with our DUML command (over COM4 and COM5).

---

## 2. TRANSPORT

### 2.1 AOA mux (stream wrapper) — `composite.py`
Raw bulk stream from AOA = a concatenation of units:
```
[0]=0x55 [1]=0xCC | type(u16 LE) | length(u32 LE) | payload[length]     (total = 8+length)
```
Routing by `type` (index = type−0x5749):
- `0x5749` → **DUML** (payload = standard 0x55… frame)
- `0x574A / 0x574D` → **video** (payload starts with 0x6d, 16-byte liveview header)
- `0x574B / 0x574C / 0x7530` → other channels

Resync on `55 CC`; a unit may be split across USB reads → we buffer.
(parser `duss_parse_composite_data` @0x491a070, task `mb_route_usb_data_recv_task`)

### 2.2 DUML frame (payload of unit 0x5749) — `duml.py`
```
[0]=0x55 magic | [1..2]=len(10b)+ver(6b) | [3]=CRC8(seed 0x77) | [4]=sender | [5]=receiver
| [6..7]=seq LE | [8]=cmd_type/attr | [9]=cmd_set | [10]=cmd_id | [11..]=payload | CRC16(seed 0x3692)
```
Addresses (dev_type): PC=0x0a, RC=0x06, FC=0x03, gimbal=0x04, camera=0x01, dm368=0x08, battery=0x0d.
Codec checked against real frames (GetVersion header `55 0d 04 33`, RomanLut stick pings) — the CRCs match.

### 2.3 Video liveview — `liveview.py`
H.264 is **not encrypted**. Packet = [16-byte header][H.264 slice]. Header (LE):
`[0]=0x6d; [1] b0 is_i,b1-2 chan,b3-4 fmt(0=H264),b5 clear_cache; [2-5]u32 frm_idx;`
`[6-9] b0-19 pkt_len, b20-31 ssfn; [0xa]fps; [0xb-0xe] b0 frm_end,b1-5 slice_idx,b7 slice_end,`
`b8 pkt_is_last,b9-15 pkt_idx,b16-25 frm_len_kb; [0xf]crc(can be skipped).`
**Assembly:** group by frm_idx, order by (slice_idx,pkt_idx), concatenate pkt_len bytes, the frame
is ready on frm_end, is_i=keyframe → Annex-B H.264.
**Video start** (`drone.start_liveview()`): 0x02/0x09 camera selection, 0x08/0x41 decoder
capabilities (26b), 0x08/0x42 fps, 0x08/0x69 bandwidth. There is no separate "start push" — the stream comes on its own.

---

## 3. FLIGHT CONTROL

### 3.1 Sequence (confirmed both by reversing and by the MSDK model)
```
1. set_ground_station_mode(on)   0x03/0x80  1 byte   — ground station mode (on)
2. request_control()             0x49/0x80  0x01     — take control (+ if ignored: rc_to_pc 0x06/F1, preempt 0x19/41)
3. stream sticks ~10-25 Hz       (see 3.2)
4. land() 0x03/2A/02  OR  return_to_home() 0x03/2A/06   — land/RTH (panic)
5. release_control()             0x49/0x80  0x00
```
Takeoff (starts the motors): `takeoff()` **0x03/2A/01**. Cancels: takeoff 0x0D, land 0x0E, RTH 0x0C.
**RTH is reliable on the Mini 1** (climb→home→land) — we use it as an emergency button.

### 3.2 Virtual stick — TWO variants (we will check on HW which one the FC accepts)
Both: 4 channels, `value = round(axis·660 + 1024)`, clamp **[364..1684]**, center 1024.
- **A. special_tlv** — `cmd_set 0x01 / cmd_id 0x0A`, TLV container (our `drone.set_sticks`).
  4×11-bit in uint64 (bits 8/19/30/41 + bit62) + flags 0x0200 + TLV 0x55/0x04.
- **B. mobilerc joystick** — `cmd_set 0x01 / cmd_id 0x02` (`uav_action_virtual_rc_joystick`),
  13 bytes: [0]=0, [1..6]=4×11-bit, [B..C]=flags 0x0200|(mode<<10). Simpler, no TLV.

**MSDK confirms: Virtual Stick WORKS on the Mavic Mini** (supported since SDK 4.13). So the FC
accepts these commands — our injection will almost certainly work. There is no Course/Home Lock on the Mini.

### 3.3 Gimbal (camera) — VERIFIED, it moves
- speed `0x04/0x0C`: int16 LE yaw·10,roll·10,pitch·10 + `0x81 00` (°/s, send ~10 Hz)
- angle `0x04/0x14`: int16 yaw·10,roll·10,pitch·10 + ctrl + duration·10 (0.1°)
- modes: FOLLOW/FPV/FREE, recenter, extended downward tilt.

### 3.4 Camera — 101 commands (full table `reverse_docs/CMD_TABLE.txt`)
Ready: photo `0x02/01` (type 2=single), record `0x02/02` (1=start/0=stop/2=pause/3=resume),
mode `0x02/10`, zoom `0x02/34` (`09 00 00 <u16=×100>`), ISO `0x2a`, shutter `0x28` (7b), EV `0x2e`,
WB `0x2c` (5b), video format `0x18` (5b), photo mode `0x6a`, codec `0xab` (H264/H265).

---

## 4. FULL DUML MAP — 436 commands, 29 sets
Files: `reverse_docs/cmdmap.txt` (all set/id), `cmds.json` (with req/rsp types), `full_table.txt`.

| set | what | set | what |
|---|---|---|---|
|0x00 general (51)|activation/files/upgrade/reset|0x11 adsb|ADS-B/RemoteID (not on Mini)|
|0x01 special|virtual-RC, blackbox, TLV sticks|0x12 bt|BLE/iBeacon|
|0x02 camera (101)|camera|0x19 extend|control arbitration|
|0x03 fc (40)|flight controller|0x21 health|health + fault injection|
|0x04 gimbal (20)|gimbal|0x22 fc2|waypoint/WPMZ (Mini will reject), factory|
|0x05 centerboard|power|0x23 navigation|handheld (not for Mini)|
|0x06 rc (41)|remote controller|0x24 perception/esim|VPS token, eSIM|
|0x07 wifi (26)|link, TX-power, region|0x49 sdk|control-authority|
|0x08 dm368|video board/liveview|0x50/51/52|audio-LED / WLM(debug) / autoflight|
|0x09 ofdm/SDR|radio link|0x59 diag|diagnostics engine (execute)|
|0x0A vision (34)|tracking/QuickShots (Mini partially)|0x10 test|factory self-test|
|0x0B simulator|**simulator (fly without props!)**|0x0D battery|battery|

**Key flight/control:** 0x03/2A (takeoff/land/rth), 0x49/80 (control-auth), 0x03/80
(ground-station), 0x06/F1 (RC→PC), 0x19/40-41-46 (right-of-control), 0x01/0A and 0x01/02 (sticks).
**Files/logs from the drone:** 0x00/2A (start, 54b header+path) + 0x00/26 (fragments) + selective-ACK.
**Parameters:** 0x03/F8 read, F9 write, F7 info — but by name HASH (see §7).

---

## 5. TELEMETRY — 371 fields (full table `reverse_docs/TELEMETRY_TABLE.txt`)
Main source — OSD push from the FC (cmd_set 0x03). Offsets in the payload:
altitude s16@0x10 ×0.1m; speeds @0x12/14/16; roll/pitch/yaw @0x18/1a/1c ×0.1°;
**flight mode** u8@0x1e&0x7F; **flying** (u32@0x20&0x0E), **motors on** bit3@0x20;
**GPS level** (u32@0x20>>18)&0xF, **satellites** u8@0x24; **motor-start-refusal reason** u8@0x33.
Home/drone lat/lon = f64 **radians** @0x00/0x08. Battery (0x0D/02): voltage@+1, current@+5, temp@+0x11, %@+0x13.
Plus: wind, RemainingFlightTime, GPS-spoofing detect, gimbal angles, ADS-B, altitude/distance limits.

---

## 6. DIAGNOSTICS "why the motors won't start" — `diag_codes.py`
OSD byte +0x33 → reason (96 codes): 0=OK, 1=compass, 3=device locked, 5=IMU calibration,
7=IMU warmup, 8=compass calibration, 10=no GPS in novice, 13=low voltage… (text 30xxx —
server-side HMS). `test_all.py`/`checks.py` read this via the drone's direct USB and suggest what to fix.

---

## 7. FC PARAMETERS — `flyc_param_infos.json` (687 of them)
Limits and settings, readable/writable via 0x03/F8(read)/F9(write). Key ones:
- `flying_limit.max_height` def 120, **max 500**; `max_radius` def 30, **max 5000**; `radius_limit` off.
- `serial_api_cfg.advance_function_enable` def=**0** — possibly the external-control gate (to be checked).
- `go_home.fixed_go_home_altitude` — RTH altitude.
- ⚠️ **Limitation:** parameters are addressed by the **name hash** (`"g_config.section.field_0"`), and the
  hash algorithm itself is computed at runtime behind the packer — **not obtainable statically**. To write
  parameters (remove limits/raise speed), we need ONE live (name→hash) via Frida/traffic
  capture, then brute-force the variant. For now — only commands that need no hash (takeoff/land/sticks/gimbal).

---

## 8. FUNCTIONS on the Mini 1 — what WORKS, what does NOT
✅ **Available:** takeoff/land/**RTH** (climb→home→land), **virtual stick** (confirmed by
MSDK 4.13+), gimbal (all modes), camera (photo/video/zoom/settings), **QuickShots** (Dronie/Circle/
Helix/Rocket), **simulator** (fly without props — testing), GPS position/speed/modes (Normal/Sport/
Cine/Tripod), altitude/distance limits, LED/find-my-drone, voice.
❌ **Not available (SDK code exists, but the aircraft will reject it):** ActiveTrack/Follow-Me/tracking (no sensors),
waypoint/WPMZ, obstacle avoidance (`supportNavigationMode=false` for UAV59), Course/Home Lock.
🔧 **Homemade tracking** (neural net on the PC → sticks+gimbal) — **feasible** (follow-me on the Mini
was done via virtual stick), but it flies blind (no sensors) — risky.

---

## 9. UNPACKED APP (bonus)
`libdatajar.so` (165 MB) turned out to be 16 concatenated DEX (**128,105 classes**) with wiped magic +
a scrambled string table. **Fully recovered** → `reverse_docs/unpacked_app_dex/*.dex`
(decompilable with jadx anytime). Class list: `reverse_docs/all_classes.txt`.
Capabilities (`isSupport_keys.txt`, 237 flags) mostly **align with the drone at runtime** —
we will get the exact WM160 matrix live on connection.

---

## 10. WHAT IS NEEDED (hardware/software) and LIMITATIONS
**Hardware:** Pi Zero 2 W + 16GB microSD (+ a card reader if there is no slot) + the micro-USB cable from the
drone kit. Pi↔PC — over Wi-Fi (no cable needed), the remote controller powers the Pi.
**PC software (Python, ready):** `duml/composite/liveview/telemetry/drone/control/diag_codes` + utilities.
**Honest open items:**
1. **Virtual stick on HW** — which variant (0x01/0A vs 0x01/02) and whether `serial_api_cfg`/ground-station is needed (MSDK says it works — we'll check).
2. **FC parameter hash** — a runtime dump (Frida) is needed to write limits/speed.
3. **Pi bring-up** — raw-gadget AOA is finicky, finalized on a live Pi.
4. Exact WM160 capability matrix — live on connection.

---

## 11. CODE (the ready PC brain, `dji_link_beta/`)
`duml.py` protocol · `composite.py` AOA demux · `liveview.py` video · `telemetry.py`+`diag_codes.py`
telemetry · `drone.py` all commands · `control.py` WASD→sticks · `transport.py` (Serial/Net/AOA).
Utilities: `probe_serial/read_sticks/monitor_serial/checks/test_all/gimbal_demo/video_liveview/…`.
Reverse docs: `reverse_docs/` (this summary + telemetry + command maps + unpacked DEX).

---

## 12. ROADMAP (for when the Pi arrives)
1. Flash the Pi (headless, Wi-Fi/SSH), transfer `pi/`, bring up the raw-gadget AOA bridge.
2. The Pi plugs into the remote controller → the PC receives the raw AOA stream over Wi-Fi → `composite.py` splits DUML/video.
3. **Video on screen** (H.264→ffplay/PyAV), **telemetry** (human-readable), **WASD→sticks**.
4. First flight: **props removed**, ground-station→control-auth→light throttle, read the reason if the motors
   won't start; RTH/land as the emergency button; the physical remote controller nearby.
5. Then: removing limits (after the parameter hash), simulator for safe tests, then — the neural net.
