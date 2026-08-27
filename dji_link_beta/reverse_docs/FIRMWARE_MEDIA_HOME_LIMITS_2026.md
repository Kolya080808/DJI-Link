# WM160 firmware pass: media, home/RTH, and flight limits

Date: 2026-08-27

This report compares the unpacked WM160/RC160 firmware with the existing DJI Fly/MSDK reverse
engineering and the current DJI Link implementation. It deliberately separates four evidence levels:

- **Firmware-confirmed**: observed in the WM160 image shipped in `V01.00.0600`.
- **Native/APK-confirmed**: bytecode or native DJI SDK code proves the structure, but not necessarily
  that WM160 uses that branch.
- **Hardware-confirmed**: observed on the project's WM160 over the AOA/DUML link.
- **Capture-pending**: plausible, but a raw DJI Fly album capture is still required.

No firmware image was modified or repacked.

## 1. Firmware map

The two top-level `*_dji_system.bin` files are POSIX tar archives containing signed IMAH modules.

| Product | Module | IMAH type | Role | Result |
|---|---:|---|---|---|
| WM160 | `0100` | `CAM` | Ambarella camera, Linux, media and SD services | Outer IMAH decrypted; LZ4 and two SquashFS filesystems extracted |
| WM160 | `0306` | `FC` | Flight controller | Outer IMAH decrypted; an inner encrypted MVFC-like payload still blocks code/table analysis |
| WM160 | `0905` | `NFZ` | FlySafe/NFZ data | Decrypted vendor data image |
| WM160 | `1100` | `BTRY` | Battery MCU | Decrypted raw MCU image |
| WM160 | `1200` | `ESC` | ESC MCU | Decrypted raw MCU image |
| RC160 | `0600` | unspecified | RC Cortex-M firmware | Plaintext chunk; Cortex-M vector table identified |
| RC160 | `2700` | unspecified | RC Linux kernel | Plaintext MIPS U-Boot uImage, LZMA kernel |

The camera module is:

`decompiled/firmware/drone/wm160_0100_v02.51.00.08_20200414.pro.fw.sig`

Relevant extracted tree:

`decompiled/firmware/research_outputs/camera_parts/squashfs_2_root/`

Firmware-confirmed media facts:

- The SD card is mounted as Ambarella filesystem `d:` at `/tmp/SD0`.
- `cmd_rlv_transfer` starts before the DUML host and links to `libdjiimc.so`.
- `dji_sys` links to `libduml_frwk.so`, `libduml_hal.so`, `libduml_osal.so`, and
  `libduml_util.so`.
- `dji_ftpd` exists, but generic FTP/file-handle infrastructure must not be confused with the album
  protocol.
- `bin/dji_nail` has separate JPEG thumbnail and screennail generation paths, including a WM160-specific
  low-memory YUV420 path.

Useful `dji_nail` string file offsets:

| Offset | Evidence |
|---:|---|
| `0x17fb` | `Seq:%d Type:%d Index:%d` |
| `0x19f9` | screennail resampling |
| `0x1a49` | screennail JPEG encoding |
| `0x1a97` | thumbnail resampling |
| `0x1ab4` | received nail image sequence/index |
| `0x1bbf` | `WM160 Use Low Mem, should be yuv420` |

This proves that WM160 firmware produces both preview tiers. It does not prove the request selector bytes
or the gallery list record layout.

## 2. Media protocol: corrected status

### 2.1 Transport

Media control uses the same AOA stream as normal commands. Composite type `0x5749` carries DUML. There is
no evidence that RC-connected WM160 requires a separate FTP, HTTP, RNDIS, or RTP socket for album access.

The existing outer framing remains correct:

```text
55 cc | type u16 LE | payload length u32 LE | payload
```

### 2.2 There are at least three related DJI file paths

The existing documents often combine these paths, but they are not one protocol:

1. **Modern native CSDK media task**
   - `0x00/0x20`: get file list
   - `0x00/0x1f`: get file data
   - `0x00/0x28`: delete
   - Native state machine waits for playback/download state and uses a windowed selective-ACK transfer.

2. **Legacy PlaybackManager command family**
   - `0x22`: RequestSendFiles
   - `0x23`: AckReceiveFiles
   - `0x24`: GetPushFiles
   - `0x25`: SetResendFiles
   - `0x26`: RequestFile
   - `0x27`: GetPushFile
   - `0x28`: DeleteFile

3. **Litchis FileChannel multiplexed through `0x26/0x27`**
   - Inner command IDs List/File/Stream/Num.
   - A 10-byte session/offset header.
   - REQUEST/DATA/ACK/PUSH/ABORT inner packet types.

The second path selects/pushes files using outer command IDs. The third path puts another protocol inside
outer `0x26/0x27`. They must not be treated as the same handshake.

### 2.3 Modern native path is real, but not yet reproduced

The recovered `libsdk_jni.so` analysis proves that `0x20/0x1f` are native wire command objects, not Java
enum ordinals:

| Native symbol/function | VA/file offset |
|---|---:|
| `uav_general_get_get_file_list_req` constructor | `0x27bb464` |
| `CommonFileDownloadHandler::RequestFileList` | `0x216e3d0` |
| `ListTransferRequest::ConfigFilterData` | `0x20d47cc` |
| `ListTransferRequest::CreateStartRequestPack` | `0x20d4bb4` |
| `FileTransferHandler::SendPack` | `0x4a15840` |

The list constructor identifies cmd set `0x00`, ID `0x20`, response-required behavior, route `0x5749`, and
a 500 ms default timeout. The native code reserializes the Java task object; therefore
`FileTaskRequest.toBytes()` is not automatically the DUML payload.

The native playback state machine also proves that a mode-set ACK is not readiness:

| State function | Offset |
|---|---:|
| `CameraQuickModeModule::ActionEnterPlaybackImpl` | `0x26aca90` |
| `ExpectedInPlayback` | `0x26adc2c` |
| `getSwitchPlaybackModeStrategy` | `0x26ab52c` |
| `switchPlaybackModeDirectly` | `0x26ad994` |
| `SpecialCommandManager::EnterPlayback` | `0x4658a38` |
| `SendSpecialControllPack` | `0x465b4d8` |
| `KeyIsPlayingBackPush` | `0x3469f98` |
| `KeyCameraWorkModePush` | `0x34509f4` |

For the legacy strategy, the SDK repeatedly sends an 11-byte, XOR-protected `0x01/0x01` special-control
packet until the camera status push confirms playback. The exact final 11 payload bytes have not yet been
recovered into a tested fixture.

### 2.4 APK/SDK product selection

WM160 is DJI product `UAV59`, device ID `59` (`0x3b`), camera model `FC7203`, main component index 0.
`UAV59PlaybackProductConfig` confirms playback-related capability but does not select a wire protocol.

The active DJI Fly UI path is:

```text
V1FileListKt / V1PlaybackManageFileKt
  -> PlayBackManagerForAndroid$CppProxy
  -> native fetchMediaFiles/fetchThumbnail/fetchPreview/downloadMediaFileRawData/deleteFiles
```

Consequently, finding a Java `DataCameraRequestSendFiles` or `DataRequestList` class proves that DJI owns
that protocol, but not that the official WM160 product abstraction selects it. The decisive selection is
inside native camera abstraction/capability logic.

There are also two incompatible mode-enum generations:

```text
legacy DataCameraGetMode.MODE:          2=PLAYBACK, 3=TRANSCODE, 6=DOWNLOAD, 7=NEW_PLAYBACK
modern CameraWorkMode value object:     2=PLAYBACK, 3=MEDIA_DOWNLOAD, 6=DOWNLOAD, 7=TRANSCODE
```

Thus `0x02/0x10 [03]` cannot be called an authoritative WM160 media-mode frame. For FC7203 the first
legacy candidate is `0x02/0x10 [02]`, followed by the special-control transition and a wait for the
camera state push.

### 2.5 Firmware routing boundary

The Linux camera image does not own the final album command handlers. Its route table proves this chain:

```text
external DUML camera receiver 0x01
  -> internal camera:0 host key 0x10
  -> vt_air
  -> netlink v1, host_pid 41225
  -> target_pid 20480 (0x5000)
  -> remote camera RTOS command dispatcher
```

The target RTOS application text is not present as an analyzable ARM/ELF image in the extracted `0100`
partitions. The pre-SquashFS region is Ambarella DSP/ORC image processing code, not the DUML command CPU.
This is why no honest firmware VA can be supplied for `0x1f/0x20/0x22..0x28`.

Linux-side responsibilities are nevertheless clear:

- `libduml_frwk.so`: generic routing, netlink, event request/response transport;
- `dji_sw_uav`: relays opaque image/download streams from RTOS to wireless transport;
- `dji_nail`: creates thumbnail and screennail JPEGs;
- `dji_ftpd`: FTP/RNDIS service, not the AOA album protocol;
- `cmd_rlv_transfer`: upgrade/checksum relay, not the album protocol.

The SD/DCIM path is firmware-confirmed as `/tmp/SD0/DCIM/<N>MEDIA` on Linux, corresponding to Ambarella
filesystem `d:` on the RTOS side.

### 2.6 What hardware artifacts currently prove

- The AOA/DUML link reaches camera receiver `0x01`.
- `0x02/0x10` changes camera behavior/work mode.
- Four exact binary frames prove that `0x02/0x09` returned `0xe0` on an early direct-USB run.
- Project notes report that tested `0x00/0x20` request variants returned `0xe0`, but their raw frames were
  not preserved.
- Three zero-length historical abort dumps prove that litchis sessions 4, 7, and 10 ended in ABORT without
  DATA. They do not preserve the preceding PUSH body or ABORT reason.
- The reported values `4110` and `385 files` are operator observations; no raw PUSH or independent SD
  directory listing preserving those values exists in the repository.
- No checked-in capture demonstrates a successful `0x22 -> 0x24` list.
- No checked-in capture demonstrates a successful full download, thumbnail, screennail, or delete.

Therefore neither of these claims is currently justified:

- "`0x20/0x1f` do not exist on WM160."
- "`0x22/0x24` is hardware-confirmed as the correct WM160 gallery path."

DJI's `Ccode` names `0xe0` `INVALID_CMD`; `0xe4` is the distinct `NOT_SUPPORT_CURRENT_STATE` code. An
`0xe0` reply still does not establish which capability/route/parser gate rejected a multi-product SDK
command, but it must not be relabeled as the wrong-state code.

### 2.7 Exact legacy FileChannel structures confirmed by DJI bytecode

Header inside outer `0x26/0x27`:

```text
+0 u8   0x0a | version<<6       # version 1 -> 0x4a
+1 u8   inner_cmd_id<<5 | type
+2 u16  packet/length word LE   # receive: high 4 bits packet index, low 12 bits length
+4 u16  session ID LE
+6 u32  offset LE
```

LIST request body:

```text
u32 startIndex, with storage in the upper two bits of byte 3
u16 count
u8 subtype
```

FILE request body:

```text
u32 fileIndex
u16 subIndex
u8 subtype       # ORG=0, THM=1, SCR=2
u8 grade
u32 offset
u32 length
```

These bytes are confirmed for the legacy Java classes. Their use as the active DJI Fly WM160 gallery path
is not confirmed.

ACK and abort bodies are also serializer-confirmed:

```text
ACK:   u32 seek/next, u8 rangeCount, rangeCount * (u32 offset, u32 length)
ABORT: u32 reason, Force=1
```

For session 1, start 0, storage 1, count 200, subtype origin, the exact FileChannel LIST candidate is:

```text
4a 00 11 00 01 00 00 00 00 00 00 00 00 40 c8 00 00
```

This is serializer-exact, but its storage/index semantics are not yet WM160-confirmed.

### 2.8 Recommended WM160 sequence

The best-supported non-destructive sequence to test next is:

1. Save the current mode from `0x02/0x80 payload[4]`.
2. Send legacy playback candidate `0x02/0x10 [02]` to camera `0x01`.
3. Do not list on the ACK. Wait for `0x02/0x80` mode 2 and preferably playback parameters `0x02/0x82`.
4. If playback does not become active, reproduce the repeated `0x01/0x01` special-control transition:
   11-byte body, 9 action bytes plus XOR checksum and device/flags, every 100-200 ms, at most 20 tries.
   The final 11 bytes remain capture-required.
5. Try one litchis LIST through outer `0x00/0x26`, starting with small count and both start index 0 and 1.
6. On `0x00/0x27` preserve the raw length word, packet nibble, session, offset, PUSH body, DATA and ABORT.
7. Send an inner LIST ACK only after a PUSH. Do not mix it with outer `0x23` ACK.
8. Reassemble DATA by the header offset and `length_word & 0x0fff`; do not infer EOF from idle time.
9. Request original/thumbnail/screennail with inner FILE subtype 0/1/2 only after a real file index is known.
10. Keep delete disabled until its exact body is captured from the official app.
11. Restore the mode saved in step 1 and wait for its state push.

The competing paths must be probed independently:

- modern: `0x20/0x1f` plus its native selective-ACK protocol;
- outer legacy: `0x22 -> 0x24`, `0x23/0x25`, `0x27`;
- litchis: inner protocol entirely inside `0x26/0x27`.

Responses and ACK types from one family are not interchangeable with another.

### 2.9 Download and delete status

For an inner FILE request, the confirmed 16-byte body is:

```text
u32 fileIndex, u16 subIndex, u8 subtype, u8 grade, u32 offset, u32 length
```

Subtypes 0/1/2 mean original/thumbnail/screennail. It is not yet known whether WM160 wants nail
offset/size from list metadata or accepts zero offset/length to select the complete generated nail. The
modern `PhotoAndVideoNailInfo` dataset contains four `u64` values in decode order: thumbnail size,
screennail size, thumbnail offset, screennail offset. Native code may translate a compact camera record
into that object, so it must not be used as the raw litchis record parser without a capture.

Delete reaches native code as either `PlayBackManagerForAndroid.deleteFiles` or a `FileActionRequest`.
`0x00/0x28` is a real command ID, but the current `u16 count + u32 index[]` body has no Java packer,
firmware handler, or successful capture behind it. Automatic delete or automatic fallback to camera
`0x02/0x79` would risk selecting the wrong file and is intentionally not recommended.

### 2.10 Known issues in the Python media prototype

`dji_link_beta/media.py` is useful as a probe but must not be ported to C++ as production behavior yet:

- `parse_fc()` ignores the high-four-bit packet index and low-12-bit receive length split.
- LIST PUSH `inner[0:4]` is described as a file count but is also used as a byte-length completion target.
- FILE transfers do not send an ACK for each announced/data window.
- Completion is inferred from an idle timeout rather than a validated protocol end state.
- First-chunk metadata stripping is not hardware-confirmed for a real file.
- Delete payload `u16 count + u32 indices[]` is capture-pending.
- Mode `3` plus a `0x02/0x80` byte check does not reproduce the native special-control state machine.

### 2.11 Required capture

The shortest path to a correct implementation is one raw AOA capture using the official app on the same
WM160 firmware:

```text
open album -> wait for grid -> request thumbnail -> download one original -> delete one disposable file
```

Capture complete `0x5749` units in both directions, including timestamps. It will settle:

- work-mode and repeated `0x01/0x01` entry bytes;
- state push used as readiness;
- active list command family;
- sender, receiver, DUML attr bits, and sequence behavior;
- list body and record boundaries;
- transfer window/ACK structure;
- thumbnail selector and offsets;
- delete body and response.

Until that capture exists, further blind payload sweeps have low value.

## 3. Home point and RTH

### 3.1 Outgoing wire structures

The current Python and C++ builders agree with DJI's serializers:

| Operation | Command | Payload |
|---|---|---|
| Explicit home | `0x03/0x31` | `02 | latitude f64 LE radians | longitude f64 LE radians | interval` |
| Aircraft-current home | `0x03/0x31` | 18 zero bytes |
| Legacy/current fallback | `0x03/0x2a` | `03` (`HOMEPOINT_NOW`) |
| Start RTH | `0x03/0x2a` | `06` (`GOHOME`) |
| Cancel RTH | `0x03/0x2a` | `0c` (`DropGohome`) |

Important: SET is latitude-first, longitude-second. The home/common telemetry structures are
longitude-first, latitude-second.

The final interval byte remains a small unresolved discrepancy: shipped-app analysis supports `0`, while
an MSDK v4.18 path uses `0x64`. The current `0` is reasonable for the DJI Fly one-shot profile, but only a
raw official-app frame can make it byte-capture-confirmed.

### 3.2 RTH altitude

The implementation is correct:

```text
parameter: g_config.go_home.fixed_go_home_altitude_0
hash:      0x38cc63dc
wire hash: dc 63 cc 38
type:      u16 LE metres
range:     20..500
write:     0x03/0xf9
read:      0x03/0xf8
```

There is no separate WM160 RTH-altitude command in the investigated app path.

### 3.3 Remaining correctness gap

Correct bytes do not yet mean verified success:

- Commands are sent fire-and-forget even when ACK is requested.
- `0x31` set-home response codes are not correlated or decoded.
- UI messages announce success immediately after send.
- Param read replies do not consistently reject a nonzero return code before updating state.
- RTH start/cancel was not previously represented by the common-push go-home status bits.
- Home coordinates are intentionally not exposed, so an explicit coordinate cannot be compared with
  readback.

Known `0x31` result meanings include success, invalid coordinate, home not initially recorded, GPS not
ready, and distance too far. A proper request manager should correlate `(sequence, cmd set, cmd ID)`, parse
the result, and only then update the UI.

### 3.4 Telemetry identity caveat

Static DJI classes identify `0x03/0x44` and alias `0x09/0x02` as `DataOsdGetPushHome`, with:

```text
+0x00 longitude f64 LE radians
+0x08 latitude  f64 LE radians
+0x14 flags u16
+0x16 goHomeHeight u16 metres
```

However, `TELEMETRY_TRUTH.md` describes a live `0x03/0x44` length-78 frame with sensor-like fields. The
current corpus does not preserve enough complete envelope information to reconcile the two observations.
Do not trust coordinates or aliases until a fixture includes sender, receiver, cmd type, set, ID, length,
and known real coordinates. The conservative implementation keeps the home-recorded and RTH-related flags
but does not publish coordinates.

### 3.5 Changes made in this pass

- Explicit home now rejects NaN, infinity, latitude outside `[-90,90]`, and longitude outside
  `[-180,180]` before sending.
- OSD common parsing exposes `(u32@0x20 >> 5) & 7` as `go_home_status`.
- Home parsing exposes flags bit 1 (`go_home_mode`), bit 7 (`has_go_home`), and `u16@0x16`
  (`go_home_height_m`).
- The OSD common minimum length is corrected to `0x35` because byte `0x34` is read.
- A C++ regression test checks explicit-home order/units, invalid coordinates, RTH/cancel bytes, and
  RTH telemetry fields.

ACK correlation and home-coordinate readback were not guessed into the implementation; they remain
capture-backed follow-up work.

## 4. Height and speed modification

### 4.1 Stock parameter path

The practical supported method is a normal FC parameter write:

```text
read by hash:  0x03/0xf8, payload hash u32 LE
write by hash: 0x03/0xf9, payload hash u32 LE + typed value
```

The name hash is:

```python
h = 0
for byte in full_name.encode("gbk"):
    h = (byte + (h << 8)) % 0xfffffffb
```

Confirmed limits:

| Parameter | Hash | Type | Min | Max | Observed |
|---|---:|---|---:|---:|---:|
| `g_config.flying_limit.max_height_0` | `0x0371238a` | u16 m | 15 | 500 | 500 |
| `g_config.flying_limit.max_radius_0` | `0x425c0a94` | u16 m | 15 | 5000 | 2000 |
| `g_config.go_home.fixed_go_home_altitude_0` | `0x38cc63dc` | u16 m | 20 | 500 | hardware write/read path known |

Values inside these ranges are writable and persistent. A write above 500 m cannot raise the altitude
ceiling because the FC validates against its parameter metadata.

### 4.2 More than 500 m

The supplied firmware does not currently offer a practical patch path:

- Outer `wm160_0306` IMAH decryption and checksums succeed.
- The decrypted chunk still contains a high-entropy, encrypted MVFC-like inner layer.
- The legacy `dji_mvfc_fwpak.py` key does not decrypt this WM160 payload correctly.
- Parameter names and stock float/u16 patterns are therefore not available for reliable offsets.
- Even with a plaintext patch, production IMAH signing requires DJI's private PRAK key or a separately
  demonstrated trusted boot/update bypass.

Random occurrences of `f4 01` in ciphertext are not a 500 m table offset.

Conclusion: normal writes up to 500 m are practical; more than 500 m is not currently achievable with the
available image and tools. This report does not cover or recommend NFZ/FlySafe bypass.

### 4.3 Speed

WM160 speed modes are multi-parameter preset blocks, not one universal speed number. Relevant stock data
includes:

| Preset | Parameter | Stock |
|---|---|---:|
| Gentle/Cine | `mode_gentle_cfg.rc_scale` | 0.25 |
| Gentle/Cine | `mode_gentle_cfg.tilt_atti_range` | 20 deg |
| Normal | `mode_normal_cfg.rc_scale` | 0.77 |
| Normal | `g_config.mode_normal_cfg.tilt_atti_range_0` | 20 deg |
| Sport | `mode_sport_cfg.rc_scale` | 0.925 |
| Sport | `mode_sport_cfg_tilt_atti_range_0` | 30 deg |
| Sport | `mode_sport_cfg_vert_vel_up_0` | 4 m/s |

The active Normal tilt parameter (`0x95544807`) is writable and readback works. Hardware testing found
that trying to emulate Sport by raising only this parameter was effectively limited around Normal
behavior. This means a second mode-dependent clamp, RC scale, gear-selected block, or runtime selection
logic still applies. Claims that one tilt value alone provides a true Sport mode are too strong.

The exact speed clamp cannot be located until the inner FC layer is decrypted or a RAM/runtime dump is
obtained.

## 5. Reproduction references

Outer FC decrypt/check:

```bash
python decompiled/firmware/dji-firmware-tools/dji_imah_fwsig.py \
  -vvvv -k PRAK-2019-09 -k UFIE-2019-11 -u \
  -i decompiled/firmware/drone/wm160_0306_v03.04.06.44_20211115.pro.fw.sig \
  -m /tmp/opencode/wm160_0306
```

Camera outer decrypt:

```bash
python decompiled/firmware/dji-firmware-tools/dji_imah_fwsig.py \
  -vv -u \
  -i decompiled/firmware/drone/wm160_0100_v02.51.00.08_20200414.pro.fw.sig \
  -m /tmp/opencode/wm160_0100
```

Relevant primary files:

- `decompiled/firmware/research_outputs/logs/wm160_0100.log`
- `decompiled/firmware/research_outputs/logs/wm160_0306.log`
- `decompiled/firmware/research_outputs/camera_parts/squashfs_2_root/bin/dji_nail`
- `decompiled/firmware/research_outputs/camera_parts/squashfs_2_root/lib/libduml_frwk.so`
- `dji_link_beta/reverse_docs/MEDIA_PROTOCOL_DEX_TRUTH.md`
- `dji_link_beta/reverse_docs/MEDIA_TRANSPORT_TRUTH.md`
- `dji_link_beta/reverse_docs/HOME_POINT_RESEARCH_2026.md`
- `dji_link_beta/reverse_docs/HOME_POINT_RESEARCH_2026_v2.md`
- `dji_link_beta/reverse_docs/RTH_ALTITUDE_RESEARCH_2026.md`
- `dji_link_beta/reverse_docs/PARAM_TABLE_WM160.md`
- `dji_link_beta/reverse_docs/FLIGHT_LIMITS_RESEARCH_2026.md`
- `dji_link_beta/reverse_docs/FLIGHT_MODE_SPEED_RESEARCH_2026.md`

## 6. Final priorities

1. Capture one official DJI Fly album session at raw AOA/composite level.
2. Recover a tested 11-byte `0x01/0x01` playback-entry fixture and the confirming status push.
3. Implement request/ACK/retcode correlation before claiming home/RTH success in the UI.
4. Add a complete-envelope fixture for the genuine WM160 home push before restoring coordinates.
5. Do not port the current Python media parser to C++ until list/chunk/delete layouts are captured.
