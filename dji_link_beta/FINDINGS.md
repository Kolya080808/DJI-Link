# DJI Fly / Mavic Mini 1 (WM160) — reversing map

Summary of the full reconnaissance reversing of the APK (`dji.go.v5`, v1.21.4) + `libsdk_jni.so`.
Assembled from 5 parallel agents. WM160 = **device ID 59 (UAV59)** in the app's numbering.

---

## 0. TL;DR — the most unexpected
1. **PC control of the drone confirmed live** (camera/gimbal moved). Stick/flight/camera commands are reversed and baked into `drone.py`.
2. **The remote controller bridges our commands to the drone over radio ONE way** (the drone executes; responses do not come back into the remote controller's serial). Tether-free control is real, without a Pi.
3. **`libwpmz_jni.so` — a full autonomous-mission engine** (WPML 1.0.2, KMZ format): waypoint/mapping/survey/agri, a full set of actions. Missions can be authored and uploaded.
4. **`libgimbal-lib` — visual gimbal tracking by ML boxes** (`MLTrackingBox → _nav_gimbal_ctrl_cmd_t`) — a ready foundation for "neural net tracks a person".
5. **`libneonui_shared` — Alibaba voice control** (ASR/TTS/wakeword/voiceprint) with a hardcoded app_key — for the voice roadmap.
6. **436 DUML commands across 29 sets** (not 0x00–0x0D, but up to 0x59/0xEE), including factory/diagnostic/fault-injection ones.
7. **`flyc_param_infos`** (687 FC parameters) — altitude/distance limits and the **external-control gate `serial_api_cfg.advance_function_enable`**.
8. The app is **packed** (AppGuard/Bangcle); the real Java code is in an encrypted `libdatajar` (165 MB, DEX disguised as .so).
9. Hardcoded: **AES key `2b3e96e33b801b52`** (media), white-box AES IV `0102030405060708`, a bunch of cloud API keys, 5 DJI X.509 certs (geo-unlock trust anchor).

---

## 1. Control — commands (baked into drone.py, verified)
| function | cmd_set | cmd_id | recv | payload |
|---|---|---|---|---|
| virtual stick (special_tlv) | 0x01 | 0x0A | FC 0x03 | 4×11-bit channels, ×660+1024→[364,1684] |
| take/return control | 0x49 | 0x80 | FC | 1 byte (1=take/0=return) |
| RC→PC control | 0x06 | 0xF1 | RC | flag |
| seize right of control | 0x19 | 0x40/0x41/0x46 | FC | arbitration |
| takeoff/land/RTH | 0x03 | 0x2A | FC | 1 byte (01/02/06; cancels 0D/0E/0C) |
| gimbal speed/angle | 0x04 | 0x0C/0x14 | gimbal 0x04 | int16 ×0.1° |
| camera photo/video/mode/zoom | 0x02 | 0x01/0x02/0x10/0x34 | camera | see drone.py |
| **alt: virtual RC joystick** | 0x01 | 0x02 | — | 4 axes + buttons (second method) |

**Flight sequence:** `49 80 01` → (if ignored: `06 F1` / `19 41`) → `03 2A 01` takeoff → stream `01 0A` ~20 Hz → `03 2A 02` land → `49 80 00`.

⚠️ **Likely missing gate:** the FC parameter `serial_api_cfg.advance_function_enable` (idx 362, def=**0**). It may need to be set =1 (parameter write `0x03/0xF9`) for the FC to accept external sticks. Input clamps: `input_pitch/roll_limit` (def 3500), `input_yaw_rate_limit` (15000), `input_vertical_velocity_limit` (600).

---

## 1b. Media — SD card list / download / delete (confirmed ≥2 sources: MSDK v4 jar + app smali + dft lua)

All on cmd_set **0x00** (non-encrypted), sender **0x02** (APP), receiver **0x01** (CAMERA).
`0x00/0x20` (File List) and `0x00/0x1F` (File Data) are **NOT implemented on WM160** → hardware returns `0xE0 INVALID_CMD`.

| step | cmd | payload | notes |
|---|---|---|---|
| 1. Enter playback | `0x02/0x10` | `[0x02]` | PLAYBACK mode; liveview freezes |
| 2. Wait gate | — | — | poll `0x02/0x80` push byte[4]==2, OR `0x02/0x82` push arrival |
| 3. Request list | `0x00/0x22` | `[0x00]` CURRENT / `[0x01]` NEXT | list does NOT come in ACK |
| 4. List push | `0x00/0x24` ← | `[seq i32][records…]` | strip 4B prefix; ACK with `0x23[0x00]` |
| 5. Request file | `0x00/0x26` | `[idx u32][subIdx u16=0][grade u8][count u8=1][off u32][size u32]` | grade: ORIGIN=0 THUMB=1 SCREEN=2 |
| 6. Data push | `0x00/0x27` ← | `[hdr u32][dataLen u32][idx u32][nameLen u8][name][data]` | ACK each with `0x23[0x00]` |
| 7. Delete | `0x00/0x28` | `[count u16 LE][idx u32 LE …]` | count width capture-pending (watch 0xD6) |
| 8. Exit playback | `0x02/0x10` | `[0x01]` | restores liveview |

**ACK** (`0x00/0x23`) payload is always `[0x00]` (1 byte) — required after each 0x24 and 0x27 push or camera stalls.

---

## 2. Full DUML map (29 sets, 436 commands)
| set | purpose | set | purpose |
|---|---|---|---|
| 0x00 general (51) | activation/files/upgrade | 0x11 adsb (9) | ADS-B/RemoteID/realname |
| 0x01 special (6) | virtual-RC, blackbox, TLV | 0x12 bt (5) | BLE/iBeacon |
| 0x02 camera (100) | camera/payload | 0x15 goggles | FPV goggles |
| 0x03 fc (40) | flight controller | 0x18 cellular4g | LTE/eSIM |
| 0x04 gimbal (20) | gimbal | 0x19 extend (6) | control arbitration |
| 0x05 centerboard | power | 0x21 health | health monitor + **fault inject** |
| 0x06 rc (41) | remote controller | 0x22 fc2 (12) | FC-gen2, **waypoint/factory/ESC** |
| 0x07 wifi (26) | Wi-Fi link, TX-power, region | 0x23 navigation | nav/handheld |
| 0x08 dm368 | video transcoder | 0x24 perception/esim | VPS + eSIM 0x75/0x76 |
| 0x09 ofdm (15) | SDR/radio link | 0x49 sdk | control-authority |
| 0x0A vision (34) | VPS/QuickShots | 0x50/0x51/0x52 | audio-LED / WLM(debug) / autoflight |
| 0x0B simulator (3) | **flight simulator** | 0x59 diag (6) | **diagnostics engine (exec)** |
| 0x0D smartbattery (8) | battery | 0x10 test (5) | **factory self-test** |
| | | 0xEE app (5) | app↔app sync |

### "Not for the consumer" — engineering/dangerous
- `0x59/04 sys_diag_execute` — run arbitrary diagnostics; `0x10/10-14` factory self-test; `0x22/28 FC_FACTORY_CMD`; `0x21/04` fault injection.
- `0x03/F7-FA` — **read/write/reset ANY FC parameter by hash** (limits, serial_api gate, gains).
- `0x03/FE` force-disable motors; `0x03/39` FC raw-data mode.
- `0x00/96 force_upgrade`, `0x00/A5 switch_firmware_bank` (downgrade), `0x00/91 RNDIS toggle`, `0x00/0B reboot`, `0x00/DE reset`.
- `0x01/82-84` blackbox access; `0x00/1F-2A` drone filesystem; `0x00/DF,EA` log export.
- **Regulatory:** `0x06/21 CE/FCC`, `0x07/05 TX-power`, `0x07/18 country_code`, `0x11/50 dynamic_max_height`, RemoteID/EID `0x03/77,78`, `0x03/F5 driver_license_info`.

Full list — scratchpad `full_table.txt` / `cmdmap.txt`.

---

## 3. FC parameters — `flyc_param_infos.json` (687 of them, in the project)
Read/write via `0x03/F8` (read hash) / `0x03/F9` (write hash). Key ones:
- `flying_limit.max_height` def 120, **max 500**; `max_radius` def 30, **max 5000**; `radius_limit_enabled` def 0.
- `advanced_function.height_limit_enabled` min=1 (cannot be set to 0); `novice_cfg.novice_func_enabled` def 0.
- `go_home.fixed_go_home_altitude` def 20, max 500.
- `serial_api_cfg.*` — the external-control gate (see §1).
- `system_command.mapper[COMMAND_*]` — enum of RC channels (AILERON/ELEVATOR/THROTTLE/RUDDER/AUTO_TAKE_OFF/GO_HOME/KNOB…).
- Full control-loop gains, servo/gimbal limits, battery thresholds.

---

## 4. Telemetry (telemetry.py + diag_codes.py)
OSD-general (FC push): altitude/speeds/angles ×0.1, mode(+0x1e), flying/motors(+0x20), satellites(+0x24), motor-start-refusal reason(+0x33). OSD-lowfreq: time/flight count, limits. Home/drone lat/lon = **radians**. Battery in detail = `0x0D/01,02,03` (bit-packed). **Motor-failure reasons** — 96 codes (diag_codes.py); text 30xxx — server-side HMS.

---

## 5. Autonomy / neural net (for the future)
- **`libwpmz_jni.so`** — WPML 1.0.2 missions (KMZ = zip: `template.kml` + `waylines.wpml`). 7 JNI: `native_GenerateKMZFile`, `native_CheckWPMZValid`, `native_GetWaylines…`. Actions: takePhoto, record, gimbalRotate, hover, rotateYaw, orientedShoot, spray, triggers (reachPoint/timed/distance). No limit on the number of points. **→ full autonomous flights can be authored.**
- **`libgimbal-lib`** — gimbal tracking by ML boxes: `MLTrackingBox → _nav_gimbal_ctrl_cmd_t`, EIS, `setDebugParams`. **→ foundation for "track a person".**
- **`libneonui_shared`** — Alibaba voice (ASR/TTS/wakeword/**voiceprint**), app_key `83578acaef32b906ad3aaf62b662e714` @ `nls-wave.aliyuncs.com`.
- Vision/QuickShot: cmd_set `0x0A` (34 commands), MobileNetV3 person-detection models in assets (`qs/*.model`), sky-segmentation.

---

## 6. Native libs — map (the interesting bits)
| lib | what | findings |
|---|---|---|
| libGroudStation | DUML CRC/TEA helper (not missions!) | CRC8/16 tables, hardcoded `key_tea`, `native_rcDataDeal` (stick obfuscation), Bangcle anti-tamper |
| libwpmz_jni | **WPML mission engine** | KMZ authoring (see §5) |
| libgimbal-lib | visual gimbal tracking | ML boxes → gimbal ctrl |
| libflightrestrictcore | GEO/FlySafe "brain" | SQLCipher NFZ+licenses, unlock REST API, per-SN licenses — **enforces advisory, real block in firmware** |
| libFRCorkscrew | FR signing/verification + APK-sig anti-tamper | **5 DJI X.509 certs** (geo-unlock roots) + Android Debug cert in whitelist |
| libFlyForbid | NFZ geometry | seg-circle intersection |
| libupgrade_core/jni | firmware upgrade | payload **MD5 only**, anti-rollback (device-side), corrupted ELF, `addUpgradeDebugMode`/`SetUpgradeServerUrlMode`, DUML CFT + FTP |
| libmtmd_crypto | AES-128 media | **key `2b3e96e33b801b52`** (ECB) |
| libwaes | White-Box AES-128-CBC | key in tables; **IV `0102030405060708`** |
| libhash | signing of cloud requests | HMAC-SHA256, X-Wk-*/-Mc |
| libdatajar (165MB) | **packed DEX disguised as .so** | the real Java code |
| libilink*/liblinkid | **Tencent iLink/Mars** (not DJI radio!) | cloud/account/face; live-stream to WeChat |
| libdongle_esim_core | LTE/eSIM (DUML) | cmd_set 0x24 id 0x75/0x76 |

DJI radio (OcuSync/enhanced-wifi) — **not in these libs**, it is in firmware + part of libsdk_jni.

---

## 7. App layer, secrets, endpoints
- **Packing:** AppGuard (`libAppGuard.so` loads the DEX), Bangcle anti-tamper. Feature flags/bridges → only a Frida dump from root.
- **Hidden modes:** Profile→Settings→**Developer Options** (Demo/Store mode, Advanced Gimbal Cali, **Propeller Guard Mode** — changes limits), **Factory Mode** (工厂模式), Smart Diagnostic, QR-driven debug.
- **Exported (adb):** `DJIAoaActivity` (AOA/USB!), deep-links `djifly://linkapi`, `dji://fly/…`, BillyCC component bus, `UnlockLicenseManagerActivity`.
- **The app allows raising the ceiling >120 m** via a warning dialog (limits from the geo-DB).
- **Hardcoded keys:** Google/Firebase `AIzaSyBOtxVQ2yJLcqD6aXuP1tSYbEY3RJMTpUQ`, AMap `7eb94d5a03afc792bcabd0319c670bed`, HERE, OPPO push secret `27b67b37c9c04e6ebd5585c0185488bc`, Alibaba voice `83578acaef…`, AMap AES `01QZk7Fq1jhhx6e63Xfx9FdSmpbOeQQL`.
- **Internal hosts in prod:** `ci.djicorp.com` (internal CI), `dev-relay-service.djicdn.com`, **`192.168.2.1`** (another drone/remote-controller address, cleartext HTTP allowed), RNDIS `192.168.42.x`. CAAC report `uom.receive.caacic.cn`.
- **Certificates:** debug cert `dji.go.v5.debug` (morgan.zhu@dji.com) in the release.

---

## 8. Geo/NFZ/unlock
- Enforcement is **advisory in the app**, the real block is firmware. WM160 <250 g → most zones are "warning/authorization", not a hard block.
- Unlock is by **licenses** (`app_license_keys_table_`, tied to the drone SN), signed by a DJI cert (`libFRCorkscrew`, no private key). REST: `/api/v4/mobile/unlock_license_groups`, `api/unlimit_license?license_key=`.
- Honest bypass: (a) a valid self-unlock license, or (b) patching the WM160 firmware. App-side you can touch the computed `LimitAreaLevel`/license-enable (but Bangcle anti-tamper).

---

## 9. Next steps / surface
1. **Real flight** (carefully, without props): check whether `serial_api_cfg.advance_function_enable=1` is needed; assemble `flight_control.py` with the §1 sequence + panic-land.
2. **Live telemetry** — via the drone's direct USB (`checks.py`).
3. **Autonomy** — `libwpmz_jni` KMZ missions; tracking via `libgimbal-lib`.
4. **WM160 protocol** in detail — protobuf lib `libwmb261_proto.so`.
5. **Frida dump** of the packed DEX (`libdatajar`) from root — for feature flags/bridges.
</content>
