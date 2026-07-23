# DOMAIN: media_album — gallery, thumbnails, in-drone playback engine, in-app editor (WM160 / Mavic Mini 1)

> **⚠ WM160 CORRECTION (2026-07-23, confirmed ≥2 sources: MSDK v4 jar + app smali + dft lua + hardware):**
> `MEDIA_TRANSFER.md` and this doc's §1–2 table reference `0x00/0x20` (File List) and `0x00/0x1F` (File Data)
> as the primary path. **These cmd_ids are NOT implemented on WM160 — hardware returns `0xE0 = INVALID_CMD`.**
> The correct WM160 media sequence is:
> `0x02/0x10 [0x02]` → wait `0x02/0x80` push byte[4]==2 → `0x00/0x22 [0x00]` → receive `0x00/0x24` push →
> `0x00/0x26` (16B) → receive `0x00/0x27` chunks → ACK each with `0x00/0x23 [0x00]` → `0x00/0x28` delete.
> See `media.py` for the authoritative implementation and `MEDIA_0XE0_RESEARCH_2026.md` for the root-cause.

This document **extends** `MEDIA_TRANSFER.md`. It does **not** re-derive the wire protocol for
LIST / DOWNLOAD / THUMBNAIL / DELETE / STORAGE — that is fully nailed there (the `ByteStream` serializer,
`FileListRequest`/`FileDataRequest`/`FileActionRequest` byte layouts, the `0x00/0x1F`+`0x00/0x20`+`0x00/0x28`
DUML commands, `MediaFile`/`PhotoAndVideoNailInfo` records, and the reassembly contract). Read that first.
> Note: `0x00/0x1F`/`0x00/0x20` above apply to other DJI cameras; WM160 uses `0x22`/`0x24`/`0x26`/`0x27` — see correction above.

What this doc adds, for the `uav.media.album` / `com.dji.playback` / `com.uav.playback` domain:

1. The **Java task-orchestration layer** that sits *above* `JNIMediaTaskManager` (which `MEDIA_TRANSFER.md`
   treated only at the JNI boundary): `UAVMediaManager`, `MediaTaskManager`, `MediaBaseTask` and its three
   task subclasses, plus **folder listing** (`MEDIA_FOLDER`), which `MEDIA_TRANSFER.md` did not cover.
2. The **in-drone playback / streaming engine** (`uav.media.player.*`): the JNI API, the DUML commands it
   drives (`0x02/0x7A`, `0x02/0x7B`), and the **decoded video/audio/state frame callbacks** + the exact
   `VideoPlayInfo` / `SeekVideoMsg` / `RotateViewMsg` / `ScaleFOVMsg` / `MediaPlaybackState` layouts.
3. The **gallery UI + in-app editor / template features** (`com.uav.playback.*`): MasterShot/"Creations"
   templates, QuickShot templates, beauty, audio-record — what they are, where they live, and why almost
   all of them are **NOT-WM160** (cloud- and camera-capability-gated, off the drone wire entirely).
4. **`com.dji.playback`** = FlyShare (Quick-Transfer WiFi share server) + LTM (tone mapping). NOT drone DUML.
5. **Receiver addressing for WM160** — the concrete device-id / camera-model / component evidence found in
   `PlaybackCameraInfo` (WM160 = device id **59 / 0x3B**, camera EXIF model **`FC7203`**,
   `ComponentIndexType.LEFT_OR_MAIN = 0`), and why the DUML receiver byte is still native.

> Sources (baksmali'd for this analysis; all under `/mnt/c/users/nikolay/Downloads/reversing/`):
> - `…/reverse_docs/unpacked_app_dex/classes_03a5700c.dex` — **DEX-03a5**, the `uav.media.*` task/JNI/player layer.
> - `…/reverse_docs/unpacked_app_dex/classes_0451d00c.dex` — **DEX-0451**, the CSDK KeyValue value objects (`uav.sdk.keyvalue.value.*`).
> - `…/reverse_docs/unpacked_app_dex/classes_00b9d00c.dex` — **DEX-00b9**, the `com.uav.playback.*` gallery/editor/preview UI (5147 classes, one dex).
> - `…/reverse_docs/unpacked_app_dex/classes_04e4400c.dex` — **DEX-04e4**, `com.dji.playback.*` (FlyShare / LTM / camera-info registry).
> - `…/reverse_docs/full_table.txt`, `cmdmap.txt` — native DUML cmd_set/cmd_id ↔ message-name registry.
> - `…/reverse_docs/MEDIA_TRANSFER.md` — the wire-protocol reference this doc extends.

---

## 0. Layer map — where "album" lives relative to the wire

```
┌─ com.uav.playback.*  (DEX-00b9)  ── the GALLERY + EDITOR UI (Kotlin)
│   ui/ preview/ main/ widget/ databinding/   → grid, single-view, video preview screens
│   editor/ beauty/ audio/                     → in-app auto-editor (MasterShot/QuickShot templates, beauty, voiceover)
│   task/ provider/ impl/ interfaces/ internal/→ UI-side task glue, DI, camera-info
│   flyshare/                                  → UI for Quick-Transfer
│           │  (talks down through provider interfaces to…)
├─ com.dji.playback.*  (DEX-04e4)  ── platform providers
│   flyshare/FlyShareServiceProvider           → WiFi share server (Quick Transfer)  [NOT drone DUML]
│   ltm/DJILtmProvider                          → local tone mapping of previews      [NOT drone DUML]
│   internal/camera/FlyPlaybackCameraInfoKt     → model→camera-info registry (device ids)
│           │
├─ uav.media.*  (DEX-03a5)  ── the SDK MEDIA API (Java) — THIS is "media_album"
│   UAVMediaManager                            → friendly facade: list / download / delete / star / state
│   album/MediaTaskManager + MediaBaseTask     → task queue orchestration
│   album/MediaFileListTask / MediaFolderListTask / MediaFileDataTask
│   album/jni/JNIMediaTaskManager              → JNI boundary  (see MEDIA_TRANSFER §0/§2.5)
│   player/MediaPlayer + player/jni/JNIMediaPlayer → IN-DRONE video playback/streaming engine
│   FileOperateHelper                          → sets slot=EXTERN1, builds requests
│           │  (JNI [B blob == ByteStream.toBytes, wrapped as DUML in libsdk_jni)
├─ uav.sdk.keyvalue.value.*  (DEX-0451)  ── the value objects / serializer (MEDIA_TRANSFER §1/§8)
│           │
└─ libsdk_jni (native)  ── binds value-object → cmd_set/cmd_id, stamps DUML header, AOA/composite → drone
```

**Takeaway:** everything that actually crosses to the drone is the DUML protocol already documented in
`MEDIA_TRANSFER.md`. `uav.media.album` is the Java queue on top of it; `com.uav.playback` is the UI/editor on
top of *that*; `com.dji.playback` is peripheral (WiFi transfer / tone mapping). For our PC controller we
reproduce the `MEDIA_TRANSFER.md` DUML payloads directly and can ignore the Java/Kotlin layers — except the
playback **engine** in §3, which is the one new piece of drone-facing protocol here.

---

## 1. `UAVMediaManager` — the high-level facade (DEX-03a5, `uav/media/UAVMediaManager.smali`)

This is the friendly API the app calls; each method assembles a value object and hands it to the album task
layer (§2) or `FileOperateHelper`. Public surface (method letters are the obfuscated names; signatures prove intent):

| method | signature | does |
|---|---|---|
| `D(II)` | `(deviceType, componentIndex) → UAVMediaManager` | **get/create instance for a camera** (addressing, see §5) |
| `s(Z, MediaFileListRequest, ICallback)` | list | **list media** (bool = refresh/force); builds `FileListRequest` (MEDIA_TRANSFER §2.2) |
| `i()` | `→ List` | return the **cached** media list |
| `j()` | `→ MediaFileListState` | list-load state (idle/loading/done/error) |
| `g(MediaFileDownloadRequest, ICallback)` | download | **download** (whole-file callback) |
| `h(MediaFileDownloadRequest, IGetFileCallBack)` | download | **download** (streamed `(offset,data)` callback) |
| `E(List, MediaFileStarTag, ICallback)` | tag | **star/tag** files (→ `FileActionRequest` TAG_STAR/CANCEL_STAR, MEDIA_TRANSFER §5) |
| `f(List, ICallback)` | delete | **delete these files** (→ `FileActionRequest` DELETE_SINGLE) |
| `c / e / v / w / z(ICallback)` | control | subscribe/refresh/cancel/format helpers |
| `l(CallBack)` / `x()` | lifecycle | attach/detach |

`MediaFileDownloadRequest` (`…/value/media/MediaFileDownloadRequest.smali`) is the media-layer shape that
`FileOperateHelper` maps field-for-field into a `FileDataRequest` (proven in `FileOperateHelper.smali`:
`getIndex→setIndex`, `getSubIndex→setSubIndex`, `getSegSubIndex→setSegSubIndex`, and it force-sets
`slotLocation = UAV_CAMERA_STORAGE_ID_EXTERN1`). So `UAVMediaManager.h(...)` ≡ the `0x00/0x1F` download in
MEDIA_TRANSFER §2.4. Nothing new on the wire; this is just the ergonomic entry point.

---

## 2. The album task queue (DEX-03a5, `uav/media/album/*`)

`MEDIA_TRANSFER.md` documented the JNI boundary (`JNIMediaTaskManager`, `MediaFileDataTask$IFileDataTaskHolder`
callbacks). Here is the queue that drives it.

### 2.1 `MediaTaskManager` (`uav/media/album/MediaTaskManager.smali`)
- `getInstance(int deviceType, int componentIndex, ComponentIndexType)` — one manager per camera (addressing §5).
- `o(MediaBaseTask, ITaskActionCallback)` / `J(...)` — **enqueue** a task.
- `G(MediaBaseTask)` / `H(MediaBaseTask)` — start / remove.
- `I(boolean)` — pause/resume queue; `q(int)` / `p(cb)` — cancel; `r()` — is-busy.
- Static `a/b/h/l/m/n(MediaBaseTask, int retCode, FileTaskResponse, byte[])` — the JNI up-calls that route a
  `FileTaskCallback.invoke(retCode, FileTaskResponse, byte[])` back to the owning task. `FileTaskResponse` is
  the flow-control object (`listLeft`/`dataLeft`/`receivedDataSize`/`totalDataSize`/`bitSpeed`) — MEDIA_TRANSFER §2.5.

### 2.2 `MediaBaseTask` and its three concrete tasks
`MediaBaseTask` (`…/MediaBaseTask.smali`) holds one `FileTaskRequest` (MEDIA_TRANSFER §2.1) and an
`ITaskResponseHolder`. Three subclasses, one per `FileTaskType`:

| task | `FileTaskType` | request | response holder callback | DUML |
|---|---|---|---|---|
| `MediaFileListTask` | `FILE_LIST` (1) | `FileListRequest` type=`MEDIA`(0) | `IFileListTaskHolder` → `MediaFile[]` | `0x00/0x20` |
| **`MediaFolderListTask`** | `FILE_LIST` (1) | `FileListRequest` type=**`MEDIA_FOLDER`(4)** | `IFolderListTaskHolder.a(int, List<MediaFolder>)` | `0x00/0x20` |
| `MediaFileDataTask` | `FILE_DATA` (0) | `FileDataRequest` | `IFileDataTaskHolder.c(req,data,offset)` (MEDIA_TRANSFER §2.5) | `0x00/0x1F` |

**New vs MEDIA_TRANSFER — folder listing.** `MediaFolderListTask.m(List<FileListRequest>)` asserts each
request's `getType() == FileType.MEDIA_FOLDER` and builds a `FileTaskRequest` around it; its
`IFolderListTaskHolder.a(int, List<MediaFolder>)` returns folders instead of files. So the **same** `0x00/0x20
get_file_list` command, with `FileListRequest.type = MEDIA_FOLDER (4)` instead of `MEDIA (0)`, returns the DCF
directory list. Each entry is a **`MediaFolder`** (`…/value/media/MediaFolder.smali`, `toBytes` order):

| off | field | type | meaning |
|----:|---|---|---|
| +0 | `index` | int32 LE | folder index (feed back as `FileListRequest.folderIndex` to list its files) |
| +4 | `date` | DateTime (24 B) | folder timestamp |
| +28 | `name` | string (i32 len + UTF-8) | e.g. `100MEDIA` |
| … | `isPageLastFolder` | u8 | paging terminator (mirrors `MediaFile.isPageLastFile`) |

Flow: list folders (`type=MEDIA_FOLDER`) → for each folder, list files with `folderIndex = MediaFolder.index`,
`type=MEDIA`. On the Mini this is optional; a flat listing (`folderIndex=0`, `isAllList=true`) works because
the Mini keeps everything under one DCIM tree, but folder mode is what the app's "by folder" view uses.

---

## 3. The IN-DRONE PLAYBACK / STREAMING ENGINE (DEX-03a5, `uav/media/player/*`) — NEW

`MEDIA_TRANSFER.md §4` flagged `0x02/0x0C`, `0x02/0x7A`, `0x02/0x7B` as "playback mode, exact payloads native,
optional." Here is the full Java engine that drives them: it asks the **drone to decode a stored video and
stream frames back**, rather than downloading the file. This is what powers the in-app video-preview scrubber.

### 3.1 `MediaPlayer` / `JNIMediaPlayer` API (`uav/media/player/MediaPlayer.smali`, `…/jni/JNIMediaPlayer.smali`)
`MediaPlayer.getInstance(deviceType, componentIndex, ComponentIndexType)` — one engine per camera (§5). Each
public method funnels to a `native_*` in `JNIMediaPlayer` (all take `(int,int,int, …, RetCodeCallback)` where
the three ints are the device/component/handle triple that native turns into the DUML receiver):

| `MediaPlayer` | `JNIMediaPlayer` native | value object sent | on-wire DUML (native binding) |
|---|---|---|---|
| `e(VideoPlayInfo, cb)` | `native_PreparePlayData(III,[B,cb)` | **`VideoPlayInfo`** | **`0x02/0x7B` single_playback_select** (selects file + prepares stream) |
| `d(cb)` | `native_PlayVideo` | — | **`0x02/0x7A` video_playback_control** (PLAY) |
| `c(cb)` | `native_PauseVideo` | — | `0x02/0x7A` (PAUSE) |
| `f(cb)` | `native_ResumeVideo` | — | `0x02/0x7A` (RESUME) |
| `k(cb)` | `native_StopVideo` | — | `0x02/0x7A` (STOP) |
| `g(SeekVideoMsg, cb)` | `native_SeekVideo(III,[B,cb)` | **`SeekVideoMsg`** | `0x02/0x7A` (SEEK) |
| — | `native_RotateView(III,[B,cb)` | **`RotateViewMsg`** | pano/360 view control |
| — | `native_ScaleFOV(III,[B,cb)` | **`ScaleFOVMsg`** | pano/360 zoom |
| — | `native_ResetFOV` / `native_ResetView` | — | pano/360 reset |
| `i(cb)` / `a()` | `native_SetStateObserver` / `native_CancelStateObserver(JII)` | — | subscribe playback state |
| `j(cb)` / `h(cb)` / `b()` | `native_SetVideoObserver` / `native_SetAudioObserver` / `native_CancelVideoAudioObserver` | — | subscribe frame streams |

All five transport calls (play/pause/resume/stop/seek) map to the single **`0x02/0x7A
video_playback_control`** (full_table.txt:129); the action is a control-type discriminator inside that
command's native payload. `preparePlayData` maps to **`0x02/0x7B single_playback_select`** (full_table.txt:130)
and carries the `VideoPlayInfo` selecting the file. (`0x02/0x0C switch_playbackmode`, MEDIA_TRANSFER §4.1, is
still the mode gate you enter first.)

### 3.2 Playback value objects (DEX-0451, exact `toBytes` order — enums = int32 LE `value()`, primitives LE)
- **`VideoPlayInfo`** (`…/value/media/VideoPlayInfo.smali`): `index` i32 + `duration` i32 + `frameRate`
  (`VideoFrameRate` enum, i32) = **12 B**. `index` = the `MediaFile.fileIndex` to play.
- **`SeekVideoMsg`** (`…/value/camera/SeekVideoMsg.smali`): `position` **f64 (8 B)** + `autoStart` **u8** = **9 B**.
  `position` is the seek target (seconds/normalized); `autoStart` resumes playback after the seek.
- **`RotateViewMsg`** (`…/value/camera/RotateViewMsg.smali`): `pitchAngularVelocity` i32 + `rollAngularVelocity`
  i32 + `yawAngularVelocity` i32 = **12 B** (pano/360 pan; irrelevant to WM160, no pano video).
- **`ScaleFOVMsg`** (`…/value/camera/ScaleFOVMsg.smali`): `scalingFactor` **f64 (8 B)** = **8 B** (pano zoom).

### 3.3 Frame / state callbacks (the stream coming back)
Native pushes reassembled frames up through these interfaces (DEX-03a5):

| callback | signature | meaning |
|---|---|---|
| `player/jni/PlaybackVideoCallback` | `invoke(J handle, C, Z isKeyFrame, D pts, [B frame)` | one **video frame** (`[B`) + timestamp `pts` |
| `jni/callback/JNIPlaybackVideoCallback` | `onVideoDataComing(I, I, Z, D, [B)` | JNI-level video frame |
| `player/jni/PlaybackAudioCallback` | `invoke(J handle, C, D pts, [B pcm/aac)` | one **audio frame** |
| `jni/callback/JNIPlaybackAudioCallback` | `onAudioDataComing(I, I, D, [B)` | JNI-level audio frame |
| `player/jni/PlaybackStateCallback` | `invoke(J handle, C, MediaPlaybackState)` | playback state update |
| `jni/callback/JNIPlaybackStateCallback` | `onPlaybackStateChanged(I, I, [B)` | state as serialized `MediaPlaybackState` |

**`MediaPlaybackState`** (`…/value/media/MediaPlaybackState.smali`, `toBytes` order, = **28 B**):
`fileIndex` i32 + `status` (`PlaybackStatus` enum, i32) + `playingPosition` f64 + `totalDuration` f64 +
`bufferProgress` i32. `PlaybackStatus`: `PREPARED=0, PLAYING=1, PAUSED=2, ENDED=3, STOPPED=4, BUFFERING=5`
(MEDIA_TRANSFER §8). There is also a camera-side **`PlaybackMode`** enum (`…/value/camera/PlaybackMode.smali`)
used with `0x02/0x0C`: `SINGLE_IMAGE, SINGLE_IMAGE_ZOOM_IN, MULTIPLE_IMAGES, MULTIPLE_IMAGE_DELETE,
SINGLE_VIDEO_PLAY, SINGLE_VIDEO_PAUSE, VIDEO_PLAYBACK_STOP, DOWNLOAD, MODE_ERROR, UNKNOWN`.

**Whether frames are H.264/H.265-encoded NAL units or already decoded** is decided in native (the codec is not
visible at the Java boundary — Java only sees `[B` + `pts` + `isKeyFrame`). `isKeyFrame` strongly implies these
are **encoded** frames handed to the app's decoder. **Needs a live capture (§6, H-P1/H-P3) to confirm codec and
whether the transport is this `0x02/0x7A` path or the liveview channel.**

> **WM160 relevance of §3:** This whole engine is **optional** for our controller — we get photos/videos by the
> §2 file download. It only matters if you want the drone to *decode-and-stream* a stored clip. It is
> **model-generic camera protocol** (cmd_set 0x02), so it should work on WM160, but it is unverified on the Mini
> and the pano-view calls (`RotateView`/`ScaleFOV`) are pano/360 features WM160 does not have.

---

## 4. In-app GALLERY UI and EDITOR / TEMPLATE features (DEX-00b9, `com.uav.playback.*`)

This is the entire Kotlin gallery + auto-editor (5147 classes). **None of it is new drone wire protocol** — it
sits on the providers/`UAVMediaManager` above. Documenting scope + WM160 applicability:

### 4.1 Gallery / preview UI (on-drone protocol = §2/§3, nothing new)
- Entry: `com.uav.mainpageui.mainpage.business.album.AlbumEntryClickUseCase` — opens the album from the main page.
- Grid / single-view / video preview live in `com.uav.playback.ui/`, `preview/`, `main/`, `widget/`,
  `databinding/` (e.g. `NewEditorGalleryGridLayoutBinding`, `NewEditorSelectLayoutBinding`). These drive the
  §2 list+thumbnail and §3 preview flows. **No drone command originates here that isn't already in §2/§3.**

### 4.2 In-app EDITOR — MasterShot / "Creations" templates (`com.uav.playback.editor.*`)  — mostly NOT-WM160
The auto-editor that stitches clips into a themed video:
- `editor/data/MasterShotInfo`, `MasterShotMode {NORMAL, PORTRAIT, BIG_OBJ}`, `MasterShotStrategy`, `Segment`,
  `ClipShotInfo`, `MasterShotTemplate`, `MasterShotTemplateCategory`.
- Templates are **cloud assets**: `editor/data/source/TemplateRepository` = `LocalMasterTemplateDataSource` +
  `RemoteEditorTemplateSource`; `MasterShotTemplate` has `coverPath`/`templatePath`/`strategyPath` and a
  `TemplateState { NeedDownload, Downloading, DownloadError, Exists }` — i.e. downloaded from DJI's SkyPixel/
  service **domain** (configured in `EditorConfig`: `skyPDomain`, `lightSkyPDomain`, `userCenterDomain`,
  `mastershotTemplateCategory`), not from the drone. No hardcoded URL; the host is a runtime-configured domain.
- **Capability gating (the WM160 verdict):** `editor/data/MasterShotMode$Companion` maps a template mode from
  **`com.uav.flymodel.generated.api.camera.MasterShotMode.getValue()`** — a *camera capability key reported by
  the connected aircraft*. WM160/Mavic Mini 1 has **no MasterShot ("Creations") capability** (MasterShots
  debuted on Air 2S; Mini 1 predates it), so the camera never reports a MasterShot mode and this whole editor
  path is **dormant / NOT-WM160**. When an unmapped model value arrives it logs
  `"flyModel to MasterShotMode is error, flyModelValue:"` via `EditorExceptionHandler`.

### 4.3 QuickShot templates, beauty, audio-record — NOT-WM160 (as editor features)
- QuickShot template selectors exist (`databinding/…QuickShotTemplateSelect…`, `PlaybackLayoutQuickshotTemplates…`).
  Note the distinction: **WM160 *can shoot* QuickShots** (Dronie/Circle/Helix/Rocket/Boomerang — those are flight
  modes, out of this domain), and the resulting clip is just a normal MP4 on the SD (`MediaFile.videoType`
  tagged). The **editor's** QuickShot *re-templating* here is a phone-side re-edit feature, capability-gated the
  same way as MasterShot.
- `beauty/param/provider/EditorBeautifyParam {category, type, value:double}` + `EditorBeautifyParamsProvider`
  pull `BeautifyCloudParam` — **cloud-sourced portrait beautify** applied during editing. Off-drone; not WM160.
- `audio/record/*` — voiceover/music recording added to an edited video on the phone. Off-drone; not WM160.

> **Bottom line for §4:** the editor/template/beauty/audio stack is a **phone-side, cloud-driven post-processing
> studio**. It touches the drone only via the ordinary §2 download. For a WM160 PC controller it is entirely out
> of scope, and its "auto-edit" (MasterShot/Creations) surface is not even reachable on the Mini 1.

---

## 5. RECEIVER ADDRESSING for WM160 (confirming MEDIA_TRANSFER §7)

MEDIA_TRANSFER §7 said the DUML receiver byte is stamped by native and "must be read off the wire." This domain
adds concrete evidence of how the app *identifies* the WM160 camera, and confirms the manager/engine addressing
tuple:

### 5.1 The camera-info registry (`com.dji.playback.internal.camera.FlyPlaybackCameraInfoKt`, DEX-04e4)
Builds `PlaybackCameraInfo(exifTagModel: String, xmpProductName: List, isHasselbladLens: Boolean, deviceId:
Long, pictureFrameSupportStatus: Int)` for every model. The relevant row:

| exifTagModel | deviceId | model |
|---|---|---|
| **`FC7203`** | **`0x3B` = 59** | **WM160 (Mavic Mini 1)** — matches the project's UAV59 = WM160 |
| `FC7203` | `0x60` = 96 | Mini-family sibling (same camera EXIF tag, different device id) |
| `FC7303` | `0x4C` = 76 | Mini 2 |
| `FC7503` | `0x71` | Mini 2 SE-class |
| `FC3170` | `0x43` | (Air 2) etc. |

So the app keys WM160 by **device id 59 (0x3B)** with camera EXIF model **`FC7203`**, `isHasselbladLens=false`.
This `deviceId` is the **model/type identifier** (used for EXIF/XMP tagging of previews), **not itself the DUML
receiver byte** — but it is what selects the per-model behaviour and confirms we are addressing a single-camera
Mini.

### 5.2 Component addressing
`MediaTaskManager`, `MediaPlayer`, and `UAVMediaManager` are all created as
`getInstance(int deviceType, int componentIndex, ComponentIndexType)`. **`ComponentIndexType`**
(`…/value/common/ComponentIndexType.smali`): `LEFT_OR_MAIN=0, RIGHT=1, UP=2, FPV=3, AGGREGATION=4, UNKNOWN=5`.
WM160 has a **single gimbal camera → `LEFT_OR_MAIN` (0)**. The `(deviceType, componentIndex)` pair is what
`libsdk_jni` turns into the DUML **receiver** for these frames.

### 5.3 Net for WM160
- Media file commands (`0x00/0x1F/0x20/0x28`) and camera/playback commands (`0x02/0x0C/79/7A/7B/98`) target
  the **camera** — DUML receiver **`0x01`** (possibly the video board `dm368 0x08` for the `0x00/0x1F` data
  pump). Component = `LEFT_OR_MAIN(0)`. The exact receiver byte is still native — **confirm on the wire
  (MEDIA_TRANSFER §7 H2, and §6 below).** The `deviceId=59`/`FC7203` mapping here is the app-side identity, not
  the receiver byte.

---

## 6. FRIDA HOOKS specific to this domain (in addition to MEDIA_TRANSFER §7 H1–H6)

| # | to settle | hook |
|--:|---|---|
| P1 | in-drone playback command binding + payload for `0x02/0x7A`/`0x02/0x7B` | `uav.media.player.jni.JNIMediaPlayer.native_PlayVideo/native_PauseVideo/native_SeekVideo/native_PreparePlayData` (dump the `int` args + `[B`), compare to raw 0x02 frame |
| P2 | `VideoPlayInfo`/`SeekVideoMsg` blob == on-wire payload | `uav.sdk.keyvalue.value.media.VideoPlayInfo.toBytes([BI)I` / `…camera.SeekVideoMsg.toBytes` return vs P1 frame |
| P3 | **video/audio frame codec + framing** of the streamed playback | `uav.media.player.jni.PlaybackVideoCallback.invoke(JCZD[B)` / `PlaybackAudioCallback.invoke(JCD[B)` — dump `[B`, `pts`, `isKeyFrame`; check for H.264/H.265 NAL start codes |
| P4 | folder-list vs file-list on the wire (same `0x00/0x20`) | `uav.media.album.MediaFolderListTask` request `[B` (via `JNIMediaTaskManager.native_FileTaskPushBack`, MEDIA_TRANSFER H1) — verify only `FileType` differs (MEDIA_FOLDER=4) |
| P5 | confirm `deviceType/componentIndex` → DUML receiver byte | hook `uav.media.album.MediaTaskManager.getInstance(IILComponentIndexType;)` args, correlate with MEDIA_TRANSFER H2 raw frame receiver |

---

## 7. WM160 support matrix (this domain)

| feature | class(es) | WM160? | note |
|---|---|:--:|---|
| Media LIST (photos/videos) | `MediaFileListTask`, `UAVMediaManager.s` | ✅ | `0x00/0x20`, `FileType.MEDIA` (MEDIA_TRANSFER §2) |
| **Folder LIST** | `MediaFolderListTask` | ✅ | `0x00/0x20`, `FileType.MEDIA_FOLDER=4` → `MediaFolder[]` (§2.2). Optional (flat list works) |
| Thumbnails / screennail | `MediaFileDataTask` type=THUMBNAIL/SCREEN | ✅ | `0x00/0x1F` (MEDIA_TRANSFER §3) |
| Download original | `MediaFileDataTask`, `UAVMediaManager.g/h` | ✅ | `0x00/0x1F` (MEDIA_TRANSFER §2.4) |
| Delete / star-tag | `UAVMediaManager.f/E`, `FileActionRequest` | ✅ | `0x00/0x28` (MEDIA_TRANSFER §5) |
| Storage / SD info | `StorageInfoMsg`, `FileSystemInfo` | ✅ | `0x02/0x98` + camera key (MEDIA_TRANSFER §6) |
| Enter playback mode | `0x02/0x0C switch_playbackmode` | ⚠️ | camera-generic; only needed for §3 streaming, verify live |
| **In-drone video streaming** (play/pause/seek back over DUML) | `MediaPlayer`, `JNIMediaPlayer`, `0x02/0x7A/0x7B` | ⚠️ | model-generic camera cmds; **unverified on Mini**; optional for us (§3) |
| Pano/360 view control (RotateView/ScaleFOV/ResetFOV) | `RotateViewMsg`, `ScaleFOVMsg` | ❌ | pano-only; **NOT-WM160** (Mini has no pano video) |
| MasterShot / "Creations" auto-editor + templates | `com.uav.playback.editor.*` | ❌ | cloud+capability gated on `camera.MasterShotMode`; **Mini never reports it — NOT-WM160** |
| QuickShot re-template editor | `editor` + QuickShot template UI | ❌ | phone-side re-edit, capability gated; NOT-WM160 (Mini *shoots* QuickShots — different domain) |
| Portrait beautify | `com.uav.playback.beauty.*` | ❌ | cloud `BeautifyCloudParam`; NOT-WM160 |
| Voiceover / audio record for edits | `com.uav.playback.audio.record.*` | ❌ | phone-side; NOT-WM160 (as an album feature) |
| Quick-Transfer (WiFi share server) | `com.dji.playback.flyshare.FlyShareServiceProvider` (→ `com.share.service.FlyShareManager`) | ⚠️ | **not drone DUML** — separate WiFi/QR channel; Mini 1 has no Quick-Transfer WiFi, so effectively NOT-WM160 |
| LTM (preview tone mapping) | `com.dji.playback.ltm.DJILtmProvider` | n/a | on-phone image processing of previews; not drone protocol |

---

## 8. `com.dji.playback` in one paragraph (so it isn't mistaken for the gallery)

`com.dji.playback` (DEX-04e4) is **not** the media-file gallery — it is three platform providers behind
`com.uav.playback.interfaces`: (1) **`flyshare/FlyShareServiceProvider`** implements
`IFlyShareServiceProvider` over `com.share.service.FlyShareManager` — the **Quick-Transfer WiFi share server**
(`startShareServer`/`stopShareServer`/`syncFileList`/`getWifiQRInfo`, progress via
`PlaybackFlyShareProgressInfo`, status via `ShareStatus`/`ErrorInfo`); a separate WiFi transport, **not DUML/AOA**.
(2) **`ltm/DJILtmProvider`** — local tone mapping applied to previews on the phone. (3)
**`internal/camera/FlyPlaybackCameraInfoKt`** — the model→`PlaybackCameraInfo` registry used in §5. For a WM160
DUML/AOA controller, none of this is on the drone wire; only the §5 device-id table is useful.

---

## 9. Evidence index (class → file → proves)
- `uav/media/UAVMediaManager.smali` (DEX-03a5) — high-level list/download/delete/star facade (§1).
- `uav/media/album/MediaTaskManager.smali`, `MediaBaseTask.smali`, `MediaFileListTask.smali`,
  `MediaFolderListTask.smali`(+`$IFolderListTaskHolder`), `MediaFileDataTask.smali` (DEX-03a5) — task queue & folder listing (§2).
- `uav/sdk/keyvalue/value/media/MediaFolder.smali` (DEX-0451) — folder record layout (§2.2).
- `uav/media/player/MediaPlayer.smali`, `uav/media/player/jni/JNIMediaPlayer.smali`,
  `…/jni/PlaybackVideoCallback.smali`, `PlaybackAudioCallback.smali`, `PlaybackStateCallback.smali`,
  `uav/media/jni/callback/JNIPlayback{Video,Audio,State}Callback.smali` (DEX-03a5) — in-drone playback engine & frame callbacks (§3).
- `uav/sdk/keyvalue/value/media/VideoPlayInfo.smali`, `MediaPlaybackState.smali`,
  `…/value/camera/SeekVideoMsg.smali`, `RotateViewMsg.smali`, `ScaleFOVMsg.smali`, `PlaybackMode.smali` (DEX-0451) — playback value layouts/enums (§3.2/§3.3).
- `com/uav/playback/editor/data/{MasterShotInfo,MasterShotMode,MasterShotTemplate,MasterShotTemplateCategory,Segment,ClipShotInfo}.smali`,
  `editor/data/source/{TemplateRepository,TemplateManager,EditorTemplateDataSource}.smali`,
  `EditorConfig.smali`, `beauty/param/provider/EditorBeautifyParam(sProvider).smali` (DEX-00b9) — in-app editor/templates, capability+cloud gating (§4).
- `com/uav/mainpageui/mainpage/business/album/AlbumEntryClickUseCase.smali` (DEX-00b9) — album entry point (§4.1).
- `com/dji/playback/flyshare/FlyShareServiceProvider.smali`, `ltm/DJILtmProvider.smali`,
  `internal/camera/FlyPlaybackCameraInfoKt.smali`; `com/uav/playback/internal/camera/PlaybackCameraInfo.smali` (DEX-04e4/00b9) — providers + WM160 device-id `FC7203`/59 (§5/§8).
- `uav/sdk/keyvalue/value/common/ComponentIndexType.smali` (DEX-0451) — component addressing enum (§5.2).
- `uav/media/FileOperateHelper.smali` (DEX-03a5) — `MediaFileDownloadRequest → FileDataRequest` mapping, slot=EXTERN1 (§1).
- `reverse_docs/full_table.txt` lines 8/9/11-12/70/129/130/138 — cmd_set/cmd_id ↔ message names.
- `reverse_docs/MEDIA_TRANSFER.md` — the wire protocol this doc extends.
