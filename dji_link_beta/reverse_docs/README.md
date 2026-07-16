# Reverse-engineering docs (WM160)

The full protocol/app write-up for the DJI Mavic Mini 1. Start with `MASTER_REPORT.md`
and `APP_MAP_INDEX.md`; the rest are per-topic references.

## Overview
- **`MASTER_REPORT.md`** — the consolidated report.
- **`APP_MAP_INDEX.md`** — one-page map of every app subsystem, with WM160-support tags.

## Protocol
- **`DUML_COMMANDS_FULL.md`** — 343 builder-verified DUML commands with byte layouts.
- **`DUML_ENCRYPTION.md`** — the "SIMPLE" encryption (cmd_type 0x43) for FC config frames: self-inverse byte-keystream XOR, static key, no handshake — implemented in `duml.py`.
- **`PARAM_HASH.md`** — the FC param name→hash (`h=(b+(h<<8))%(2^32-5)` over GBK) — implemented in `param_hash.py`.
- **`PARAM_WIRE.md`** — exact read/write param frames (`0x03/0xF8`/`0xF9`) and why they need the right transport.
- **`TELEMETRY_TABLE.txt`**, **`cmdmap.txt`**, **`cmds.json`**, **`full_table.txt`**, **`CMD_TABLE.txt`** — raw command/telemetry tables.

## Flight & gating
- **`FLIGHT_GATING.md`** — what a PC ground-station must do to fly (login, calibration, modes, home, limits, virtual stick).
- **`TAKEOFF_UNLOCK.md`** — every motor-start / takeoff gate and how each clears.
- **`DARK_NOGPS_TRUTH.md`** — verified: dark/no-GPS takeoff IS unlockable — write FC param `fc_dark_need_gps_0 = 0` (takes off in ATTI, drifts).
- **`INTELLIGENT_AND_PARAMS.md`** — QuickShots/IOC/panorama + the param name→hash path.
- **`CAMERA_AND_NOGPS.md`** — camera exposure enums (ISO/EV/mode), gimbal recenter.
- **`ERROR_CODES.md`** — 743 diagnostic codes with local English text (in `diag_codes_full.py`).

## Media
- **`MEDIA_TRANSPORT_TRUTH.md`** — media goes over the same radio/DUML link, but needs playback mode (`0x02/0x10`) first and the firmware's real request bytes (one live capture outstanding).
- **`MEDIA_TRANSFER.md`** — earlier media protocol notes (request layout superseded by the above).

## App subsystems
`DOMAIN_*.md` — one reference per subsystem: account, activation/motorlock, transport (USB/AOA),
cloud API, media/album, geo/NFZ/unlock, firmware upgrade, voice, push/LTE/analytics, product
config/capabilities, KeyValue SDK, logs/simulator, RC functions, UI flow, maps/Bluetooth,
missions/vision.

## Source material
`unpacked_app_dex/` — the 16 reconstructed DEX (whole app). `all_classes.txt`,
`isSupport_keys.txt` — class/capability lists.
