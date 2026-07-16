# APP_MAP_INDEX — DJI Fly (`dji.go.v5` v1.21.4) full subsystem map for WM160 / Mavic Mini 1

One-page index of the entire reversed app, keyed to what a **PC ground-station** (Pi-as-AOA-phone, replacing app + RC sticks) actually needs. Device identity everywhere: **WM160 = UAV59 = ProductType 0x3B (59)**, RC = UAV59RC (id 99), EXIF `FC7203`. DUML addresses: `APP/PC=0x0A`, `CAMERA=0x01`, `FLYC=0x03`, `GIMBAL=0x04`, `RC=0x06`.

**Legend:** ✅ = on the WM160 control path / usable · ⚪ = optional/cloud, droppable · ❌ = NOT-WM160 (feature absent on Mini 1).

---

## CONTROL — flight / camera / gimbal (the core of PC control)
*(covered by the top-level reference docs, not a single DOMAIN_ file)*

| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ✅ **Flight gating / takeoff** | What must be true to arm & fly; activation is the only hard gate | [FLIGHT_GATING.md](FLIGHT_GATING.md), [TAKEOFF_UNLOCK.md](TAKEOFF_UNLOCK.md) | An activated WM160 arms with no login/internet; send FLYC `0x03` cmds from PC |
| ✅ **Flight-control DUML (cmd_set 0x03 FLYC)** | Full byte-layout for every FLYC request builder | [DUML_COMMANDS_FULL.md](DUML_COMMANDS_FULL.md) | Takeoff/land/RTH/joystick/flight-mode wire frames to emit from PC |
| ✅ **Camera exposure (cmd_set 0x02)** | ISO/EV/shutter/WB/mode enums + MANUAL precondition | [CAMERA_AND_NOGPS.md](CAMERA_AND_NOGPS.md) | Correct payloads (enum index, not raw number) so ISO/EV actually take |
| ✅ **Gimbal (cmd_set 0x04)** | Gimbal pitch/dial control, verified moving on COM4/COM5 | [DUML_COMMANDS_FULL.md](DUML_COMMANDS_FULL.md) | Point the camera from keyboard/neural net |
| ✅ **Telemetry / OSD push** | Native `Key*Push` decoder table (87 FC + camera/battery fields, byte offsets) | [TELEMETRY_TABLE.txt](TELEMETRY_TABLE.txt), [MASTER_REPORT.md](MASTER_REPORT.md) | Decode attitude/altitude/GPS/battery/motor-state feedback |
| ✅ **RC functions (cmd_set 0x06)** | Live stick/button/dial push, calibration, pairing | [DOMAIN_rc_functions.md](DOMAIN_rc_functions.md) | Read real RC sticks (`0x06/05` push, bytes 0/2/4/6) or bypass them |
| ✅ **KeyValue CSDK** | `(productId,componentType,index,…)`→value JNI abstraction over DUML | [DOMAIN_keyvalue_sdk.md](DOMAIN_keyvalue_sdk.md) | High-level get/set/do_action/listen; `VirtualJoyStick`=4×int32 |
| ⚪ **Intelligent modes / FC params** | 687 FC params + QuickShot/ActiveTrack params | [INTELLIGENT_AND_PARAMS.md](INTELLIGENT_AND_PARAMS.md) | Tune FC params; most autonomy modes are ❌ on Mini 1 |
| ❌ **Waypoints / vision / ActiveTrack** | WPML/KMZ SDK, SmartEye/POI/ActiveTrack (cmd_set 0x0A/0x22) | [DOMAIN_wpmz_missions_vision.md](DOMAIN_wpmz_missions_vision.md) | WM160 firmware rejects all of it — don't implement |

## PROTOCOL — transport / framing / SDK plumbing
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ✅ **AOA / USB transport** | Accessory binding + `55 CC | type u16 | len u32` composite mux; 11 channel types | [DOMAIN_transport_usb_aoa.md](DOMAIN_transport_usb_aoa.md) | The exact wire the Pi must speak; DUML on `0x5749`/`0x7530`, video on `0x574x` |
| ✅ **DUML command catalog** | Every builder-verified frame (cmd_set/cmd_id/payload/receiver) | [DUML_COMMANDS_FULL.md](DUML_COMMANDS_FULL.md), [CMD_TABLE.txt](CMD_TABLE.txt) | Master reference to build/parse any frame |
| ✅ **KeyValue → native ABI** | Java carries no cmd_id; `name→cmd` table lives in `libsdk_jni.so` | [DOMAIN_keyvalue_sdk.md](DOMAIN_keyvalue_sdk.md) | Tells you what must be captured live vs. read statically |
| ✅ **Error codes** | FC/camera/gimbal error + cannot-take-off taxonomy | [ERROR_CODES.md](ERROR_CODES.md) | Surface/handle failures on the PC UI |

## ACCOUNT / ACTIVATION / CLOUD
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ⚪ **Account / DJI-SSO** | Login/OAuth to `account-api.dji.com`, cookie-token, cross-app broadcast | [DOMAIN_account.md](DOMAIN_account.md) | **Not needed to fly an already-activated unit** — 100% app/cloud side |
| ⚪ **Activation + motor-lock** | One-time cloud activation (`0x00/0x32` + `0x03/0x62`), FC persists it | [DOMAIN_activation_motorlock.md](DOMAIN_activation_motorlock.md) | The ONE hard gate: activate once, then no account/internet ever again |
| ⚪ **Cloud API surface** | Full HTTP catalog (GEO/FlySafe/SkyPixel/Care/LiveShare/CloudControl) | [DOMAIN_cloud_api.md](DOMAIN_cloud_api.md) | All optional — drop every cloud call for PC control |
| ✅ **Product config** | Static UAV59 capability table (names/assets); `isSupportLTMByMediaMeta=true` | [DOMAIN_productconfig.md](DOMAIN_productconfig.md) | Confirms WM160 is RC-mediated (id 99), **not** phone-direct-WiFi |

## MEDIA
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ✅ **Media / album** | List/folder/thumbnail/download/delete (`0x00/0x20`) + playback (`0x02/0x7A/7B`) | [DOMAIN_media_album.md](DOMAIN_media_album.md), [MEDIA_TRANSFER.md](MEDIA_TRANSFER.md) | Pull photos/videos & stream playback from the SD card |
| ❌ **MasterShot / Creations / beauty** | Cloud+capability-gated auto-editor | [DOMAIN_media_album.md](DOMAIN_media_album.md) | Keyed on `camera.MasterShotMode` WM160 never reports — skip |

## GEO / UNLOCK
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ✅/⚪ **Geo / NFZ / unlock** | Forbid-status push, FLYC unlock levers (`0x41/0x47/0xCD`), license flow | [DOMAIN_geo_nfz_unlock.md](DOMAIN_geo_nfz_unlock.md) | Sub-250g WM160 is mostly advisory; hard NFZ lives in FC firmware, no app override; online GEO map hard-disabled |

## FIRMWARE / LOGS
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ⚪ **Firmware upgrade** | UAV59AC flow (`0x00/0x26` chunks + `0x00/0x41` control); engine is native `.so` | [DOMAIN_firmware_upgrade.md](DOMAIN_firmware_upgrade.md) | Not needed to fly; frames/URLs are native black-box |
| ✅/⚪ **Logs / sim / record** | Simulator `0x0B`, flight-log pull `0x03/0xD7`, diag `0x59` | [DOMAIN_logs_sim_record.md](DOMAIN_logs_sim_record.md) | Optional: run FC simulator, pull flight records; black-box/diag are firmware-only |

## MISC — voice / push / lte / analytics / maps / bt / ui
| Subsystem | One-line | Doc | Unlocks for PC GS |
|---|---|---|---|
| ✅ **UI flow / newbie / connect-guide** | Screen map, ConnType enum, pre-flight gate, **AOA-attach branch** | [DOMAIN_ui_flow_newbie.md](DOMAIN_ui_flow_newbie.md) | Sends no DUML; `startDeviceActivityByAoaActivity` is the AOA hook to mimic |
| ✅/⚪ **Push / LTE / analytics** | dpush notifications, native `libuavanalytics.so` telemetry upload | [DOMAIN_push_lte_analytics.md](DOMAIN_push_lte_analytics.md) | Telemetry upload is consent-gated & droppable; **LTE entirely ❌** |
| ✅ **Maps** | Third-party map providers drawing from telemetry you already decode | [DOMAIN_maps_bluetooth.md](DOMAIN_maps_bluetooth.md) | Reuse decoded `AircraftLocation`/`HomeLocation` for your own map |
| ❌ **Bluetooth** | BLE only bootstraps WiFi creds for WiFi drones | [DOMAIN_maps_bluetooth.md](DOMAIN_maps_bluetooth.md) | Unused on RC-bound WM160 — ignore |
| ❌ **Voice / audio** | Alibaba IDST NUI recognizer driving intelligent modes | [DOMAIN_voice_audio.md](DOMAIN_voice_audio.md) | Gated by `IsAudioControlSupport`=false — absent on Mini 1 |

---

## Bottom line for the PC ground-station
1. **Transport:** speak the AOA composite mux (DOMAIN_transport_usb_aoa) — DUML on `0x5749`/`0x7530`, video on `0x574x`.
2. **Fly:** emit FLYC `0x03` / camera `0x02` / gimbal `0x04` DUML (DUML_COMMANDS_FULL, CAMERA_AND_NOGPS); decode OSD telemetry (TELEMETRY_TABLE).
3. **The only cloud dependency is one-time activation** (DOMAIN_activation_motorlock) — after that, no account, login, or internet is required to arm and fly.
4. **Ignore** voice, LTE, Bluetooth, waypoints/ActiveTrack, MasterShot, firmware, and every optional cloud API — all ❌ or droppable for WM160.

**Full narrative:** [MASTER_REPORT.md](MASTER_REPORT.md).
