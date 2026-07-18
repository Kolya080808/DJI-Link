# CAMERA / MEDIA RESEARCH 2026 — WM160 (Mavic Mini 1, UAV59, legacy camera)

Ground truth = `dji-sdk-provided-4.18.jar` (un-obfuscated class names, `javap -p -c`).
Numbers below are the raw wire bytes read straight from bytecode. Enum ctor is
`<init>(String name, int ordinal, int VALUE)`; **VALUE (2nd int) is the wire byte**.

## 0. Framing constants (verified from bytecode, correcting old assumptions)

| thing | class / evidence | wire |
|---|---|---|
| CmdSet.COMMON | `CmdSet.<clinit>` ordinal0 val0 | **0x00** |
| CmdSet.CAMERA | `CmdSet.<clinit>` ordinal2 val2 | **0x02**  ← NOT 0x01 |
| DeviceType.CAMERA | `DeviceType.<clinit>` | **0x01** (receiver) |
| DeviceType.APP | `DeviceType.<clinit>` | 0x02 (our sender) |
| CmdIdCamera.SetPhoto | enum val 1 | 0x01 |
| CmdIdCamera.SetRecord | enum val 2 | 0x02 |
| CmdIdCamera.SetMode | enum val 16 | 0x10 |
| CmdIdCamera.GetMode | enum val 17 | 0x11 |
| CmdIdCamera.SetExposureMode | enum val 30 | 0x1E |
| CmdIdCamera.SetIso | enum val 42 | 0x2A |
| CmdIdCamera.GetPushStateInfo | enum val 128 | 0x80 (push) |
| CmdIdCamera.GetPushPlayBackParams | enum val 130 | 0x82 (push) |
| CmdIdCamera.GetFileParams | enum val 152 | 0x98 |
| CmdIdCommon.RequestSendFiles | enum val 34 | 0x22 |
| CmdIdCommon.AckReceiveFiles | enum val 35 | 0x23 |
| CmdIdCommon.GetPushFiles | enum val 36 | 0x24 (push = file list) |
| CmdIdCommon.SetResendFiles | enum val 37 | 0x25 |
| CmdIdCommon.RequestFile | enum val 38 | 0x26 |
| CmdIdCommon.GetPushFile | enum val 39 | 0x27 (push = file data) |
| CmdIdCommon.DeleteFile | enum val 40 | 0x28 |
| CmdIdCommon.TransferFile | enum val 42 | 0x2A |

`CmdSet.CAMERA == 0x02` is confirmed (our code's `CMDSET_CAMERA = 0x02` is right; the old
"0x01" note in the header comment is wrong). Photo/record/ISO all ride cmd_set **0x02**;
the **file-transfer family rides cmd_set 0x00 (COMMON)**.

---

## 1. ISO / EXPOSURE

### 1a. SetIso — `DataCameraSetIso`
- **cmd_set 0x02, cmd_id 0x2A, receiver 0x01 (CAMERA)**, sender APP, REQUEST, NEEDACK=YES, no encrypt.
- `doPack()`: payload = **1 byte** = `(type<<7) | value`.
  - `type` = 0 → absolute (default; `setType(true)` stores `!true`=0). Byte = the ISO enum value.
  - `type` = 1 → relative → byte = `0x80 | relValue`.
- ISO enum wire values (`DataCameraGetIso$TYPE.value`):
  `AUTO=0, AUTOHIGH=1, ISO50=2, ISO100=3, ISO200=4, ISO400=5, ISO800=6, ISO1600=7,`
  `ISO3200=8, ISO6400=9, ISO12800=10, ISO25600=11, ISO51200=12, ISO102400=13, LOCK=255.`
- **Our `_ISO_INDEX` (100→3,200→4,400→5,800→6,1600→7,3200→8) is CORRECT**, and the 1-byte
  absolute payload with the high bit clear is the right shape. So ISO itself is fine.

### 1b. SetExposureMode — `DataCameraSetExposureMode`  (the precondition, and our bug)
- **cmd_set 0x02, cmd_id 0x1E, receiver 0x01**, REQUEST, NEEDACK=YES.
- `doPack()`: payload = **2 bytes** (`iconst_2 / newarray byte`):
  - `byte[0] = expMode`
  - `byte[1] = senceMode`, **only written when `expMode == 6` (SCN)**; otherwise stays 0.
- ExposureMode wire values (`DataCameraSetExposureMode$ExposureMode`):
  `AUTO=0, P=1, S=2, A=3, M=4, B=5, SCN=6, C=7`.  **Manual = M = 4.**
- Precondition (confirmed on DJI docs): for every camera except X5/X5R, **ISO only takes in
  MANUAL exposure mode**, and the app sends `setExposureMode(MANUAL)` and *waits for its ack*
  before `setISO`.

**WHY OUR ISO IS A NO-OP:** `drone.set_exposure_mode()` sends a **1-byte** payload
(`bytes([mode])`) but the model packs **2 bytes** `[expMode, 0x00]`. A short exposure-mode
frame is dropped/ignored → camera stays AUTO → the subsequent SetIso is rejected because it
isn't in Manual. Also `set_iso()` fires SetExposureMode then SetIso back-to-back with no wait
for the exposure-mode ack (the app sequences them via callback).

**FIX:** `set_exposure_mode` → send `bytes([mode & 0xFF, 0x00])` (2 bytes). In `set_iso`, send
exposure-mode=4, wait for the 0x02/0x1E ack (or ~150 ms), *then* send SetIso. Verify via the
0x02/0x82 / camera-settings pushes that exposure mode reads back as 4 before ISO.

---

## 2. RECORDING — `DataCameraSetRecord`
- **cmd_set 0x02, cmd_id 0x02, receiver 0x01**, REQUEST, NEEDACK=YES.
- `doPack()`: payload = **1 byte** = TYPE value.
  `DataCameraSetRecord$TYPE`: **STOP=0, START=1, PAUSE=2, RESUME=3, OTHER=7**.
- **Our `start_record`=`b"\x01"`, `stop_record`=`b"\x00"` are CORRECT** (cmd_id 0x02, payload 0x01/0x00).
- Note: `DataCameraSetRecord.start(long)` schedules a repeating Timer that re-sends the frame
  every `period` ms until `stop()` — the app spams it, doesn't fire once. Not required, but if a
  single START is being dropped, resend a few times.
- **Precondition:** camera must be in **RECORD work mode** first — SetMode 0x02/0x10 payload
  `[0x01]` (`DataCameraGetMode$MODE.RECORD=1`). `start_record` currently does NOT set the mode.
  Sequence: `set_camera_mode(1)` → (confirm mode) → `start_record()`.

**Verify push — `DataCameraGetPushStateInfo` (cmd_set 0x02, cmd_id 0x80):**
- `getRecordState()` → `RecordType {NO=0, START=1, STARTING=2, STOP=3}` — non-STOP == recording.
- `getVideoRecordTime()` → payload **offset 29, u16** = elapsed record seconds (watch it climb).
- `getMode()` → current work mode (confirms RECORD vs PLAYBACK transitions).

---

## 3. MEDIA LIST + DOWNLOAD — the long-standing 0xE0 blocker

### 3a. Which API WM160 uses
`dji.sdk.camera.Camera` exposes both `getPlaybackManager()`/`isPlaybackSupported()` and
`getMediaManager()`/`isMediaDownloadModeSupported()`. The **legacy** WM160 camera uses
**PlaybackManager**, whose backend is `dji/internal/camera/hgf`. That handler drives the DUML
file family below. (The newer `MediaManager`/`FetchMediaTask`/KeyValue path — which our current
`media.py` was modelled on — is the `isMediaDownloadModeSupported` path and is NOT how Mini 1 works.)

### 3b. THE MODE BUG (root cause of 0xE0)
`hgf` enters media by:
`DataCameraSetMode.setMode(DataCameraGetMode$MODE.PLAYBACK).start()`  →  **0x02/0x10 payload `[0x02]`.**

`DataCameraGetMode$MODE` wire values:
`TAKEPHOTO=0, RECORD=1, PLAYBACK=2, TRANSCODE=3, TUNING=4, SAVEPOWER=5, DOWNLOAD=6, NEW_PLAYBACK=7, BROADCAST=8, OTHER=100.`

- **Our `enter_playback()` sends `[0x03]` = TRANSCODE.** That is not a media state, so the whole
  file family is refused → **0xE0 "not available in this state"**. This is the bug.
- The prior doc claim "MEDIA_DOWNLOAD=3, PLAYBACK=2" is wrong on both counts:
  - DUML enum: TRANSCODE=3, DOWNLOAD=6, NEW_PLAYBACK=7.
  - SDK `SettingsDefinitions$CameraMode`: PLAYBACK=**2**, MEDIA_DOWNLOAD=**4**. And the abstraction
    `.../camera/nhf.gfd()` **remaps MEDIA_DOWNLOAD → wire 7** (then `MODE.find(7)`=NEW_PLAYBACK)
    before sending. So MediaManager cameras get wire **7**, never 3.
- **For WM160 send `[0x02]` (PLAYBACK).** If a given firmware wants the MediaManager path instead,
  the only other legal value is `[0x07]` (NEW_PLAYBACK). **3 is never correct.**

### 3c. Full ordered sequence (PlaybackManager / `hgf`)
1. **Enter playback:** `0x02/0x10` → CAMERA(0x01), payload `[0x02]`.
2. **Wait for state:** `DataCameraGetPushStateInfo` (0x02/0x80) `getMode()==PLAYBACK`, and the
   playback push (next line) before issuing any file op. Do **not** send the list request first.
3. **List / navigation state = `DataCameraGetPushPlayBackParams`** (push, cmd_set 0x02 cmd_id 0x82).
   This is the legacy "list": it reports counts + the currently-selected file. Offsets (from
   `DataBase.get(off,len,Integer)` in `unPack`):
   - off 0  : mode byte (`$MODE {Single=0,SingleLarge=1,SinglePlay=2,SinglePause=3,MultipleDel=4,Multiple=5,Download=6,SingleOver=7,SingleLoading=8,...}`)
   - off 3  u8  : `fileNum` (files on this page)
   - off 4  u16 : `totalNum`
   - off 6  u16 : `index` (current selection)
   - off 17 u16 : `totalPhotoNum`
   - off 19 u16 : `totalVideoNum`
   - plus `getFileName()` (string) and `getFileType()` (`$FileType {JPEG=0,DNG=1,VIDEO=2}`).
4. **Download the selected file:** `DataCameraRequestSendFiles` → **cmd_set 0x00 (COMMON), cmd_id
   0x22, receiver CAMERA(0x01)**, REQUEST/NEEDACK=YES. payload = **1 byte** `FILE_SELECT_MODE`
   `{CURRENT=0, NEXT=1}`. Send `[0x00]` for the current file; `[0x01]` to advance selection.
5. **Camera streams back:**
   - metadata via `DataCameraAckReceiveFiles` fields: `getFileName()`, `getFileSize()` (i64),
     `getFileType()`, `getCreateTime()` (i64), `getMD5()`.
   - the file-list blob via `DataCameraGetPushFiles` push (**0x00/0x24**): `getIndex()`, `getData()[]`.
   - the file bytes via `DataCameraGetPushFile` push (**0x00/0x27**), reassembled by
     `DJIVideoPackManager` (chunked transfer channel).
6. **App must ACK each unit:** `DataCameraAckReceiveFiles` → **0x00/0x23**, `AckCcode`
   `{Success=0, UnableReceive=34, NoMemory=35, NoSupport=…}`. Send Success=0 to keep the stream
   flowing; UnableReceive to abort. (`hgf` sends UnableReceive when it tears down.)
7. **Retransmit gaps:** `DataCameraSetResendFiles` → **0x00/0x25**, `setIndex(i)`.
8. **Delete:** `DataCameraDeleteFile` → **cmd_set 0x00, cmd_id 0x28**, receiver CAMERA(0x01).
9. **Exit:** SetMode 0x02/0x10 `[0x01]` (RECORD) or `[0x00]` (TAKEPHOTO) to return to liveview.

### 3d. What's wrong in current `media.py`
- `CID_FILE_LIST = 0x20` and `CID_FILE_DATA = 0x1F` **do not exist** in the camera file family.
  The real transport is 0x00/0x22 (request) + pushes 0x00/0x24 (list) and 0x00/0x27 (data) +
  0x00/0x23 (ack). `CID_FILE_DELETE=0x28` is the only right cmd_id.
- The big `file_list_request` / `file_data_request` **ByteStreamHelper/KeyValue payloads are the
  MediaManager (newer camera) format** — not WM160. Legacy transfer payloads are the 1-byte
  CURRENT/NEXT selector, not a serialized task envelope.
- `enter_playback()` → `[3]`: wrong mode (see 3b). Use `[2]`.
- So all five "list variants" fail identically with 0xE0 because the camera was never put into a
  media state and the cmd_ids address nothing.

---

## 4. Concrete changes to implement (no code changed here)

1. **Exposure mode 2-byte fix:** `set_exposure_mode(m)` → `_cmd(0x02,0x1E,bytes([m&0xFF,0x00]),CAMERA)`.
2. **ISO sequencing:** in `set_iso`, send exposure-mode=4, wait for its ack (or ~150 ms), then
   `_cmd(0x02,0x2A,bytes([idx]),CAMERA)`. Index table already correct.
3. **Record precondition:** `start_record` → `set_camera_mode(1)` (RECORD) first, confirm via
   0x02/0x80 `getMode`, then `_cmd(0x02,0x02,b"\x01",CAMERA)`.
4. **Media:** rewrite `enter_playback` to `_cmd(0x02,0x10,b"\x02",CAMERA)`; replace the whole
   list/data model with: wait 0x02/0x82 push → `RequestSendFiles` `_cmd(0x00,0x22,b"\x00",CAMERA)`
   → collect 0x00/0x27 data + 0x00/0x24 list pushes + 0x00/0x23-Success acks → advance with
   `_cmd(0x00,0x22,b"\x01",CAMERA)`; delete via `_cmd(0x00,0x28,…,CAMERA)`.

## 5. How to verify each on hardware (pc_client)
- **ISO/exposure:** set exposure-mode=4, poll the camera state/settings push; confirm exposure
  mode reads back Manual, then set ISO and confirm the ISO push reflects the value (not AUTO).
- **Record:** `set_camera_mode(1)` → start_record → watch 0x02/0x80: `getRecordState`≠STOP and
  `getVideoRecordTime` (off 29) incrementing; stop_record → returns to STOP.
- **Media:** send 0x02/0x10 `[2]`; you should stop getting 0xE0 and start receiving a
  `DataCameraGetPushPlayBackParams` (0x02/0x82) push with non-zero totalNum. Then 0x00/0x22 `[0]`
  should produce 0x00/0x24 / 0x00/0x27 pushes instead of a 1-byte 0xE0. Dump raw pushes to pin the
  exact `GetPushFiles.getData()` record stride from a real capture.

## 6. Citations
- `dji/midware/data/config/P3/CmdSet.class` (CAMERA=2, COMMON=0)
- `dji/midware/data/config/P3/DeviceType.class` (CAMERA=1, APP=2)
- `dji/midware/data/config/P3/CmdIdCamera$CmdIdType.class` (SetIso=42, SetRecord=2, SetMode=16, SetExposureMode=30, GetPushStateInfo=128, GetPushPlayBackParams=130)
- `dji/midware/data/config/P3/CmdIdCommon$CmdIdType.class` (RequestSendFiles=34, AckReceiveFiles=35, GetPushFiles=36, SetResendFiles=37, GetPushFile=39, DeleteFile=40)
- `dji/midware/data/model/P3/DataCameraSetIso.class` + `DataCameraGetIso$TYPE.class`
- `dji/midware/data/model/P3/DataCameraSetExposureMode.class` (2-byte doPack) + `$ExposureMode`
- `dji/midware/data/model/P3/DataCameraSetRecord.class` + `$TYPE`
- `dji/midware/data/model/P3/DataCameraSetMode.class` + `DataCameraGetMode$MODE.class`
- `dji/internal/camera/hgf.class` (PlaybackManager backend: SetMode PLAYBACK, RequestSendFiles CURRENT/NEXT, AckReceiveFiles, GetPushFiles)
- `dji/sdksharedlib/hardware/abstractions/camera/nhf.class` (`gfd()`: MEDIA_DOWNLOAD→wire 7)
- `dji/common/camera/SettingsDefinitions$CameraMode.class` (PLAYBACK=2, MEDIA_DOWNLOAD=4)
- `dji/midware/data/model/P3/DataCameraGetPushPlayBackParams.class` (offsets) + `$MODE` + `$FileType`
- `dji/midware/data/model/P3/DataCameraGetPushStateInfo.class` (getVideoRecordTime off 29) + `$RecordType`
- `dji/midware/data/model/P3/DataCameraRequestSendFiles.class` + `$FILE_SELECT_MODE`, `DataCameraAckReceiveFiles.class` + `$AckCcode`, `DataCameraGetPushFiles.class`
- `dji/sdk/camera/Camera.class` (getPlaybackManager/isPlaybackSupported vs getMediaManager/isMediaDownloadModeSupported)
- DJI MSDK docs: setISO requires MANUAL exposure mode — https://developer.dji.com/api-reference/android-api/Components/Camera/DJICamera.html ; ISO sample (setExposureMode then setISO in callback) — https://github.com/dji-sdk/Mobile-SDK-Android/blob/master/Sample%20Code/app/src/main/java/com/dji/sdk/sample/demo/camera/SetGetISOView.java ; MediaManager overview — https://developer.dji.com/api-reference/android-api/Components/Camera/DJIMediaManager.html
