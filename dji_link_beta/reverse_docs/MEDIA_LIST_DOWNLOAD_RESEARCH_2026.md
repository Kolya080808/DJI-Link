# WM160 Media List + Download — DUML Reverse (2026)

> **SUPERSEDED AGAIN (2026-08-27):** this page remains useful for native/legacy evidence and readiness
> analysis, but it does not prove either `0x20/0x1F` absent or `0x22/0x24` selected on WM160. See
> `FIRMWARE_MEDIA_HOME_LIMITS_2026.md`.

Fresh reverse of the **rebranded WM160 app** (`uav.midware.*` = repackaged DJI CSDK core).
DEX dumps: models `classes_0451d00c.dex`, config `classes_016b200c.dex`,
download mgr `classes_08fe100c.dex`, crossplayback `classes_0855200c.dex`, full dump `/tmp/all`.
Native libs are now git-LFS stubs (133 B) — the prior `MEDIA_TRANSPORT_TRUTH.md` mined the real
`libsdk_jni.so` while it was present; its native findings are reused here.

---

## 0. TL;DR — the two things that were actually broken

1. **MODE BUG (highest confidence, fixes the 0xE0 / zero-reply):** enter-playback must send
   `0x02/0x10` payload **`[0x02]` = PLAYBACK**. Current `media.py` sends `[0x03]` = TRANSCODE, which
   is not a media state, so every file op is refused. VERIFIED by two independent reverses.
2. **LIST/DATA transport family is contested — see §4.** Native registry of THIS app = `0x20/0x1F`;
   DJI-GO reference jar = `0x22` legacy family. Both agree on delete `0x28` and on the mode fix.

---

## 1. Command IDs (VERIFIED)

Camera set **0x02** (CmdIdCamera$CmdIdType, DeviceType CAMERA=1, APP=2, CmdSet CAMERA=2 COMMON=0):
| cmd | name | payload |
|---|---|---|
| 0x02/0x10 | SetMode (set_camera_working_mode) | 1B mode |
| 0x02/0x11 | GetMode | — |
| 0x02/0x0C | switch_playbackmode (modern alias) | strategy |
| 0x02/0x80 | GetPushStateInfo (push) | camera state incl. getMode() |
| 0x02/0x82 | GetPushPlayBackParams (push) | playback nav/counts |

`DataCameraGetMode$MODE` wire values (from CAMERA_MEDIA_RESEARCH_2026, dji.* jar):
`TAKEPHOTO=0, RECORD=1, PLAYBACK=2, TRANSCODE=3, TUNING=4, SAVEPOWER=5, DOWNLOAD=6, NEW_PLAYBACK=7, BROADCAST=8, OTHER=100`
→ **enter playback = `[0x02]`**; exit = `[0x01]` RECORD or `[0x00]` TAKEPHOTO.

Common/general set **0x00** — file family. TWO families are defined by DJI on this set:
| cmd | LEGACY (dji GO/Spark/P3 era) | MODERN (CSDK/Fly era) |
|---|---|---|
| 0x1F | — | get_file_data_req (data) |
| 0x20 | — | get_file_list_req (list) |
| 0x22 | RequestSendFiles (start) | — |
| 0x23 | AckReceiveFiles | — |
| 0x24 | GetPushFiles (list push) | — |
| 0x25 | SetResendFiles (resend) | — |
| 0x26 | RequestFile | transfer_msg (0x00/0x26) |
| 0x27 | GetPushFile (data push) | — |
| 0x28 | DeleteFile | delete_file_req (SAME) |
| 0x2A | — | general_file_transfer |

VERIFIED cmd_id values (CmdIdCommon$CmdIdType in this app's config dex):
RequestSendFiles=0x22, AckReceiveFiles=0x23, GetPushFiles=0x24, SetResendFiles=0x25,
RequestFile=0x26, GetPushFile=0x27, DeleteFile=0x28, TransferFile=0x2A.

## 2. Packet framing (VERIFIED from DataBase.start / Pack + DeviceType.smali)
sender=DeviceType.APP=**0x02**, receiver=DeviceType.CAMERA=**0x01**, cmd_type=REQUEST, need_ack=YES,
encrypt=NONE (cmd_set 0x00 is on the non-encrypted list), cmd_set/cmd_id per above.
(DeviceType values confirmed in this app's config dex: CAMERA=0x01, APP=0x02.)

### Wait-for-ready signal (VERIFIED offset)
`DataCameraGetPushStateInfo.getMode()` reads **byte[4]** (offset 4, len 1) of the 0x02/0x80 push and
maps it via `DataCameraGetMode$MODE.find(int)`. So playback-ready = a 0x02/0x80 push whose
**byte[4] == 0x02 (PLAYBACK)**. This is the concrete condition `wait_playback_ready()` should test
(plus/or arrival of a 0x02/0x82 GetPushPlayBackParams push).

## 3. VERIFIED static payloads (legacy family, this app's Java models)
- 0x22 RequestSendFiles: 1B FILE_SELECT_MODE {CURRENT=0, NEXT=1, OTHER=0x64}.
- 0x25 SetResendFiles: 4B u32 index (LE, BytesUtil.z).
- 0x2A TransferFileRequest header: +0 1B cmdType, +1 4B fileSize(LE), +5 1B nameLen, +6 name,
  +6+N 1B fileType, then parameter bytes. (This is the upload/waypoint transport, not gallery.)

## 4. THE CONTESTED PART — which list/data family does WM160 use? (KEY OPEN ITEM)

Evidence for **MODERN 0x20/0x1F** (my fresh reverse of THIS app):
- Native command registry `cmdmap.txt` / `full_table.txt` (dumped from THIS app's `libsdk_jni.so`)
  registers on cmd_set 0x00 ONLY: **0x1F get_file_data, 0x20 get_file_list, 0x28 delete,
  0x2A general_transfer, 0x26 transfer_msg**. It does **NOT** register 0x22/0x23/0x24/0x27.
  An app cannot consume push frames it has no parser for ⇒ this app does not use 0x24/0x27.
- This app has NO Java model classes for GetPushFiles/GetPushFile/AckReceiveFiles/DeleteFile/
  GetPushPlayBackParams (only the send-side RequestSendFiles + SetResendFiles survive as vestigial
  legacy stubs). ⇒ list parsing, data reassembly, delete, and playback-params are ALL native. An app
  that kept the full legacy 0x22 flow would need those Java push-parsers; their absence is decisive.
- The gallery list model (`PlaybackMediaFile`, `MediaFileListType`, `FileType`, `FileSlotLocation`)
  is a **native-backed JNI interface** (`com.uav.crossplayback.playback.*`, CppProxy) → the list
  record is parsed in C++ (`libcross_playback`), and even the legacy-device path
  (`V1FileListKt`, `FlyFileDownloadManagerV1`) routes through the same native `PlayBackManager`.
- `MEDIA_TRANSPORT_TRUTH.md` (native mining): `CommonFileDownloadHandler::RequestFileList()` emits
  `uav_general_get_get_file_list_req` (0x00/0x20); data returns as `file_transfer_push` with a
  windowed **selective-ACK** (`FileTransferHandler::SendACKPack` / `TransmissionMissedSections`).

Evidence for **LEGACY 0x22** (`CAMERA_MEDIA_RESEARCH_2026.md`, from the DJI-GO `dji.*` reference jar):
- `dji/internal/camera/hgf` (PlaybackManager backend) drives WM160 via SetMode PLAYBACK →
  RequestSendFiles(CURRENT/NEXT) 0x22 → pushes 0x24 (list) + 0x27 (data) → ack 0x23 → resend 0x25.
- `Camera.getPlaybackManager()` vs `getMediaManager()` split; Mini-era claimed PlaybackManager.

**Reconciliation / my read:** The 0x22 family is the DJI-GO-4 era protocol; the jar covers all drones
so its presence does not prove WM160 uses it *with this app*. The app we must interoperate with is
THIS rebranded CSDK/Fly-style app, whose native registry only knows 0x20/0x1F. WM160 firmware very
likely supports BOTH families, but the path THIS app (and our bridge, which mimics it) should drive is
**0x20/0x1F**. The reason our earlier 0x20/0x1F attempt got zero reply is almost certainly the MODE
bug (§0.1: we sent TRANSCODE `[0x03]`, never PLAYBACK `[0x02]`), NOT the wrong cmd family.

**Not 100% statically decidable** — the on-wire `get_file_list_req`(0x20)/`get_file_data_req`(0x1F)
payloads are **native-built** (re-serialized in C++, not the Java `toBytes()`), so exact field bytes
need ONE live capture or a Frida hook. Everything else is pinned.

## 5. RECOMMENDED sequence for WM160 (media.py rewrite target)

```
1. ENTER PLAYBACK: _cmd(0x02, 0x10, b"\x02", CAMERA)          # [0x02]=PLAYBACK  (fixes the [0x03] bug)
2. WAIT for camera to confirm media state, do NOT list first:
     - 0x02/0x80 GetPushStateInfo push: getMode()==PLAYBACK(2)
     - 0x02/0x82 GetPushPlayBackParams push with non-zero totalNum
3. LIST (primary): _cmd(0x00, 0x20, <get_file_list_req>, CAMERA)
     struct (best static guess, confirm by capture): u32 index, u16 count, u8 slot(=0),
     u8 type(0=all/media); paged — loop index+=count until list-left==0.
     Response 0x00/0x20 rsp = MediaFile records (record layout parsed natively; capture to pin stride).
4. DOWNLOAD (primary): _cmd(0x00, 0x1F, <get_file_data_req>, CAMERA)
     req fields: file index, sub-type (ORIGIN/THUMBNAIL/SCREENNAIL), offset, dataSize.
     Data arrives as windowed file_transfer_push frames → app MUST send selective-ACK per window
     (ack received-mask / missed-sections) or the pump stalls; send abort to stop.
5. DELETE: _cmd(0x00, 0x28, <delete_file_req>, CAMERA)         # both families agree on 0x28
6. EXIT: _cmd(0x02, 0x10, b"\x01", CAMERA)                      # back to RECORD

FALLBACK (only if 0x20 stays silent after the mode fix — legacy path):
1'. same enter-playback [0x02]
2'. wait 0x02/0x82
3'. _cmd(0x00, 0x22, b"\x00", CAMERA)  (CURRENT)  →  expect pushes 0x00/0x24 (list) + 0x00/0x27 (data)
4'. ACK each unit: _cmd(0x00, 0x23, b"\x00", CAMERA)  (AckCcode Success=0; 0x22=UnableReceive to abort)
5'. advance selection: _cmd(0x00, 0x22, b"\x01", CAMERA)  (NEXT)
6'. resend gaps: _cmd(0x00, 0x25, <u32 index LE>, CAMERA)
7'. delete 0x00/0x28 ; exit 0x02/0x10 [0x01]
```

## 6. media.py rewrite plan (Python beta; code NOT edited per instructions)
- `enter_playback()`: change payload `[0x03]`→`[0x02]`. This is the single most important fix.
- Add `wait_playback_ready()`: block until a 0x02/0x80 push shows getMode==2 AND/OR a 0x02/0x82
  push arrives (subscribe in the existing push-dispatch), with a timeout + one retry of step 1.
- Keep `CID_FILE_LIST=0x20`, `CID_FILE_DATA=0x1F`, `CID_FILE_DELETE=0x28` (they DO exist in the
  native registry; CAMERA_MEDIA_RESEARCH_2026's "0x20 doesn't exist" claim is wrong — it looked at
  the camera 0x02 set, but 0x20 is on the common 0x00 set).
- Replace the KeyValue/ByteStreamHelper `file_list_request`/`file_data_request` blobs with the small
  native struct (index/count/slot/type for list; index/subtype/offset/size for data). Mark the exact
  bytes as "capture-pending" and log raw request+reply for the first hardware run.
- Implement the selective-ACK loop for 0x1F data pushes (window received-mask / missed-sections);
  without it the download stalls after the first window.
- Add the legacy 0x22/0x23/0x24/0x25/0x27 fallback behind a flag, in case firmware ignores 0x20.
- `exit_playback()`: 0x02/0x10 [0x01].
- Verify on hardware via pc_client: after [0x02] you should stop getting 1-byte 0xE0 and start
  seeing 0x02/0x82 pushes; then the list request should yield records instead of 0xE0.

## 7. Open items requiring ONE live capture (cannot finish statically)
- Exact on-wire bytes of 0x00/0x20 get_file_list_req and 0x00/0x1F get_file_data_req (native-built).
- The MediaFile list-record stride/field layout (parsed in libcross_playback C++).
- The 0x1F data-push window header + selective-ACK mask format.
- Confirm receiver id (0x01 camera vs 0x08 dm368 for the data pump) and cmd_type/ack bits.
- Definitive 0x20-vs-0x22 answer for live WM160 firmware (try 0x20 first post-mode-fix).

## 8. Cross-check status
Web-research subagent (dji-firmware-tools / MSDK / capture blogs) launched to independently confirm
0x20-vs-0x22 and the native list-record / data-chunk / selective-ACK byte layouts. Its findings will
be appended here when it returns. All §1-§7 facts are from static reverse of THIS app + the prior
native mining (MEDIA_TRANSPORT_TRUTH.md) and stand on their own.

---

## Historical correction (2026-07-22; superseded 2026-08-27)

This pass concluded that WM160 rejected the modern `0x20`/`0x1F` family with `0xE0` and selected the
following RequestSendFiles handshake. Later native and firmware work showed that the native commands are
real and that the retained hardware artifacts do not establish either family as selected. The sequence
below is preserved as the historical hypothesis, not the current verdict:
`0x00/0x22 (RequestSendFiles, payload 1B CURRENT=0)` → the list is PUSHED back as `0x00/0x24
(GetPushFiles)`; file bytes via `0x00/0x26 (RequestFile)` → `0x00/0x27 (GetPushFile)`; delete `0x00/0x28`.
Confirmed by app `Ccode`+`CmdIdCommon` smali AND dji-firmware-tools. See MEDIA_0XE0_RESEARCH_2026.md.
media.py was rewritten to this handshake.

<!-- PROGRESS: 100% — SUPERSEDED by MEDIA_0XE0_RESEARCH_2026.md: WM160 uses 0x22/0x24 (RequestSendFiles/GetPushFiles), NOT 0x20/0x1F (those NAK 0xE0 INVALID_CMD). media.py fixed. -->
