# Reverse-engineering docs (WM160)

The full protocol/app write-up for the DJI Mavic Mini 1. Start with `MASTER_REPORT.md`
and `APP_MAP_INDEX.md`; the rest are per-topic references.

## Overview
- **`MASTER_REPORT.md`** — the consolidated report.
- **`APP_MAP_INDEX.md`** — one-page map of every app subsystem, with WM160-support tags.

## ★ 2026 research — derived from the PUBLIC MSDK (`dji-sdk-provided-4.18.jar`), HW-verified
These supersede earlier guesses where they disagree — the jar is DJI's own un-obfuscated DUML layer.
- **`VIRTUAL_STICK_RESEARCH_2026.md`** — controlled flight SOLVED: `0x03/0x8E` DataFlycJoystick (17-byte
  float payload, flag byte, WM160 pitch/roll + yaw/throttle swaps), authority via `0x03/0x80` (open=1/close=2).
- **`FLIGHT_MODE_SPEED_RESEARCH_2026.md`** — Cine/Normal/Sport + speed via `mode_normal_cfg.tilt_atti_range_0`.
- **`HOME_POINT_RESEARCH_2026_v2.md`** — ★ latest. SET home = `0x03/0x31` 18B, type+LAT+LON (HOMETYPE
  APP=2/AIRCRAFT=0, MSDK-confirmed). NOTE: home-coordinate READBACK (`DataOsdGetPushHome` 0x44 lat/lon) was
  DROPPED in code — never read reliably on WM160; only the home-recorded flag (u16@0x14 bit0) is kept → HUD
  "home: set/not set". Supersedes `HOME_POINT_RESEARCH_2026.md`.
- **`RECORD_PHOTO_RESEARCH_2026.md`** — ★ why recording didn't start: set-mode→START race (async mode switch drops
  START). Fix = wait + re-send START. Photo type SINGLE=1 (was HDR=2). Verify push `0x02/0x80` (recordState, videoRecordTime@0x1D).
- **`RTH_ALTITUDE_RESEARCH_2026.md`** — ★ RTH/go-home altitude = param `g_config.go_home.fixed_go_home_altitude_0`
  (hash 0x38cc63dc, u16 LE metres, 20..500) via `0x03/0xF9`; no dedicated command. Read back via `0x03/0xF8`
  (same as max height / max distance) — pc_client reads all three on connect + after write, shown in the HUD.
- **`MEDIA_0XE0_RESEARCH_2026.md`** — ★★ AUTHORITATIVE media reverse (HW-confirmed). `0xE0 = INVALID_CMD`
  (app Ccode enum) → WM160 does NOT implement cmd_id `0x20`/`0x1F`. Correct path = **`0x00/0x22`
  RequestSendFiles [CURRENT] → list PUSHED back as `0x00/0x24` GetPushFiles**; file via `0x26`→`0x27`;
  delete `0x28`. Confirmed by app smali + dji-firmware-tools. media.py rewritten to this.
- **`MEDIA_DELETE_VIEW_RESEARCH_2026.md`** — ★★ delete + view/thumbnail (app + dji-firmware-tools + MSDK v4,
  all consistent). DELETE = `0x00/0x28` count-prefixed u32 index list (native deleteFiles(ArrayList);
  fallback camera-set `0x02/0x79` DeletePhoto). VIEW = `0x00/0x26` RequestFile + 1-byte grade
  (ORIGIN=0/THUMBNAIL=1/SCREENNAIL=2) + offset/size from the record's PhotoAndVideoNailInfo → bytes on
  `0x00/0x27`. 16-byte request layout from litchis packer. media.py has delete()/fetch_thumbnail()/fetch_screennail().
- **`MEDIA_LIST_DOWNLOAD_RESEARCH_2026.md`** — earlier pass; its 0x20/0x1F conclusion is CORRECTED by the
  above (0x20 NAKs 0xE0 on hardware). Still useful for the mode fix + readiness signal.
- **`FLIGHT_LIMITS_RESEARCH_2026.md`** — max height/radius via `0x03/0xF9` param write; read back 0xF8.
- **`CAMERA_MEDIA_RESEARCH_2026.md`** — ISO (needs Manual exposure), recording (needs video mode), shutter
  `0x02/0x28`, and the media playback/download sequence (0xe0 fix = enter playback with mode `[2]`).
- **`HOME_POINT_RESEARCH_2026.md`** — superseded by v2 (kept for history).

## Protocol
- **`DUML_COMMANDS_FULL.md`** — 343 builder-verified DUML commands with byte layouts.
- **`DUML_ENCRYPTION.md`** — the "SIMPLE" encryption (cmd_type 0x43) for FC config frames: self-inverse byte-keystream XOR, static key, no handshake — implemented in `duml.py`.
- **`PARAM_HASH.md`** — the FC param name→hash (`h=(b+(h<<8))%(2^32-5)` over GBK) — implemented in `param_hash.py`.
- **`PARAM_WIRE.md`** — exact read/write param frames (`0x03/0xF8`/`0xF9`) and why they need the right transport.
- **`PARAM_WRITE_TRUTH.md`** — ★ how params are named/hashed/written/gated: `attribute` bitfield gates writes, no global unlock, no commit, send plaintext.
- **`PARAM_TABLE_WM160.md`** — ★ the **132 verified live params** on WM160 (captured by `0xF8` sweep) with hash/type/access/current/min/max/default.
- **`KEYVALUE_DUML_TRANSPORT.md`** — how MSDK KeyValue keys map onto DUML frames.
- **`TELEMETRY_TRUTH.md`** — verified OSD offsets/decoding as implemented in `telemetry.py`.
- **`TELEMETRY_TABLE.txt`**, **`cmdmap.txt`**, **`cmds.json`**, **`full_table.txt`**, **`CMD_TABLE.txt`** — raw command/telemetry tables.

## Flight & gating
- **`FLIGHT_GATING.md`** — what a PC ground-station must do to fly (login, calibration, modes, home, limits, virtual stick).
- **`VIRTUAL_STICK_NATIVE.md`** — ★ byte-perfect `0x01/0x0A` VirtualJoyStickHelper payload from `libsdk_jni.so`, channel order, preconditions.
- **`MSDK_FLIGHT_UNLOCK.md`**, **`MSDK_FULL_REFERENCE.md`**, **`MSDK_MEDIA_SEQUENCE.md`** — DJI MSDK (4.13) reference: virtual-stick/flight-control API, KeyManager, media sequence, mapped to our DUML.
- **`TAKEOFF_UNLOCK.md`** — every motor-start / takeoff gate and how each clears.
- **`DARK_NOGPS_TRUTH.md`** — verified: dark/no-GPS takeoff IS unlockable — write FC param `fc_dark_need_gps_0 = 0` (takes off in ATTI, drifts).
- **`INTELLIGENT_AND_PARAMS.md`** — QuickShots/IOC/panorama + the param name→hash path.
- **`CAMERA_AND_NOGPS.md`** — camera exposure enums (ISO/EV/mode), gimbal recenter.
- **`ERROR_CODES.md`** — 743 diagnostic codes with local English text (in `diag_codes_full.py`).

## Media
- **`MEDIA_0XE0_RESEARCH_2026.md`** — ★★ authoritative list/download (0x22/0x24 handshake).
- **`MEDIA_DELETE_VIEW_RESEARCH_2026.md`** — ★★ authoritative delete (0x28) + view/thumbnail (0x26 grade byte).
- **`MEDIA_LIST_DOWNLOAD_RESEARCH_2026.md`** — earlier pass, 0x20/0x1F conclusion corrected by the above.
- **`MEDIA_TRANSPORT_TRUTH.md`** — native-lib mining; the `get_file_list_req 0x20` claim is wrong for WM160.
- **`MEDIA_TRANSFER.md`** — earliest media notes (superseded).

## App subsystems
`DOMAIN_*.md` — one reference per subsystem: account, activation/motorlock, transport (USB/AOA),
cloud API, media/album, geo/NFZ/unlock, firmware upgrade, voice, push/LTE/analytics, product
config/capabilities, KeyValue SDK, logs/simulator, RC functions, UI flow, maps/Bluetooth,
missions/vision.

## Source material
`unpacked_app_dex/` — the 16 reconstructed DEX (whole app). `all_classes.txt`,
`isSupport_keys.txt` — class/capability lists.
