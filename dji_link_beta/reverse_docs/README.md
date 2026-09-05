# Reverse-engineering docs (WM160)

The full protocol/app write-up for the DJI Mavic Mini 1. Start with `MASTER_REPORT.md`
and `APP_MAP_INDEX.md`; the rest are per-topic references.

**Media status update (2026-08-27):** the WM160 media protocol remains **largely unknown**.
`FIRMWARE_MEDIA_HOME_LIMITS_2026.md` supersedes prior WM160
media-path verdicts. Native `0x20/0x1f`, outer legacy `0x22..0x28`, and litchis inside `0x26/0x27`
are distinct real DJI protocols, but no successful checked-in WM160 capture establishes which one DJI Fly
selects. List records, transfer completion/ACK behavior, and delete remain capture-pending.

## Overview
- **`MASTER_REPORT.md`** — the consolidated report.
- **`APP_MAP_INDEX.md`** — one-page map of every app subsystem, with WM160-support tags.

## ★ 2026 research — derived from the PUBLIC MSDK (`dji-sdk-provided-4.18.jar`), HW-verified
These supersede earlier guesses where they disagree — the jar is DJI's own un-obfuscated DUML layer.
- **`VIRTUAL_STICK_RESEARCH_2026.md`** — controlled flight SOLVED: `0x03/0x8E` DataFlycJoystick (17-byte
  float payload, flag byte, WM160 pitch/roll + yaw/throttle swaps), authority via `0x03/0x80` (open=1/close=2).
- **`FLIGHT_MODE_SOFTSWITCH_2026.md`** — ★ flight mode IS switchable: the mode is an RC **gear**
  choice, sent as `0x06/<cmd_id>` to receiver `0x06` (payload = u32 LE gear, SPORT=0/POSITION=1/TRIPOD=2,
  plaintext, sender `0x02`), confirmed by `FLYC_STATE` (31 Sport / 19 Cine / 38 Tripod), with three
  candidate cmd_ids and an auto-detector. Supersedes the "cannot switch" verdict below.
- **`FLIGHT_MODE_HW_CHECKLIST.md`** — ★ the on-drone procedure that settles the remaining unknowns
  (winning cmd_id, FLYC_STATE per mode, Cine↔Tripod, gating, virtual sticks).
- **`FLIGHT_MODE_SPEED_RESEARCH_2026.md`** — max horizontal speed via `mode_normal_cfg.tilt_atti_range_0`
  (still authoritative for *speed*); its mode-switch section is superseded by `FLIGHT_MODE_SOFTSWITCH_2026.md`.
- **`HOME_POINT_RESEARCH_2026_v2.md`** — ★ latest. SET home = `0x03/0x31` 18B, type+LAT+LON (HOMETYPE
  APP=2/AIRCRAFT=0, MSDK-confirmed). NOTE: home-coordinate READBACK (`DataOsdGetPushHome` 0x44 lat/lon) was
  DROPPED in code — never read reliably on WM160; only the home-recorded flag (u16@0x14 bit0) is kept → HUD
  "home: set/not set". Supersedes `HOME_POINT_RESEARCH_2026.md`.
- **`RECORD_PHOTO_RESEARCH_2026.md`** — ★ why recording didn't start: set-mode→START race (async mode switch drops
  START). Fix = wait + re-send START. Photo type SINGLE=1 (was HDR=2). Verify push `0x02/0x80` (recordState, videoRecordTime@0x1D).
- **`RTH_ALTITUDE_RESEARCH_2026.md`** — ★ RTH/go-home altitude = param `g_config.go_home.fixed_go_home_altitude_0`
  (hash 0x38cc63dc, u16 LE metres, 20..500) via `0x03/0xF9`; no dedicated command. Read back via `0x03/0xF8`
  (same as max height / max distance) — pc_client reads all three on connect + after write, shown in the HUD.
- **`FIRMWARE_MEDIA_HOME_LIMITS_2026.md`** — ★ current media status across APK, SDK native code, firmware,
  and retained hardware evidence; separates the three protocol families and identifies capture-pending fields.
- **`MEDIA_0XE0_RESEARCH_2026.md`**, **`MEDIA_DELETE_VIEW_RESEARCH_2026.md`**, and
  **`MEDIA_LIST_DOWNLOAD_RESEARCH_2026.md`** — investigation history. Their WM160 protocol-selection,
  mode-fix, and delete-body conclusions are superseded; serializer/enumeration evidence remains useful.
- **`FLIGHT_LIMITS_RESEARCH_2026.md`** — max height/radius via `0x03/0xF9` param write; read back 0xF8.
- **`CAMERA_MEDIA_RESEARCH_2026.md`** — ISO, recording, and shutter findings remain useful; its album
  protocol conclusion is superseded by `FIRMWARE_MEDIA_HOME_LIMITS_2026.md`.
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
- **`FLIGHT_MODE_HW_CHECKLIST.md`** — ground checks + flight steps for the mode switch (novice off, GPS fix, `gear_cfg.*`).
- **`DARK_NOGPS_TRUTH.md`** — verified: dark/no-GPS takeoff IS unlockable — write FC param `fc_dark_need_gps_0 = 0` (takes off in ATTI, drifts).
- **`INTELLIGENT_AND_PARAMS.md`** — QuickShots/IOC/panorama + the param name→hash path.
- **`CAMERA_AND_NOGPS.md`** — camera exposure enums (ISO/EV/mode), gimbal recenter.
- **`ERROR_CODES.md`** — 743 diagnostic codes with local English text (in `diag_codes_full.py`).

## Media
- **`FIRMWARE_MEDIA_HOME_LIMITS_2026.md`** — authoritative current status and next capture sequence.
- **`MEDIA_PROTOCOL_DEX_TRUTH.md`** — authoritative for DEX serializers/value objects, not WM160 selection.
- **`MEDIA_TRANSPORT_TRUTH.md`** — native symbols and AOA transport evidence; working WM160 flow not proven.
- **`MEDIA_0XE0_RESEARCH_2026.md`**, **`MEDIA_DELETE_VIEW_RESEARCH_2026.md`**,
  **`MEDIA_LIST_DOWNLOAD_RESEARCH_2026.md`**, **`MEDIA_TRANSFER.md`** — superseded research history.

## App subsystems
`DOMAIN_*.md` — one reference per subsystem: account, activation/motorlock, transport (USB/AOA),
cloud API, media/album, geo/NFZ/unlock, firmware upgrade, voice, push/LTE/analytics, product
config/capabilities, KeyValue SDK, logs/simulator, RC functions, UI flow, maps/Bluetooth,
missions/vision.

## Source material
`unpacked_app_dex/` — the 16 reconstructed DEX (whole app). `all_classes.txt`,
`isSupport_keys.txt` — class/capability lists.
