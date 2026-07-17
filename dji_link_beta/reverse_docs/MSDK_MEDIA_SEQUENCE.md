# MSDK media/file-list sequence vs. our DUML `0xe0` NAK (WM160 / Mavic Mini 1)

Goal: pin the exact precondition the DJI Mobile SDK (and DJI Fly's embedded CSDK) satisfies before
`get_file_list`, so we can add the missing step to our DUML client. Our symptom: `0x02/0x10
set_camera_working_mode` returns `0x00` (liveview freezes, mode really changed), but `0x00/0x20
get_file_list` (and `0x00/0x1F`, `0x00/0x28`) returns a 1-byte `0xe0` for every payload/mode we tried.

Cross-checked against: DJI MSDK Android/iOS `MediaManager` docs + tutorial, MSDK sample repos, and the
local decompile (`MEDIA_TRANSFER.md`, `MEDIA_TRANSPORT_TRUTH.md`, `DOMAIN_media_album.md`,
`libsdk_jni.so`, `CameraWorkMode.smali`).

---

## VERDICT (one line)

We put the camera in **`PLAYBACK` (work-mode value 2)**; the file/download command family is serviced
**only in `MEDIA_DOWNLOAD` (work-mode value 3)**. `0xe0` = firmware "command refused, not available in
this state." Fix: send `0x02/0x10 set_camera_working_mode` with **mode byte = 3 (MEDIA_DOWNLOAD)**, wait
for the camera to actually report `download_mode` active (not just the `0x00` ack), then send `0x00/0x20`.

---

## 1. The EXACT documented MSDK call sequence for the SD file list

From the DJI MSDK Android MediaManager tutorial/sample (`refreshFileListOfStorageLocation`,
`getSDCardFileListSnapshot`) and the iOS equivalent (`refreshFileListOfStorageLocation:`,
`fileListSnapshot`). Every step is mandatory and each waits on a callback/state:

1. **`camera.setMode(CameraMode.MEDIA_DOWNLOAD, completion)`**
   - Must **await the `onResult`/completion callback with no error** before doing anything else. This is
     a real async mode change on the camera, not a fire-and-forget. Sample code only calls `getFileList()`
     *inside* that success callback.
   - `MEDIA_DOWNLOAD` is a **distinct** `CameraMode` from `PLAYBACK`. It is also **decoupled from
     `FlatCameraMode`** — `setFlatMode()` only sets photo/video sub-modes and **will not** enter media
     download. You must use `setMode(MEDIA_DOWNLOAD)`. (MSDK docs: "MEDIA_DOWNLOAD ... download media to
     the Mobile Device"; "setFlatMode only works for photo/video modes and won't help with downloading
     media from the SD card.")
2. **`mediaManager.addUpdateFileListStateListener(listener)`** — subscribe to `FileListState` changes.
3. **Gate on `FileListState`**: read `currentFileListState`; only proceed to refresh when it is
   **neither `SYNCING` nor `DELETING`** (those mean the manager is busy). States seen:
   `UNKNOWN / RESET / SYNCING / INCOMPLETE / UP_TO_DATE(IDLE) / DELETING`.
4. **`mediaManager.refreshFileListOfStorageLocation(StorageLocation.SDCARD, completion)`** — this is the
   call that actually **pulls the list from the SD card**. Storage location argument is explicit
   (`SDCARD` vs `INTERNAL_STORAGE`); WM160 has only SD.
5. **On refresh success**, read the snapshot: **`mediaManager.getSDCardFileListSnapshot()`** (iOS:
   `fileListSnapshot`). This returns the `MediaFile[]`. There is a **refresh/pull vs read distinction**:
   `refreshFileList...` = go get it; `getSDCardFileListSnapshot` = read the cached result.

So the ordered gate is: **MEDIA_DOWNLOAD mode (await cb) → subscribe state → wait state ∉ {SYNCING,
DELETING} → refreshFileListOfStorageLocation(SDCARD) (await cb) → getSDCardFileListSnapshot**.

## 2. Which step maps to our DUML `0x00/0x20`, and the precondition we're missing

- `refreshFileListOfStorageLocation(SDCARD)` is what turns into the on-wire **`0x00/0x20
  get_file_list`** family (native `CommonFileDownloadHandler::RequestFileList()` → `FileTransferHandler::
  SendPack`).
- The precondition we omit is **step 1 done correctly**: being in **`MEDIA_DOWNLOAD`**, not `PLAYBACK`.
  Freezing liveview only proved we left liveview into *some* non-liveview mode — we sent `PLAYBACK (2)`,
  which is the wrong one. The file family (`0x00/0x1F/0x20/0x28`) is gated on a **separate
  `download_mode`** state the firmware tracks independently of `liveview_mode`/`playback`:
  - native `dev_state[conn, liveview_mode, download_mode]` — `download_mode` is first-class and separate;
  - native `IsMediaDownloadModeSupported`, `UpdateMediaDownloadModeDefaultValue`,
    `native_CheckDownloadInvalidReason` — an explicit media-download gate is checked before file ops;
  - app `UAVCameraUtil.l(CameraWorkMode)` branches on `MEDIA_DOWNLOAD` (and `PLAYBACK`, `TRANSCODE`) —
    browsing/downloading rides `MEDIA_DOWNLOAD`.
  This is exactly the MSDK `RESET → SYNCING → UP_TO_DATE` pull gate expressed one layer down: the mode
  must be `MEDIA_DOWNLOAD` first, then the pull; in any other mode the camera refuses with `0xe0`.

## 3. Camera mode: enum value, setMode vs setFlatMode, callback

- **Wire enum** (authoritative, from `CameraWorkMode.smali`, `value:I` = the byte carried by `0x02/0x10`):
  `SHOOT_PHOTO=0, RECORD_VIDEO=1, PLAYBACK=2, MEDIA_DOWNLOAD=3, TURNING=4, POWER_SAVE=5, DOWNLOAD=6,
  TRANSCODE=7, BROADCAST=8, UNKNOWN=9`. **Use value 3.** (Fallback to test: 6 = `DOWNLOAD`.)
- **setMode, not setFlatMode**: media download is decoupled from `FlatCameraMode`; `setFlatMode` cannot
  reach it. At the DUML level our `0x02/0x10 set_camera_working_mode` is the right command — we were just
  passing the wrong value (2 instead of 3).
- **A mode-change confirmation MUST be awaited — our `0x00` ack is not enough.** MSDK gates the file list
  inside the `setMode` completion callback; the native waits on the reported `download_mode` state, not on
  the command ack. Sending `0x00/0x20` immediately after the `0x02/0x10` ack can still hit `0xe0` if the
  camera hasn't transitioned yet. Poll a camera status/condition push confirming download-mode before
  listing (or at minimum insert a short delay and retry).

## 4. Does WM160 expose media over MSDK, or only DJI Fly?

- **Officially, Mavic Mini 1 (WM160) is NOT a public-MSDK aircraft.** DJI added MSDK support for Mini 2 /
  Mini SE / Air 2S in Jan 2022; the original Mini 1 (2019, camera `FC7203`, device id 59/`0x3B`) was a
  DJI GO/DJI Fly-only product and never got a public MSDK. So the public MSDK MediaManager API is a
  **documentation analogue**, not a supported runtime path, for this drone.
- **But the underlying protocol is identical.** DJI Fly embeds the same CSDK; its media pipeline
  (`UAVMediaManager` → `MediaTaskManager` → `JNIMediaTaskManager` → `libsdk_jni`
  `CommonFileDownloadHandler`) emits the **same DUML `0x00/0x20` / `0x00/0x1F` / `0x00/0x28`** to the
  camera. For a legacy camera like the Mini, DJI Fly enters the mode via the **`SpecialCommandManager` /
  `0x02/0x10 set_camera_working_mode`** path (native log: `"switch playback mode by 0x02 0x10"`) rather
  than the modern KeyValue keys — which is exactly why `0x02/0x0C switch_playbackmode`, `0x02/0x09`,
  `0x02/0xB3` each return `0xe0` on this firmware (those cmd_ids simply aren't implemented on WM160/FC7203;
  they are **not needed** for listing). So: media over DUML/AOA is achievable on WM160; the app just drives
  it through the legacy work-mode command, not the modern playback keys.

## 5. Concrete DUML-level action to try next (the missing step)

```
1. ENTER MEDIA-DOWNLOAD MODE  (the fix):
     0x02/0x10 set_camera_working_mode → receiver camera 0x01, mode byte = 3 (MEDIA_DOWNLOAD)
        NOT 2/PLAYBACK.  Expect 0x00 SUCCESS; liveview stays frozen.
        Fallback to probe if 3 → 0xe0: mode byte = 6 (DOWNLOAD).
2. WAIT FOR THE STATE, NOT THE ACK:
     poll the camera status/condition push and confirm download_mode is active before listing
     (or insert a short delay + retry the list a few times). The native waits on the state.
3. LIST:
     0x00/0x20 get_file_list → camera 0x01, slot = EXTERN1 (0), FileType = MEDIA (0).
     Should now return get_file_list_rsp (MediaFile records) instead of 0xe0. Page with index/count.
4. DATA / DELETE in the same mode: 0x00/0x1F, 0x00/0x28 → 0x01.
5. RESTORE liveview when done: 0x02/0x10 mode = 1 (RECORD_VIDEO) or 0 (SHOOT_PHOTO).
```

Caveat carried from the local decompile: the exact on-wire `get_file_list_req` **body** is re-serialized
natively (`FileTaskRequest.toBytes()` is a CSDK-internal envelope, not the wire payload), so if `0x00/0x20`
returns `0x00`/data-but-empty after the mode fix, the payload byte layout is the next thing to confirm via
one live album capture. But the mode fix (value 3) is the evidence-backed cause of the `0xe0` and the first
thing to change.

---

### Sources
- Local: `reverse_docs/MEDIA_TRANSPORT_TRUTH.md` §"Why 0xe0 (root cause)" (authoritative — `CameraWorkMode`
  enum, `download_mode` gate, `ret_code 0xE0` sentinel in `libsdk_jni.so`), `MEDIA_TRANSFER.md`,
  `DOMAIN_media_album.md`, `full_table.txt` (`0x02/0x10`, `0x00/0x20/0x1F/0x28`).
- Web: DJI MSDK Android MediaManager tutorial & `DJIMediaManager` API reference (`refreshFileListOfStorageLocation`,
  `getSDCardFileListSnapshot`, `FileListState`); iOS `DJIMediaManager.h`; `DJICameraSettingsDef.CameraMode`
  (MEDIA_DOWNLOAD distinct from PLAYBACK; decoupled from FlatCameraMode; setFlatMode won't enter it);
  DJI product-SDK compatibility (Mini 1 not a public-MSDK aircraft; MSDK added for Mini 2/SE/Air 2S in 2022).
