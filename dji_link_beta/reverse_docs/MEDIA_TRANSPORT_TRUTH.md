# MEDIA TRANSPORT — GROUND TRUTH (WM160 / Mavic Mini 1), verified against native `libsdk_jni.so`

Purpose: settle, from the **native** DJI Fly code (not the Java value-objects that `MEDIA_TRANSFER.md`
trusted), **why the drone gives ZERO reply** to our hand-built `0x00/0x20` (file list) and `0x00/0x1F`
(file data) frames over the AOA/RC-radio link, and what the real working sequence is.

**This document CORRECTS `MEDIA_TRANSFER.md` and `DOMAIN_media_album.md` on the two points that actually
matter.** Those docs are right that "media is DUML over AOA," but they were written from the Java
value-object serializers and explicitly deferred the two load-bearing facts to Frida (their H2 and H6). Both
of those deferred facts are the reason for the silence, and both are now resolved from the native binary.

> Primary new source: **`libsdk_jni.so`** (80 MB, ARM64, stripped of dynsym but **full C++ RTTI / log
> strings survive**), extracted to the analysis scratchpad. This is the CSDK native that every media call
> funnels into. All `uav::sdk::…`/`uav::core::…` symbols and quoted log strings below are `strings`/`nm`
> dumps from that binary. Java side: baksmali of `classes_03a5700c.dex` (media task/JNI) and
> `classes_0451d00c.dex` (value objects). Command registry: `full_table.txt` / `cmdmap.txt`.

---

## 0. TL;DR — the verdict

1. **Transport is NOT the problem.** Media list/thumbnail/download for an RC-connected WM160 rides the
   **same AOA DUML datalink** as flight/video — there is **no separate FTP/RNDIS/192.168/RTP socket** for
   the RC path. The native routes file tasks through the identical `BaseDataLinkServiceMgr` / `IServicePort`
   the whole SDK uses, and the AOA pipe is registered as a service port (`[Aoa-ServicePort] UsbDatalinkMgr`).
   The data comes back as DUML `file_transfer_push` frames with a windowed selective-ACK. (There *is* an
   HTTP/CURL downloader and a WiFi-high-speed path in the same binary — those are for **direct-WiFi and cloud
   products**, not the RC/AOA link. See §2.)

2. **Reason #1 for zero reply (highest-confidence): the camera is not in PLAYBACK / media-download mode.**
   The native **always enters playback mode first** (`CameraQuickModeModule::ActionEnterPlayback` →
   `switchPlaybackModeDirectly`, or, for legacy cameras, `SpecialCommandManager::EnterPlayback`). It tracks a
   per-device `download_mode` **separate** from `liveview_mode`, and it switches the camera work mode with
   **`0x02/0x10 set_camera_working_mode`** (native log: `"switch playback mode by 0x02 0x10 failed"`) and/or
   **`0x02/0x0C switch_playbackmode`**. In record/liveview mode — the mode in which takeoff and video work —
   the camera **silently ignores `0x00/0x20`**. This is the first thing to fix.

3. **Reason #2 for zero reply: the on-wire `0x00/0x20` payload we send is almost certainly the wrong bytes.**
   The `byte[]` that crosses the JNI boundary is **`FileTaskRequest.toBytes()`** — the CSDK's *internal task
   envelope*, **not** the on-wire DUML frame. The native **re-serializes** the actual
   `uav_general_get_get_file_list_req` itself inside `CommonFileDownloadHandler::RequestFileList()` before it
   ever hits the wire. So `MEDIA_TRANSFER.md`'s central premise — "the `FileListRequest.toBytes()` blob == the
   DUML payload" — is **unproven and most likely false**. Our `media.py` `file_list_request()` bytes are a
   guess at a structure the firmware never sees in that form.

4. **Verdict on the RC/radio path:** list+download **is achievable over the Pi/RC/AOA link in principle**
   (transport is fine), but **not with the current frames**. You must (a) drive the camera into playback/
   download mode first, and (b) send the *firmware's* `get_file_list_req` byte layout, which cannot be
   fully derived statically because the native builds it — **it needs one live capture** (or a Frida hook on
   `uav.sdk.…FileListRequest.toBytes` **vs** the raw `0x00/0x20` frame on the wire) to pin exactly. Until then
   any static payload (theirs or ours) is a hypothesis.

---

## 1. The real call chain (Java → native → wire)

```
Java:  UAVMediaManager.s()/g()/h()      (uav/media/UAVMediaManager.smali, DEX-03a5)
   └─> MediaTaskManager.getInstance(deviceType=0, componentIndex=0, LEFT_OR_MAIN)   ← default addressing
   └─> MediaBaseTask (wraps ONE FileTaskRequest: listReq / dataReq / streamReq)
   └─> JNIMediaTaskManager.fileTaskPushBack(J handle, I devType, I compIdx,
                                            FileTaskRequest, FileTaskCallback)
         → FileTaskRequest.toBytes()  →  byte[]   ← CSDK-INTERNAL ENVELOPE, not the wire payload
         → native_FileTaskPushBack(J,I,I,[B,cb)
── JNI boundary ───────────────────────────────────────────────────────────────────────────────
Native (libsdk_jni.so):
   JNI_FileTaskPushBack(JNIEnv*, jobject, jint, jint, jbyteArray, jobject)
     callback type = (int, shared_ptr<const uav::sdk::FileTaskResponse>, const uav::common::Buffer&)
   └─> uav::sdk::FileTaskManager  (queue; TryNextTask / SetSuspend / AddTaskToPending / StartPendingTask)
   └─> uav::sdk::FileTask::TransferFileListRequest()  → cb(int, shared_ptr<const FileList>)
   └─> uav::sdk::CommonFileDownloadHandler::RequestFileList()   ← BUILDS THE REAL uav_cmd_req HERE
         logs: "[CommonFileDownloadHandler] RequestFileList send data" / "... timeout"
               / "... failed res_id=0" / "Send GetFileList Pack Failed"
   └─> uav::sdk::FileTransferHandler::SendPack(const uav::core::uav_cmd_req&, cb)
         inner lambda: operator()(uint64_t session, const std::string& link_id, uint16_t seq)
   └─> BaseDataLinkServiceMgr / IServicePort  →  AOA service port  →  RC  →  drone
   Downlink: FileTransferHandler::OnReceiveDataPack(const uav::core::file_transfer_push::RspType*)
             + SendACKPack(TransmissionMissedSections) + SendAbortPack   ← windowed selective-ACK, all DUML
```

Proof points (symbols/log strings in `libsdk_jni.so`):
- `auto JNI_FileTaskPushBack(JNIEnv *, jobject, jint, jint, jbyteArray, jobject)::…operator()(int,
  std::shared_ptr<const uav::sdk::FileTaskResponse>, const uav::common::Buffer &)`
- `void uav::sdk::CommonFileDownloadHandler::RequestFileList()` and its logs quoted above.
- `uav::sdk::FileTransferHandler::SendPack(const uav::core::uav_cmd_req &, std::function<void(int,
  uav::core::uav_cmd_rsp *)>)`, `…::OnReceiveDataPack(const uav::core::file_transfer_push::RspType *)`,
  `…::SendACKPack(std::shared_ptr<const TransmissionMissedSections>)`, `…::SendAbortPack(...)`.
- `uav::core::uav_cmd_req` / `uav_cmd_rsp` = the native DUML command request/response type ⇒ **it is DUML**,
  and the payload is constructed by the native handler, **not** copied from the Java `byte[]`.

### Consequence for us (the key correction to MEDIA_TRANSFER.md §0)
The Java `byte[]` (`FileTaskRequest.toBytes()`) is consumed by `FileTaskManager` as **queue metadata**
(task type, priority, dedupe, the list/data/stream sub-request fields). The native then reads the fields it
needs and **emits its own `uav_general_get_get_file_list_req`**. Therefore:
- The on-wire `0x00/0x20` payload is **neither** `FileTaskRequest.toBytes()` **nor** the bare
  `FileListRequest.toBytes()` layout documented in `MEDIA_TRANSFER.md §2.2`. That layout is the CSDK
  KeyValue serialization, an *internal* format.
- Any Python that hand-packs `0x00/0x20` from those docs is sending a byte string the firmware does not parse
  → it is dropped with no reply (consistent with the observed silence), independently of the mode issue.

---

## 2. Transport, definitively: same AOA/DUML link — with two decoys to ignore

The binary contains **three** file-moving mechanisms. Only the first is the RC/AOA media path:

| mechanism | native class | transport | when used |
|---|---|---|---|
| **CSDK file task (media)** | `FileTaskManager`/`FileTask`/`CommonFileDownloadHandler`/`FileTransferHandler` | **DUML over the connected datalink service port** (`BaseDataLinkServiceMgr`→`IServicePort`; AOA port `[Aoa-ServicePort] UsbDatalinkMgr::fd`) | **photos/videos on any connected product, incl. RC-linked WM160** |
| generic common transfer | `CommonFileTransferHandler(WithBreakPoint)`, `ICommonTransferFileMgr`, `CommonTransferProviderImpl::TransferFile` | DUML `0x00/0x2A general_file_transfer` + `0x00/0x26 transfer_msg` | logs / calibration / firmware / offline-map — **NOT the gallery** |
| **HTTP download** | `FileTransferHTTPDownloadHandler::Start()(CURLcode,…)` (libcurl) | **TCP/HTTP** | **direct-WiFi products & cloud/SkyPixel** — not the RC/AOA link |

Datalink evidence (same manager for everything): `[AbstractionManagerDatalinkDetector][DataLinkConnect]
OnDataLinkAdd datalink_id:`, `AddWiFiDataLinkConnectStateObserver`, `[AOA]IsDataLinkAvailable write() return
code:`, `[Aoa-ServicePort]g_pModuleMediator->OnDataLinkAdded start`. There are AOA **and** WiFi datalinks;
media uses whichever the product is on. **WM160 over the RC = the AOA datalink**, i.e. the same
`55 CC | 0x5749 | … | <DUML>` composite pipe your `composite.py` already carries.

The `wifi_highspeed_download` / `LinkModeControlModule` (`NORMAL/UPLOAD/HIGHSPEED`) /
`dev_mgr_state WIFI_MUTEX … change use port usb->wifi` strings are **bandwidth/port arbitration for
direct-WiFi products** (phone connects straight to the aircraft's WiFi for fast bulk download). They are a
**decoy for our case** — WM160's link to the phone is the RC's AOA pipe, not a phone↔drone WiFi socket.

⇒ **There is no hidden media socket to find. If the drone answered mode+list, the bytes would come back on
the AOA DUML channel your bridge already reads.**

---

## 3. The PLAYBACK / DOWNLOAD-MODE precondition (Reason #1), with the exact frames

The native never issues a file task without first ensuring the camera is in the right mode. Evidence:

- `uav::sdk::CameraQuickModeModule::ActionEnterPlayback` / `ActionEnterPlaybackImpl` /
  `ExpectedInPlayback(bool,uint8_t)` / `switchPlaybackModeDirectly(bool,uint8_t)`; logs
  `"camera enter playback mode"`, `"send kEnterPlaybackEvent call ExpectedInPlayback"`,
  `"ActionEnterPlayback return instantly, already in playback mode"`.
- Device state tracked as `dev_state[conn:%u, liveview_mode:%u, download_mode:%u]` — **`download_mode` is a
  first-class, separate state from `liveview_mode`.** Plus `IsMediaDownloadModeSupported`,
  `CameraAbstraction::UpdateMediaDownloadModeDefaultValue`, `EnterPlaybackInvalidReason`,
  `IsIgnoreStorageStateForEnteringPlayback`.
- Legacy cameras (which WM160/FC7203, 2019, is) go through `uav::sdk::SpecialCommandManager::EnterPlayback(
  const std::string& link, uint16_t, const std::pair<uint8_t,uint8_t>& dev)` →
  `SpecialCommandOneDeviceImpl::SendSpecialControllPack`. The same manager also does
  `ShootPhotoBySpecialCommandAction` / `Start/StopRecordBySpecialCommandAction` — i.e. **on WM160, camera
  photo/record/playback are driven by these "special command" packs, not the modern KeyValue keys.**
- The mode switch on the wire (native log): **`"switch playback mode by 0x02 0x10"`** ⇒
  **`0x02/0x10 uav_camera_set_camera_working_mode`** (full_table.txt:`0x02/0x10 (16)`). The modern alias is
  **`0x02/0x0C uav_camera_switch_playbackmode`** (full_table.txt:`0x02/0x0C (12)`). Try `0x02/0x10` first for
  the Mini.

**Receiver/addressing:** the native resolves the target from the connected product's device table
(`dev[%d][dev_type:%u dev_index:%u dev_model:%u dev_sn:%s]`); the file/camera commands go to the **camera
device** (DUML receiver **`0x01`**, component `LEFT_OR_MAIN=0`), resolved dynamically — it is **not** set by
the Java layer (`MediaTaskManager.getInstance` passes `deviceType=0, componentIndex=0`). `receiver=0x01` is
the right guess; the **sender** should be the app's assigned id, and the frame's **`cmd_type`** (attr/ack
bits) is stamped natively — capture it (see §5). Note: cmd_set `0x00` is on the **non-encrypted** list
(`UAVEncryManager.c()` excludes COMMON), so DUML "SIMPLE" encryption is **not** the blocker for `0x1F/0x20/
0x28`.

---

## 4. The exact working sequence (what to actually do)

```
0. Be on the AOA/DUML link, product connected (you already are — video/telemetry/takeoff prove it).
1. ENTER PLAYBACK / DOWNLOAD MODE  →  camera (receiver 0x01):
      try  0x02/0x10 set_camera_working_mode  = PLAYBACK/DOWNLOAD    (Mini/legacy path; native uses this)
      and/or 0x02/0x0C switch_playbackmode (strategy)                (modern alias)
   Wait for the camera to report download_mode/playback (a camera-state push), NOT just the ack.
2. LIST  →  0x00/0x20 uav_general_get_get_file_list_req  to receiver 0x01
      payload = the FIRMWARE's get_file_list_req struct (index,count,slot=EXTERN1(0),type=MEDIA(0),…)
      — NOT FileListRequest.toBytes(). Exact bytes = capture (see §5).
   Reply: 0x00/0x20 rsp with the MediaFile records (paged; loop index+=count until listLeft==0).
3. DOWNLOAD  →  0x00/0x1F uav_general_get_get_file_data_req to receiver 0x01
      (index=fileIndex, type=ORIGIN/THUMBNAIL/SCREEN, offSet, dataSize, …).
   Data returns as windowed DUML pushes (file_transfer_push); you must SEND SELECTIVE-ACKs back
   (FileTransferHandler::SendACKPack / TransmissionMissedSections) or the pump stalls, and SendAbortPack to stop.
4. (video preview only) 0x02/0x7B single_playback_select + 0x02/0x7A video_playback_control — optional.
5. DELETE  →  0x00/0x28 delete_file_req (FileActionRequest), receiver 0x01.
```

The two things `MEDIA_TRANSFER.md` got wrong / left open, restated: **step 1 is mandatory** (it treated it as
"optional"), and **the step-2/3 payloads are native-built** (it treated `toBytes()` as the wire bytes).

---

## 5. What still MUST come from one live capture (can't be finished statically)

The native re-serializes requests and stamps the DUML header, so these are undecidable from the binary alone:

1. **The on-wire `get_file_list_req` (0x00/0x20) byte layout** and **`get_file_data_req` (0x00/0x1F)** —
   Frida-hook `uav.sdk.keyvalue.value.file.FileListRequest.toBytes([BI)I` return **AND** the raw `0x5749`
   composite unit leaving the AOA writer; diff them. If they match, MEDIA_TRANSFER's layout is vindicated; if
   not (expected), the raw frame is your struct.
2. **The exact enter-playback frame for WM160** — hook `SpecialCommandManager::EnterPlayback` /
   `CameraQuickModeModule::switchPlaybackModeDirectly` (or just capture the `0x02/0x10` / `0x02/0x0C` frame the
   app sends when you open the album) → get cmd_id, the mode enum value, and the payload width.
3. **DUML header bytes** the native stamps: **sender**, **receiver** (confirm `0x01` vs `0x08` dm368 for the
   `0x1F` data pump), **seq**, and **`cmd_type`/ack bits**.
4. **The `0x00/0x1F` data-chunk framing + selective-ACK** (`file_transfer_push` window header) — capture one
   real download; the Java side only ever sees reassembled `(offset,data)`.

Cheapest path to all four: put the phone (or the Pi in MITM) between RC and drone, open the DJI Fly album on a
WM160, and record the `0x5749` DUML units for: album-open (mode switch), grid-load (list), and one download.

---

## 6. Bottom line

- **Radio/AOA is capable of media** — same datalink, no secret socket. Good news for the Pi.
- **The silence is caused by (1) not being in playback/download mode, and (2) sending an internal
  value-object blob instead of the firmware's DUML `get_file_list_req`** — either one alone yields zero reply,
  and we currently do both.
- **`MEDIA_TRANSFER.md` / `DOMAIN_media_album.md` remain useful for semantics** (enums, field meanings, paging,
  MediaFile fields) but are **wrong on the wire bytes and on "playback optional."** Treat their `toBytes`
  layouts as the SDK-internal format, not the wire, until a capture confirms otherwise.
- **Next action:** one live album capture from a real WM160 nails items 1–4 in §5; then the RC-path
  implementation is exact.

### Source index
- `libsdk_jni.so` (scratchpad): `JNI_FileTask*`, `uav::sdk::FileTaskManager`, `uav::sdk::FileTask`,
  `uav::sdk::CommonFileDownloadHandler::RequestFileList`, `uav::sdk::FileTransferHandler::{SendPack,
  OnReceiveDataPack,SendACKPack,SendAbortPack}`, `FileTransferHTTPDownloadHandler`, `BaseDataLinkServiceMgr`,
  `[Aoa-ServicePort]`, `CameraQuickModeModule::ActionEnterPlayback`, `SpecialCommandManager::EnterPlayback/
  SendSpecialControllPack`, `dev_state[…liveview_mode…download_mode…]`, `IsMediaDownloadModeSupported`,
  log `"switch playback mode by 0x02 0x10 failed"`.
- `classes_03a5700c.dex`: `uav/media/album/jni/JNIMediaTaskManager.smali` (all methods pass
  `FileTaskRequest.toBytes()` as `[B`), `uav/media/album/MediaTaskManager.smali`
  (`getInstance(0,0,LEFT_OR_MAIN)`), `uav/media/FileOperateHelper.smali` (slot=EXTERN1).
- `full_table.txt`: `0x00/0x1F`, `0x00/0x20`, `0x00/0x28`, `0x02/0x0C`, `0x02/0x10`, `0x02/0x7A`, `0x02/0x7B`.
- `DUML_ENCRYPTION.md`: cmd_set `0x00` (COMMON) is excluded from SIMPLE encryption ⇒ not the media blocker.

---

## Why 0xe0 (root cause) — RESOLVED against live hardware + `libsdk_jni.so` + `CameraWorkMode` enum

> New empirical state (supersedes the "zero reply" premise above): over the RC/AOA DUML link the camera
> now **answers**. `0x02/0x10 set_camera_working_mode` → `0x00` SUCCESS and liveview freezes (mode really
> changed). But `0x00/0x20 get_file_list`, `0x00/0x1F get_file_data`, `0x02/0x0C switch_playbackmode`,
> `0x02/0x09 set_liveview_source`, `0x02/0xB3 get_app_request_i_frame` all reply a **single byte `0xe0`**,
> identical for every payload layout. A defined 1-byte reply (not silence) proves the frame is well-formed,
> reaches camera device `0x01`, and is parsed — so transport, addressing, seq, cmd_type and payload are all
> fine. `0xe0` is a **return-code**, not a parse error.

### 1. What `0xe0` (224) means
`0xe0` is the DJI **camera/general firmware return code for "command refused — not available in the current
state / not supported right now"** (a generic NAK), *not* a per-payload error. Evidence:
- Native `libsdk_jni.so` hardcodes exactly this code as its "unsupported/refused" sentinel:
  `"QueryIsDeveloperSettingSupportByStaticCapability ret_code 0xE0"` — the SDK stamps `ret_code 0xE0` when a
  feature is **not available per the device's static capability**. `0xE0` = the SDK/firmware's
  "not-supported/refused" value.
- Behavioural proof it is a state/precondition NAK and **not** payload parsing: the code is byte-identical
  across every request-body we send (empty / `[index,count]` / full CSDK envelope), and it is returned by an
  *entire family* of unrelated commands at once (file list, file data, playbackmode switch, liveview-source,
  i-frame), while a sibling command in the same cmd_set (`0x02/0x10`) returns `0x00`. A parser would vary with
  the bytes; a "wrong-state / cmd-not-serviceable-now" gate does not. (cmd_set `0x00` is on the non-encrypted
  list per `DUML_ENCRYPTION.md`, re-confirmed — encryption/attr bits are **not** the cause; §3 of this doc.)

### 2. The exact missing precondition — WRONG WORK MODE (`PLAYBACK` instead of `MEDIA_DOWNLOAD`)
The camera exposes a **10-value work-mode enum**, recovered authoritatively from the CSDK value object
`uav/sdk/keyvalue/value/camera/CameraWorkMode.smali` (`value:I` = the byte carried by `0x02/0x10`):

```
SHOOT_PHOTO=0  RECORD_VIDEO=1  PLAYBACK=2  MEDIA_DOWNLOAD=3  TURNING=4
POWER_SAVE=5   DOWNLOAD=6      TRANSCODE=7 BROADCAST=8       UNKNOWN=9
```

`PLAYBACK (2)` and `MEDIA_DOWNLOAD (3)` are **distinct modes**. Freezing liveview only proves we entered
*some* non-liveview mode (we sent `PLAYBACK`); it does **not** put the camera into the state that services
file operations. The file/download command family (`0x00/0x1F`, `0x00/0x20`, `0x00/0x28`) is serviced **only
in `MEDIA_DOWNLOAD (3)`**. In any other mode the camera refuses them with `0xe0`. Native + app evidence that
the media path is gated on a *separate* download state, not on liveview/playback:
- `dev_state[conn:%u, liveview_mode:%u, download_mode:%u | …]` — the camera tracks **`download_mode` as a
  first-class state distinct from `liveview_mode`**. (native)
- `IsMediaDownloadModeSupported`, `CameraAbstraction::UpdateMediaDownloadModeDefaultValue`,
  `native_CheckDownloadInvalidReason` / `native_AddDownloadInvalidReasonCallback` — the SDK explicitly checks
  a **media-download-mode / download-invalid-reason** gate before file ops. (native)
- App picks this mode by name for media: `uav/pilot/fpv/camera/util/UAVCameraUtil.smali` method `l(...)`
  branches on `CameraWorkMode.PLAYBACK` **and** `CameraWorkMode.MEDIA_DOWNLOAD` (and `TRANSCODE`) — media
  browsing/downloading rides `MEDIA_DOWNLOAD`, not plain `PLAYBACK`. (DEX-0451)

So we entered `PLAYBACK (2)`; the firmware wants `MEDIA_DOWNLOAD (3)`; hence `0xe0` on the whole file family.
(The `0x02/0x0C`, `0x02/0x09`, `0x02/0xB3` `0xe0`s are a *separate* fact — those cmd_ids are simply not
implemented on the 2019 WM160/FC7203 firmware, which is why the app uses the legacy `SpecialCommandManager` /
`0x02/0x10` path for the Mini, per §3 above. They are not needed for listing.)

### 3. Is it a different device / not-plaintext / cmd_type issue?  No.
- Same camera device `0x01`, same AOA DUML link — a `0xe0` *reply* from `0x01` is the camera answering, not a
  "not-me". `0x02/0x10` succeeding on the same address/link proves addressing + framing are correct (re Q4/Q3).
- cmd_set `0x00` is non-encrypted (`UAVEncryManager` excludes COMMON); no attr/ack bit is missing — a missing
  cmd_type would not yield a clean per-command `0xe0` from a mode-specific subset (re Q3).

### 4. Concrete corrected steps to make `0x00/0x20` return real data
```
1. ENTER MEDIA-DOWNLOAD MODE (the fix):
   0x02/0x10 set_camera_working_mode  →  receiver camera 0x01, mode value = 3 (MEDIA_DOWNLOAD)
     (NOT 2/PLAYBACK — that is what we were doing.)  Expect 0x00 SUCCESS; liveview stays frozen.
     Fallback if 3 is rejected on this firmware: try value 6 (DOWNLOAD).
2. (recommended) confirm the camera actually reports download_mode active before listing — poll the camera
   status/condition push; do not rely on the 0x00 ack alone (native waits on the state, not the ack).
3. LIST:  0x00/0x20 get_file_list to camera 0x01 → now returns the real get_file_list_rsp (MediaFile records),
   no longer 0xe0.  Page with index/count until isPageLastFile / listLeft==0.
4. DATA / DELETE: 0x00/0x1F, 0x00/0x28 to 0x01 in the same MEDIA_DOWNLOAD mode.
5. When done, restore liveview: 0x02/0x10 mode = 1 (RECORD_VIDEO) or 0 (SHOOT_PHOTO).
```
If value 3 (and 6) still return `0xe0` after confirming download_mode did not change, the remaining
hypothesis is the **legacy Mini media path** (`SpecialCommandManager` / P3 `DataCameraSetQuickPlayBack`),
but the work-mode fix above is the evidence-backed first move and matches every observed symptom.

### Source index (this section)
- `libsdk_jni.so` (scratchpad): `"… ret_code 0xE0"`, `dev_state[…liveview_mode…download_mode…]`,
  `IsMediaDownloadModeSupported`, `UpdateMediaDownloadModeDefaultValue`, `native_CheckDownloadInvalidReason`,
  `native_AddDownloadInvalidReasonCallback`, `CameraQuickModeModule::{ActionEnterPlayback,switchPlaybackModeDirectly}`.
- `classes_0451d00c.dex` → `uav/sdk/keyvalue/value/camera/CameraWorkMode.smali` (enum values above, `value:I`),
  `uav/pilot/fpv/camera/util/UAVCameraUtil.smali` `l(CameraWorkMode)` (PLAYBACK+MEDIA_DOWNLOAD+TRANSCODE branch),
  `uav/sdk/keyvalue/key/UAVCameraKey.smali` (set-working-mode key).
- `full_table.txt`: `0x02/0x10 uav_camera_set_camera_working_mode`, `0x00/0x20 get_file_list`.

## Native disassembly — the real file-list sequence

> Fully static-reversed from the **real** `libsdk_jni.so` ELF (git-LFS blob
> `.git/lfs/objects/01/7d/017d65e3…`, 80 MB AArch64). The binary is stripped of `.dynsym` and its
> section headers are corrupted (DJI anti-analysis), but a **complete `.symtab` survives** hidden at
> file offset `0x2f8..0x1a9128` (72 514 `Elf64_Sym`, indexing the `.strtab` at `0x2414a0`). Recovered it
> and mapped every symbol → VA (`VA == file offset` in the first R-E LOAD). All VAs below are from that
> map; every claim is disassembled, not inferred from strings. This resolves §5 items 1–4 and **corrects
> the earlier "just use work-mode 3" conclusion** — which the hardware already disproved (mode 3 accepted,
> `0x00/0x20` still `0xe0`).

### A. The uav_cmd header encoding (decoded, decisive)
`uav_general_get_get_file_list_req` ctor **@ `0x27bb464`** (RTTI type
`uav_cmd_base_req<1,0,32,uav_general_get_get_file_list_req,…>`) writes a 4-byte command head at struct
offset 0 = `0x02200001` → **byte[0]=flags `0x01`(hasResp) · byte[1]=cmd_set · byte[2]=cmd_id ·
byte[3]=cmd_type `0x02`**. It also stamps route/link magic **`0x5749`** at +0xC and default timeout
**500 ms** (`0x1F4`) at +0x14. So on the wire this is exactly **cmd_set `0x00` / cmd_id `0x20`,
cmd_type `0x02`** — our frame identity was already correct. The same encoding is used everywhere, which
lets every other command below be read off its ctor.

### B. Why `0x00/0x20` returns `0xe0` — the real gate (crux)
The camera services the file family **only after it has actually entered playback/download state AND
reported it back in a status push.** The native never fires the list on the mode-set ack:

- `CameraQuickModeModule::ActionEnterPlaybackImpl` **@ `0x26aca90`** (string refs: `"IsPlayingBack"`,
  `"ActionEnterPlayback return instantly, already in playback mode"`, `"CameraWorkMode"`,
  `"ActionEnterPlayback have no SpeicalCommandManager or have no abstraction"`,
  `" send kEnterPlaybackEvent call ExpectedInPlayback"`, `"EnterPlaybackEvent"`) does:
  read `IsPlayingBack` → if already true, return; else fire `kEnterPlaybackEvent` and call
  `CameraQuickModeModule::ExpectedInPlayback(bool,uint8_t)` **@ `0x26adc2c`**.
- `ExpectedInPlayback` is a **state machine that waits on the camera status push**, not on the ack.
  The push it waits for is `uav_camera_push_camera_status_info_push`, parsed by
  `KeyIsPlayingBackPush` **@ `0x3469f98`** and `KeyCameraWorkModePush` **@ `0x34509f4`** — i.e. the
  camera's periodic **camera-status-info push** carries both `IsPlayingBack` and the live `CameraWorkMode`.
  `FileTaskManager` keeps the list task **pending/suspended** until that push says the camera is in
  playback/download; only then is `CommonFileDownloadHandler::RequestFileList` **@ `0x216e3d0`** run and
  `0x00/0x20` emitted (via `SendPack`, `0x4a15840`).
- For the camera to emit that push at all the SDK subscribes with **`0x02/0xEB
  set_camera_status_subscribe`** (`full_table.txt`).

**Therefore `0xe0` = "you asked for the list before the camera confirmed it is in the serviceable
download state."** Sending `0x02/0x10 mode=3` and immediately firing `0x00/0x20` (what we do) races the
gate: the camera acks the mode change, freezes liveview, but has **not yet** entered/confirmed the
download state, so every `0x00/0x20` in that window is refused with `0xe0` — in *every* mode, exactly as
observed.

### C. Why mode 3 alone is not enough on WM160 — the legacy special-command path
Which mechanism actually drives the camera into playback is chosen by a KeyValue strategy:
`CameraQuickModeModule::getSwitchPlaybackModeStrategy` **@ `0x26ab52c`** reads key **`SwitchPlaybackModeStrategy`**
and returns one of `SwitchPlaybackModeStrategy` / `NonFlatModeSwitchPlaybackModeStrategy` /
`SpecailCommandSwitchPlaybackModeStrategy` (inits @ `0x34dc750` / `0x34dca78` / `0x34dc8e4`).
- Modern/"flat" strategy → `switchPlaybackModeDirectly(bool,uint8_t)` **@ `0x26ad994`**, which builds a
  small `{0x0C, 3, 1}` mode message → the `0x02/0x10`(=16) work-mode set with value **3**. This is the
  path we already exercise; the camera **acks** it but on a 2019 WM160/FC7203 it does not by itself flip
  the download state the file family is gated on.
- **Legacy strategy (WM160) → `SpecialCommandManager::EnterPlayback`** **@ `0x4658a38`**. It does NOT send
  a KeyValue/camera command; it sets an action byte and device bits into the SpecialCommand device struct
  (`|=3` at +0x50, dev packed at +0x5a, action `6` at +0x74) that is transmitted by
  `SpecialCommandOneDeviceImpl::SendSpecialControllPack` **@ `0x465b4d8`** as
  **`uav_special_special_ctrl_push` = cmd_set `0x01` / cmd_id `0x01`** (ctor
  `uav_cmd_base_req<1,1,1,…special_ctrl_push>` **@ `0x465bbd8`**, head `0x02010101`). It is an **11-byte
  bit-packed control pack** (9 data bytes at struct +0x50..+0x58, **XOR checksum** of those 9 at +0x59,
  device/flags at +0x5a) **sent repeatedly on a timer** (`StartTimer` → periodic `SendSpecialControllPack`;
  retry counter at +0x6c capped at 0x14), targeted at the RC/"glass" videocore receiver
  (`UpdateReceiver` @ `0x4659f34`, `"[SpecialCommandManager] receiver change to ("`). The same 0x01/0x01
  push also carries ShootPhoto/Start-StopRecord/I-frame for legacy cameras (bit-selected by byte +0x55).

⇒ On WM160 the app enters real playback/download via the **legacy `0x01/0x01` special-ctrl push**, not the
`0x02/0x10` work-mode set we rely on. That is the missing precondition — plus the wait on the status push.

### D. The `0x00/0x20` request payload (field layout)
Two builders exist; both re-serialize natively (never the Java `toBytes()`):
- `CommonFileDownloadHandler::RequestFileList` **@ `0x216e3d0`** emits the bare
  `uav_general_get_get_file_list_req` (the `0x00/0x20` we send).
- The session/filter layout is authoritative in `ListTransferRequest::ConfigFilterData(uav_file_list_download_req*)`
  **@ `0x20d47cc`** (log `"[FileMgr] Recieve not support filter:"`): into the request struct it writes a
  **type/storage byte at +8**, a **4-byte field at +9 initialised to `0xFFFFFFFF`** (= "all" index/count),
  and a **filter bitmap dword at +0xD** whose bits are OR-ed per requested media type
  (`|=1,2,4,8,0x10,0x20` for the file-type/subtype filters). Ordering info defaults ("filelist order info
  is default"). The transfer-session variant is opened by
  `ListTransferRequest::CreateStartRequestPack` **@ `0x20d4bb4`** (`"virtual uav_cmd_req …CreateStartRequest…"`).
  The exact byte packing of the final `get_file_list_req` body still wants one live capture to pin field
  widths, but the gate (B/C) is the reason for `0xe0`, not the body.

### E. Ordered frames to make `0x00/0x20` return data (Python)
```
0. Connected on AOA/DUML (you are).
1. SUBSCRIBE camera status:      0x02/0xEB set_camera_status_subscribe  → camera 0x01
      so the camera starts pushing uav_camera_push_camera_status_info_push.
2. ENTER PLAYBACK / DOWNLOAD (do BOTH; WM160 needs the legacy one):
     a. 0x02/0x10 set_camera_working_mode, value = 3 (MEDIA_DOWNLOAD)   → camera 0x01   (modern/flat)
     b. 0x01/0x01 uav_special_special_ctrl_push  (legacy enter-playback)               (send REPEATEDLY,
        11-byte pack: 9 action bytes + XOR-checksum byte + device/flag byte; enter-playback action)
        — keep re-sending on a ~100–200 ms timer, exactly like SendSpecialControllPack, until step 3.
3. WAIT (do NOT race): parse the incoming camera status push and block until it reports
      IsPlayingBack / download_mode ACTIVE (KeyIsPlayingBackPush / KeyCameraWorkModePush). Only then:
4. LIST:  0x00/0x20 get_file_list  → camera 0x01, cmd_type 0x02, route 0x5749
      body = get_file_list_req (index/count = 0xFFFFFFFF for all, storage=SDCARD, type filter bitmap).
      Now returns real records instead of 0xe0. Page until listLeft==0.
5. DATA/DELETE: 0x00/0x1F, 0x00/0x28 in the same confirmed download state.
```
The one behavioural change vs. our current client: **stop firing `0x00/0x20` on the mode-set ack**; add
the periodic `0x01/0x01` special-ctrl push and gate the list on the camera's `IsPlayingBack` status push.

### Source index (this section)
`libsdk_jni.so` recovered `.symtab` (offset `0x2f8`): ctors `uav_general_get_get_file_list_req`@`0x27bb464`,
`uav_special_special_ctrl_push`@`0x465bbd8`; `CameraQuickModeModule::{ActionEnterPlaybackImpl@0x26aca90,
ExpectedInPlayback@0x26adc2c, getSwitchPlaybackModeStrategy@0x26ab52c, switchPlaybackModeDirectly@0x26ad994}`;
`SpecialCommandManager::EnterPlayback@0x4658a38`, `SpecialCommandOneDeviceImpl::SendSpecialControllPack@0x465b4d8`,
`UpdateReceiver@0x4659f34`; `CommonFileDownloadHandler::RequestFileList@0x216e3d0`;
`ListTransferRequest::{ConfigFilterData@0x20d47cc, CreateStartRequestPack@0x20d4bb4}`;
`KeyIsPlayingBackPush@0x3469f98`, `KeyCameraWorkModePush@0x34509f4`. `full_table.txt`: `0x00/0x20`,
`0x01/0x01 uav_special_special_ctrl_push`, `0x02/0x10`, `0x02/0xEB set_camera_status_subscribe`.
