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
