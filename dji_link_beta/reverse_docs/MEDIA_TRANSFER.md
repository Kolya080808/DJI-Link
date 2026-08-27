# DJI Fly media-file protocol (WM160 / Mavic Mini 1) — LIST / DOWNLOAD / DELETE / BROWSE

> **OBSOLETE AS A WIRE SPEC:** the value-object layouts below are SDK-internal serializers, not proven
> WM160 DUML payloads. Protocol selection, playback entry, list records, transfer framing, and delete body
> remain capture-pending. Use this page for field semantics only; see
> `FIRMWARE_MEDIA_HOME_LIMITS_2026.md`.

Reverse of how **DJI Fly v1.21.4** lists, thumbnails, previews, downloads and deletes photos/videos on
the drone's SD card, for our PC-over-AOA controller. Every byte layout below is taken from the app's own
serializer — no guessing. Where a fact lives only in native code, it is called out explicitly with the
Frida hook that settles it.

> Sources for this document (all under `/mnt/c/users/nikolay/Downloads/reversing/`):
> - `dji_link_beta/reverse_docs/unpacked_app_dex/classes_0451d00c.dex` → **DEX-0451** (CSDK KeyValue value objects + serializer). Baksmali'd for this analysis.
> - `dji_link_beta/reverse_docs/unpacked_app_dex/classes_03a5700c.dex` → **DEX-03a5** (`uav.media.*` task/JNI layer).
> - `dji_link_beta/reverse_docs/full_table.txt`, `cmdmap.txt` (native DUML command registry — the cmd_set/cmd_id ↔ message-name authority).
> - `dji_link_beta/duml.py`, `composite.py` (our on-wire codec, the layer this all rides on).

---

## 0. TL;DR — architecture and the one thing to internalise

The media pipeline is **not** a set of hand-packed DUML payloads in Java. DJI Fly v5 drives media through
its **CSDK "KeyValue" layer**:

```
Java UI ──> uav.media.FileOperateHelper ──> builds a value object
            (FileListRequest / FileDataRequest / FileActionRequest)
        ──> object.toBytes()  [DEX-0451, ByteStream serialization — LAYOUT PROVEN BELOW]
        ──> uav.media.album.jni.JNIMediaTaskManager.native_FileTask*  (JNI, [B = the toBytes blob)
        ──> libsdk_jni native  ── wraps the blob as a DUML frame with the
            cmd_set/cmd_id from the native cmdmap (full_table.txt) ──> AOA/composite ──> drone
```

Evidence the media path is JNI, not Java-packed: `uav/media/album/jni/JNIMediaTaskManager.smali` (DEX-03a5)
has only `native_FileTaskPushBack(JII[B…)`, `native_FileTaskTrySync`, `native_MediaFileBatchAction(JII[B…)`
etc. — every one takes a `[B` that is exactly `FileTaskRequest.toBytes()` / `FileActionRequest.toBytes()`.

**What is statically proven (this doc):** the exact byte layout of every request/response *body* (the `[B`
that crosses the JNI boundary), and the semantic meaning + enum value of every field.

**What is native and must be confirmed live (Frida):**
1. The mapping *value-object → cmd_set/cmd_id* (we take it from `full_table.txt`/`cmdmap.txt`, which were
   themselves dumped from the native registry — strong, but the final binding is in `libsdk_jni`).
2. The DUML header the native puts on: **sender / receiver byte / seq / ack-flag** (§6).
3. The on-wire **fragment framing** of the 0x00/0x1F data-response chunks (§2.4). The Java side only ever
   sees reassembled `(offset, byte[])` callbacks — proven — never the raw chunk header.

Named hooks for all three are in §7.

**All media traffic is DUML** → it rides the composite/AOA channel exactly like everything else
(`composite.py` type `0x5749`). There is no separate media transport. Good news for us: our `duml.py` +
`composite.py` already carry it; we only need to reproduce the payload bodies below.

---

## 1. The serializer — `ByteStreamHelper` (READ THIS FIRST, it defines every layout)

Source: `uav/sdk/keyvalue/value/ByteStreamHelper.smali` (DEX-0451). Every value object implements
`uav/sdk/keyvalue/value/ByteStream` = `{ int toBytes(byte[],int); int fromBytes(byte[],int); int serializedLength(); }`.
Serialization is **flat, positional, no tags, no field IDs** — fields are written back-to-back in a fixed
order. Primitive sizes (static fields `a=4,b=4,c=4,d=4,e=8,f=8` in `<clinit>`), **all little-endian**:

| helper (write / read) | wire format | Python (`struct`) |
|---|---|---|
| `H` / `F` — Integer | int32 LE, 4 B | `<i` |
| `N` / `L` — Long | int64 LE, 8 B | `<q` |
| `y` / `w` — Double | float64 LE, 8 B | `<d` |
| `i` / `g` — Boolean | 1 B (0/1) | `<B` |
| `s` / `q` — Byte | 1 B | `<b` |
| `T` / `R` — String | int32 LE length `n` + `n` bytes UTF-8 (**no NUL**) | `<i` + bytes |
| `l` / `j` — byte[] | int32 LE length `n` + `n` raw bytes | `<i` + bytes |
| `c`,`E`,`o`,`v`,`f` / `a` — List<ByteStream> | int32 LE count + each element's `toBytes` **inlined** (no per-element length) | `<i` + concat |
| `K` / `C` — List<Integer> | int32 LE count + count×int32 | |
| `U` / `z` — nested ByteStream | the nested object's bytes **inlined, no length prefix** | concat |
| `B` (raw) | writes an int32 LE at a given offset | `<i` |

An enum field is written as its `value()` int → **int32 LE** (via `H`). All enum `value()`s are listed in §8.
A `DateTime` (timestamps) is a nested object = **6 × int32 LE = 24 B**: `year, month, day, hour, minute, second`
(`uav/sdk/keyvalue/value/camera/DateTime.smali`).

With this table you can encode/decode any structure below by reading its field list top-to-bottom.

---

## 2. DOWNLOAD path (also covers LIST, because both are `FileTask`s)

The list and the download are two `FileTaskType`s of the **same** queue
(`uav/media/album/jni/JNIMediaTaskManager.fileTaskPushBack(handle, a, b, FileTaskRequest, FileTaskCallback)`,
DEX-03a5). One `FileTaskRequest` carries a `listReq`, a `dataReq`, or a `streamReq`.

### 2.1 `FileTaskRequest` — the envelope  (`…/value/file/FileTaskRequest.smali`)
`toBytes` order:
| off | field | type | notes |
|----:|---|---|---|
| +0 | `type` | int32 LE | `FileTaskType`: **0=FILE_DATA, 1=FILE_LIST**, 2=FILE_STREAM |
| +4 | `duplicateType` | int32 LE | `FileTaskDuplicate` (dedupe policy, 0 is fine) |
| +8 | `deferType` | int32 LE | `FileTaskDefer` |
| +12 | `priority` | int32 LE | `FileTaskPriority` |
| +16 | `runInstantly` | u8 | bool |
| +17 | `dataReq` | List<`FileDataRequest`> | int32 count + inlined; used when type=FILE_DATA |
| … | `listReq` | List<`FileListRequest`> | int32 count + inlined; used when type=FILE_LIST |
| … | `streamReq` | List<`StreamFileDataRequest`> | int32 count + inlined |
| … | `taskId` | int32 LE | client-assigned id, echoed in the response |

### 2.2 LIST request — `FileListRequest`  (0x00/0x20 `uav_general_get_get_file_list`, full_table.txt:9)
Source `…/value/file/FileListRequest.smali` → `toBytes([BI)I`. **Fixed prefix = 46 B** (with 0 filters):
| off | field | type | meaning |
|----:|---|---|---|
| +0 | `index` | int32 LE | **start index** into the media list (paging cursor) |
| +4 | `count` | int32 LE | **how many entries** to return this page (page size) |
| +8 | `folderIndex` | int32 LE | folder to list (DCF dir); 0 for flat listing |
| +12 | `type` | int32 LE | `FileType`: **0=MEDIA** (photos/videos), 1=COMMON, 4=MEDIA_FOLDER |
| +16 | `slotLocation` | int32 LE | `CameraStorageSlot`: **0=EXTERN1 = the SD card** (default, see §6) |
| +20 | `receiver_type` | int32 LE | routing hint, default 0 (§6) |
| +24 | `receiver_index` | int32 LE | routing hint, default 0 |
| +28 | `isAllList` | u8 | true = "give me everything, ignore index/count" |
| +29 | `isSubMedia` | u8 | true = list sub-items of a group (burst/pano/hyperlapse frames) |
| +30 | filter count | int32 LE | number of `FileListRequestFilter` entries following |
| +34 | filter[i] | int32 LE ×count | media-type filter, see `FileListRequestFilter` in §8 (e.g. 25=ALL_PHOTO, 26=ALL_VIDEO) |
| +34+4n | `orderInfo` | nested (12 B) | `FileListRequestOrder`: `type`(0=TIME/1=SIZE) + `timeOrderType`(0=OLD_FIRST/1=NEW_FIRST) + `sizeOrderType`(0=LARGE/1=SMALL), each int32 LE |

Default ctor sets index=count=folderIndex=0, type=UNKNOWN, slot=UNKNOWN, both bools false, empty filter.
`uav/media/FileOperateHelper.smali` overrides `slotLocation` → `UAV_CAMERA_STORAGE_ID_EXTERN1` (=0) before send.

### 2.3 LIST response — `FileList` → `FilePackage` → `MediaFile[]`
`FileList` (`…/value/file/FileList.smali`): `slotLocation` int32 + `files`(nested `FilePackage`) + `hasInvalidFile` u8.
`FilePackage` (`…/value/file/FilePackage.smali`): `type` int32 + `media`(List<`MediaFile`>) + `common`(List<`CommonFile`>) + `folder`(List<`MediaFolder`>). Photos/videos come back in **`media`**.

**Per-file record = `MediaFile`** (`…/value/media/MediaFile.smali`, `toBytes` order — this is the grid row):
| # | field | type | meaning |
|--:|---|---|---|
|1| `valid` | u8 | record valid |
|2| `isManualGroupFile` | u8 | part of a manual group |
|3| `fileIndex` | int32 LE | **primary file id** on the SD (used to address for download/delete) |
|4| `fileType` | int32 LE | `MediaFileType`: **0=JPEG,1=DNG,2=MOV,3=MP4**,4=PANORAMA,… (§8) → photo vs video |
|5| `fileName` | string | e.g. `DJI_0001.JPG` / `DJI_0002.MP4` |
|6| `fileSize` | int64 LE | bytes |
|7| `date` | DateTime (24 B) | capture timestamp (Y/M/D h:m:s) |
|8| `starTag` | int32 LE | `MediaFileStarTag` 0=NONE,1=TAGGED |
|9| `isCloudDownload` | u8 | |
|10| `duration` | int64 LE | **video duration** (ms; 0 for photos) |
|11| `orientation` | int32 LE | |
|12| `cameraOrientation` | int32 LE | |
|13| `frameRate` | int32 LE | `VideoFrameRate` |
|14| `resolution` | int32 LE | `VideoResolution` |
|15| `photoWidth` | int32 LE | |
|16| `photoHeight` | int32 LE | |
|17| `videoType` | int32 LE | |
|18| `photoType` | int32 LE | |
|19| `panoType` | int32 LE | |
|20| `videoEncodeType` | int32 LE | H264/H265 |
|21| `videoCompressType` | int32 LE | |
|22| `videoSpeedRatio` | int32 LE | |
|23| `panoCount` | int32 LE | frames in a pano group |
|24| `panoHandleState` | int32 LE | |
|25| `hasOriginalFile` | u8 | |
|26| `guid` | int64 LE | globally-unique id |
|27| `fileGroupIndex` | int32 LE | **group id** (burst/pano/AEB set) |
|28| `subIndex` | int32 LE | index within the group |
|29| `segSubIndex` | int32 LE | segment sub-index (long videos split into segments) |
|30| `timeLapseInterval` | int32 LE | |
|31| `EXIFInfo` | nested `FileExifInfo` | ISO/shutter/aperture/etc. (see class) |
|32| `photoRatio` | int32 LE | |
|33| `subMediaFile` | List<`MediaFile`> | children of a group |
|34| `dcfInfo` | nested `DCFInfo` | `customKey`(str)+cameraType+directoryIndex+fileIndex+fileSetId+time(DateTime) |
|35| `isDcfSupported` | u8 | |
|36| `isEdcfSupported` | u8 | |
|37| `isPageLastFile` | u8 | **paging terminator — true on the last record of the page** |
|38| `dirIndex` | int32 LE | DCF directory index |
|39| `videoBeautifyInfo` | nested | |
|40| `hasProxy` | u8 | a low-res proxy exists |
|41| `proxyInfo` | nested `ProxyInfo` | |
|42| `isSize64File` | u8 | file > 4 GiB (needs 64-bit offsets) |
|43| `colorMode` | nested | |
|44| `nailInfo` | nested `PhotoAndVideoNailInfo` (32 B) | **thumbnail/screennail offsets & sizes — see §3** |
|45| `originFileState` | int32 LE | |
|46| `isVideoWithAudio` | u8 | |
|47| `bitDepth` | int32 LE | |
|48| `panoPicViewInfo` | nested | |
|49| `livePhotoInfo` | nested | |

`CommonFile` (non-media) is much smaller: `fileIndex` i32, `fileType` i32, `fileName` str, `fileSize` i64,
`date` DateTime, `md5` str.

**Paging / termination:** request `index`+`count`; the drone returns up to `count` `MediaFile`s with the
last one flagged `isPageLastFile=true`. The task-level response (`FileTaskResponse`, §2.5) additionally
reports `listLeft` = entries still not delivered. Loop: `index += returned` until `listLeft==0` (or a page
comes back with `isPageLastFile` and fewer than `count` rows). `isAllList=true` fetches the whole list in one go.

### 2.4 DOWNLOAD request — `FileDataRequest`  (0x00/0x1F `uav_general_get_get_file_data`, full_table.txt:8)
Source `…/value/file/FileDataRequest.smali` → `toBytes`. **Fixed prefix = 62 B** then `physicalPathInfo`
list, then `nailInfo` (32 B), then 5 trailing bytes. With empty `physicalPathInfo` (count=0) the blob is 103 B:
| off | field | type | meaning |
|----:|---|---|---|
| +0 | `index` | int32 LE | **file id = `MediaFile.fileIndex`** to download |
| +4 | `count` | int32 LE | number of files (1 for a single download) |
| +8 | `type` | int32 LE | `FileDataType`: **0=ORIGIN** (full-res), **1=THUMBNAIL**, **2=SCREEN** (preview), 3=CLIP, 18=PROXY… (§8) |
| +12 | `slotLocation` | int32 LE | 0 = SD (set by FileOperateHelper) |
| +16 | `offSet` | int64 LE | **byte offset to start from** (resume support) |
| +24 | `dataSize` | int64 LE | **number of bytes to fetch** (0 or full size = whole file) |
| +32 | `subIndex` | int32 LE | index within a group (burst/pano frame) |
| +36 | `segSubIndex` | int32 LE | video segment index |
| +40 | `receiver_type` | int32 LE | routing hint (default 0) |
| +44 | `receiver_index` | int32 LE | routing hint (default 0) |
| +48 | `mediaFileType` | int32 LE | `MediaFileType` of the target (0=JPEG…3=MP4) |
| +52 | `callbackOnce` | u8 | deliver in one callback vs streamed chunks |
| +53 | `isSize64File` | u8 | 64-bit file (use offSet/dataSize as true 64-bit) |
| +54 | `uuid` | int64 LE | client task uuid (correlates callbacks) |
| +62 | `physicalPathInfo` | List<`PhysicalPathInfo`> | int32 count + entries; usually **empty** (0x00000000) |
| … | `nailInfo` | nested `PhotoAndVideoNailInfo` (32 B) | for THUMBNAIL/SCREEN: the offsets/sizes from the list (see §3) |
| … | `callbackInConsumeWorker` | u8 | |
| … | `downloadRateLimit` | int32 LE | throttle, 0 = unlimited |

### 2.5 How the bytes come back — the reassembly contract (PROVEN at the Java boundary)
The native side issues the on-wire 0x00/0x1F transaction and pushes **reassembled** chunks up to Java.
Callback interfaces (DEX-03a5):
- `uav/media/album/jni/FileTaskCallback.invoke(int retCode, FileTaskResponse rsp, byte[] data)` — `data` = a received chunk.
- `uav/media/album/MediaFileDataTask$IFileDataTaskHolder`:
  - `a(long done, long total)` — **progress** (bytes received / total).
  - `c(FileDataRequest req, byte[] data, long offset)` — **a data chunk `data` to be written at `offset`**.
  - `d(int code, FileDataRequest req)` — **completion / error** (retCode).
  - `b(FileDataRequest req, int a, int b)` — status/paging counters.
- `uav/media/album/MediaFileDataTask.b(FileDataRequest, byte[] data, long offset, long size, long total)` — the concrete sink.

`FileTaskResponse` (`…/value/file/FileTaskResponse.smali`, the `rsp` object) carries the flow-control:
`taskType` i32, `fileType` i32, `listReq`/`dataReq`/`streamReq` (nested echoes), `requestClear` u8,
`listLeft` i32, `dataLeft` i32, `streamLeft` i32, `fileList` (nested `FileList`), `bitSpeed` f64,
`receivedDataSize` i64, `totalDataSize` i64, `streamInfoType` i32, `streamModifiedOffset` i64, `streamInfo` nested.

**Reassembly to a .jpg/.mp4:** open the target file, and for every `c(req, data, offset)` write `data` at
`offset`; you are done when `d(code,…)` fires with success or `receivedDataSize == totalDataSize`. Because
each chunk is self-describing `(offset, data)`, out-of-order or resumed chunks are trivial — no manual
selective-ACK needed at the Java level (the native does the ACKing).

### 2.6 The 0x00/0x8C `download_status_push` (full_table.txt:29)
`uav_general_download_status_push` is the DUML-level progress/status push. In this app it is consumed by
native and surfaced as `FileTaskResponse.receivedDataSize/totalDataSize/bitSpeed` and
`IFileDataTaskHolder.a(done,total)`. There is a `DownloadWorkingInfo{ running: u8 }` value
(`…/value/media/DownloadWorkingInfo.smali`) = a 1-byte "download in progress" flag. The exact on-wire 0x8C
payload is native (Frida: §7).

### 2.7 Which path? 0x1F/0x20 vs the 0x00/0x2A + 0x26 fragment path
**Media uses 0x00/0x1F (get_file_data) + 0x00/0x20 (get_file_list).** The `0x00/0x2A general_file_transfer`
+ `0x00/0x26 transfer_msg` path is the **generic** file/log/upgrade transfer — see the legacy
`uav/midware/data/model/P3/DataCommonTransferFileData.smali` (`doPack`: `[0]=TransferCmdType, [1..4]=sequence
(int32), [5..]=fileData`) and `DataCommonTransferFileDataExtended`. That is a different subsystem (flight
logs / firmware), not the photo gallery. Confirmed: the media task JNI (`JNIMediaTaskManager`) and
`FileOperateHelper` only ever build `FileListRequest`/`FileDataRequest`/`FileActionRequest`, never the P3
transfer objects.

---

## 3. THUMBNAILS & PREVIEWS (gallery grid)

**There is no separate "get_thumbnail" command.** A thumbnail/preview is just a `FileDataRequest` (0x00/0x1F,
§2.4) with a different `type`:
- `type = 1 (THUMBNAIL)` → small grid thumbnail.
- `type = 2 (SCREEN)` → larger "screennail" preview (full-screen preview before downloading original).
- `type = 0 (ORIGIN)` → the full-resolution file.
(`FileDataType` enum, §8. The parallel `MediaRequestType` enum has the identical 0=ORIGIN/1=THUMBNAIL/2=SCREEN.)

The offsets/sizes come from the list response. Each `MediaFile.nailInfo` is a
`PhotoAndVideoNailInfo` (`…/value/file/PhotoAndVideoNailInfo.smali`), **32 B, 4 × int64 LE in this order**:
| off | field | meaning |
|----:|---|---|
| +0 | `thumbNailSize` | int64 LE — thumbnail byte length |
| +8 | `screenNailSize` | int64 LE — screennail byte length |
| +16 | `thumbNailOffset` | int64 LE — thumbnail offset inside the source file |
| +24 | `screenNailOffset`| int64 LE — screennail offset |

So to fetch a grid thumbnail for a `MediaFile`: send `FileDataRequest{ index=fileIndex, type=THUMBNAIL,
mediaFileType=fileType, offSet=nailInfo.thumbNailOffset, dataSize=nailInfo.thumbNailSize, slotLocation=0 }`
(and copy `nailInfo` into the request's `nailInfo` field). The bytes arrive **the same way as a full
download** — via the `IFileDataTaskHolder.c(req, data, offset)` chunk callbacks (§2.5), just tiny. For JPEG
originals the thumbnail is an embedded EXIF thumbnail; the offset/size tell the camera exactly which slice to
return, which is why thumbnails come back in essentially one small chunk.

---

## 4. PLAYBACK / PREVIEW MODE (entering the gallery, selecting an item)

These are **camera** commands (cmd_set 0x02, receiver = camera 0x01). Their value objects live in DEX-0451.
The KeyValue→cmd_id binding is native (full_table.txt is the authority); payload semantics below are proven
from the value classes.

### 4.1 `0x02/0x0C switch_playbackmode`  (full_table.txt:70, `uav_camera_switch_playbackmode`)
Enter/leave gallery (playback) mode. Strategy value = `SwitchPlaybackModeStrategy`
(`…/value/camera/SwitchPlaybackModeStrategy.smali`, single-int enum):
`0=COMMON_STRATEGY, 1=SPECIAL_COMMAND_STRATEGY, 2=NON_FLAT_MODE_STRATEGY`.
On the Mini this switches the camera into playback so the file list / thumbnails become available.
Payload is the strategy int; whether it is 1 byte or the standard int32 is set in native — **Frida (§7)**.
(There is a paired `SwitchPlaybackModeStrategyMsg` wrapper and a `PlaybackMode` state enum.)

### 4.2 `0x02/0x7A video_playback_control`  (full_table.txt:129, `uav_camera_video_playback_control`)
Transport control while previewing a video (play/pause/seek). Playback state is `PlaybackStatus`
(`…/value/media/PlaybackStatus.smali`): `0=PREPARED,1=PLAYING,2=PAUSED,3=ENDED,4=STOPPED,5=BUFFERING`.
Seek/position info is `VideoPlayInfo` (`…/value/media/VideoPlayInfo.smali`): `index` i32 + `duration` i32 +
`frameRate` i32 (12 B). Exact 0x7A field packing (which of these it carries) is native — **Frida (§7)**.

### 4.3 `0x02/0x7B single_playback_select`  (full_table.txt:130, `uav_camera_single_playback_select`)
Select one item to preview in playback mode. Addresses the item by its **file index** (the same
`MediaFile.fileIndex`). Payload = the selected index; exact width native — **Frida (§7)**.

> These three are for *streaming preview inside the app*. For our controller they are **optional**: you can
> list + thumbnail + download entirely through §2/§3 without ever entering playback mode. Playback mode
> mainly matters if you want the drone to *decode and stream* a stored video back over the liveview channel.

---

## 5. DELETE

Two mechanisms — the CSDK/file path (what DJI Fly v5 actually uses) and the legacy camera command.

### 5.1 `FileActionRequest`  (0x00/0x28 `uav_general_delete_file`, full_table.txt:11-12)
Source `…/value/file/FileActionRequest.smali` → `toBytes`:
| off | field | type | meaning |
|----:|---|---|---|
| +0 | `slotLocation` | int32 LE | 0 = SD |
| +4 | `type` | int32 LE | `FileActionType`: **3=DELETE_SINGLE, 4=DELETE_ALL**, 1=TAG_STAR, 2=CANCEL_STAR, 7=DELETE_SINGLE_MAGIC (§8) |
| +8 | `files` | nested `FilePackage` | the file(s) to act on: `type`(FileType) + `media`(List<MediaFile>) + `common`(List<CommonFile>) + `folder`(List<MediaFolder>) |
| … | `batchTagInfo` | nested `MediaFileTag` (4 B) | 4 bools: `stared, starTagValid, syncedToCloud, cloudTagValid` (only for star/tag actions) |

**How a file is addressed:** by putting its full `MediaFile` (or `CommonFile`) record into
`files.media` (resp. `files.common`) — i.e. **by the record**, whose `fileIndex` (+ `fileName`, `guid`) is
the identity. Not a bare integer index. For a single delete: `type=DELETE_SINGLE`, `files.type=MEDIA`,
`files.media=[theMediaFile]`. `FileOperateHelper.smali` sets `slotLocation=EXTERN1` before send.
Batch action goes through `JNIMediaTaskManager.mediaFileBatchAction(…, FileActionRequest, FileActionCallback)`.

**Response** `FileActionResponse` (`…/value/file/FileActionResponse.smali`): `slotLocation` i32 +
`succeeded`(FilePackage) + `failed`(FilePackage) + `allSucceeded` u8. Delivered via
`FileActionCallback.invoke(int retCode, FileActionResponse)` (DEX-03a5).

### 5.2 `MediaDeletionRequest` (media-layer variant, same 0x00/0x28 semantics)
`…/value/media/MediaDeletionRequest.smali`: `slotLocation` i32 + `files`(List<MediaFile>) + `isDeleteAll` u8.
Simpler shape for "delete these N files" / "wipe card".

### 5.3 `0x02/0x79 delete_photo` (legacy camera delete, full_table.txt:128, `uav_camera_delete_photo`)
The older per-camera delete. DJI Fly v5's gallery uses the 0x00/0x28 file path above; 0x02/0x79 is the
camera-native equivalent (addresses by index). Payload is native — **Frida (§7)** if you need it; prefer §5.1.

---

## 6. STORAGE / SD INFO

Two different commands — do not confuse them:

### 6.1 `0x02/0x98 get_file_system_info`  (full_table.txt:138, `uav_camera_get_file_system_info`)
Maps to `FileSystemInfo` (`…/value/media/FileSystemInfo.smali`) — **DCF directory bookkeeping, NOT capacity**:
| off | field | type | meaning |
|----:|---|---|---|
| +0 | `maxDirNumber` | int32 LE | max DCF directories |
| +4 | `lastestDirNumber` | int32 LE | current/last dir index |
| +8 | `maxFileNumberInDir` | int32 LE | max files per dir |
(12 B.) Use this to compute the next file/dir index, not free space.

### 6.2 Free / used / counts — `StorageInfoMsg` (camera SD-state key/push)
Source `…/value/camera/StorageInfoMsg.smali` → `toBytes`:
| off | field | type | meaning |
|----:|---|---|---|
| +0 | `slot` | int32 LE | `CameraStorageSlot` (0=SD) |
| +4 | `location` | int32 LE | `CameraStorageLocation` 0=SDCARD,1=EMMC,2=INTERNAL_SSD |
| +8 | `isInserted` | u8 | card present |
| +9 | `state` | int32 LE | `CameraStorageState` 0=NORMAL,1=NOT_INSERTED,8=FULL,… (§8) |
| +13 | `totalCapacity` | int32 LE | **total** (units: MB — camera reports capacity in MB) |
| +17 | `remainCapacity` | int32 LE | **free** (MB) → used = total − remain |
| +21 | `availablePhotoCount` | int32 LE | **remaining still-photo count** |
| +25 | `availableVideoDuration`| int32 LE | **remaining video seconds** |
(29 B.) This is the value to display "X GB free, N photos left". It is a camera status key (pushed /
polled), not 0x02/0x98.

---

## 7. RECEIVER ADDRESSING, TRANSPORT, and the FRIDA HOOKS that close the gaps

**Transport:** every command here is a normal DUML frame → wrapped by `composite.wrap(frame, 0x5749)` →
AOA bulk. This is our only media-capable channel (per MASTER_REPORT §1), so it is the right and only path.
Our `duml.py` builds the frame; the payloads above are the `payload` argument.

**Receiver (DUML byte [5]):** the value objects carry CSDK routing hints (`receiver_type`/`receiver_index`,
left 0 by `FileOperateHelper`), which are **not** the DUML receiver byte. The DUML sender/receiver/seq/
ack-flag are stamped by native (`libsdk_jni`) from the cmd_set:
- cmd_set 0x00 (general file cmds 0x1F/0x20/0x28) and cmd_set 0x02 (camera 0x0C/79/7A/7B/98) → the SD lives
  on the **camera**, so receiver is **camera 0x01** (possibly the video board **dm368 0x08** for the data
  pump). This must be read off the wire, not guessed.

**Frida hooks (Java unless noted):**
| # | to settle | hook |
|--:|---|---|
| H1 | value-object → cmd_set/cmd_id binding, and the exact `[B` sent | `uav.media.album.jni.JNIMediaTaskManager.native_FileTaskPushBack` / `native_MediaFileBatchAction` (dump arg `[B`) |
| H2 | the DUML header native adds (sender, **receiver**, seq, ack) + raw 0x1F/0x20/0x28 frames | native hook on the composite/AOA writer (`duss_parse_composite_data` sibling send fn ~`0x491a0xx`), or on `libsdk_jni` DUML `send` |
| H3 | on-wire fragment framing of 0x00/0x1F data responses (chunk header, selective-ACK) | native DUML recv for cmd_set 0x00 id 0x1F; cross-check with Java `uav.media.album.MediaFileDataTask$IFileDataTaskHolder.c(req, data, offset)` |
| H4 | 0x00/0x8C download_status_push exact payload | native recv 0x00/0x8C; Java surface `IFileDataTaskHolder.a(done,total)` |
| H5 | 0x02/0x0C, 0x02/0x7A, 0x02/0x7B exact payload widths | hook the KeyValue action send for those keys; or native 0x02 send |
| H6 | confirm `ByteStream` blob == on-wire payload (native pass-through vs re-pack) | `uav.sdk.keyvalue.value.file.FileListRequest.toBytes([BI)I` return + compare against H2 raw frame |

If native pass-through is confirmed by H6 (blob == payload), the layouts in §2-§6 are directly the DUML
payloads and can be hand-built in Python with zero JNI.

---

## 8. ENUM VALUES (all from DEX-0451 `<clinit>`, `value()` = wire int32)

```
FileType:              MEDIA=0, COMMON=1, SPEAKER_AUDIO=2, PAYLOAD_WIDGET_JSON=3, MEDIA_FOLDER=4, UNKNOWN=65535
FileTaskType:          FILE_DATA=0, FILE_LIST=1, FILE_STREAM=2, UNKNOWN=65535
FileDataType:          ORIGIN=0, THUMBNAIL=1, SCREEN=2, CLIP=3, STREAM=4, PANO=5, PANOSCREENNAIL=6,
                       PANOTHUMBNAIL=7, TIMELAPSESCREENAIL=8, MP4FILE=9, CUSTOM_DATA=10, PHOTO_METADATA=11,
                       USER_CTRL_INFO=12, JSON=13, PAYLOAD_WIDGET_JSON=14, PROXY_MOOV=15, ORIGIN_MOOV=16,
                       AIS=17, PROXY=18, MET=24, GROUP_AIS=36, SCREEN_PLAYBACK=65534, UNKNOWN=65535
MediaRequestType:      ORIGIN=0, THUMBNAIL=1, SCREEN=2, CLIP=3, STREAM=4, PANO=5, ... PROXY=18, UNKNOWN=65535
MediaFileType:         JPEG=0, DNG=1, MOV=2, MP4=3, PANORAMA=4, TIFF=5, UL_CTRL_INFO=6, UL_CTRL_INFO_LZ4=7,
                       SEQ=8, TIFF_SEQ=9, AUDIO=10, AIS=11, PAYLOAD_WIDGET_JSON=15, PHOTO_FOLDER=16,
                       VIDEO_FOLDER=17, FOLDER_ATTR=18, LRF=19, THM=20, SCR=21, MET=26,
                       PANO_WITHOUT_FUSION=29, OSV=41, MASTER_SHOT_GROUP=43, UNKNOWN=65535
FileActionType:        NONE=0, TAG_STAR=1, CANCEL_STAR=2, DELETE_SINGLE=3, DELETE_ALL=4, STOP_BATCH_STAR=5,
                       STOP_BATCH_DELETE=6, DELETE_SINGLE_MAGIC=7, START_BATCH_TAG=8, STOP_BATCH_TAG=9, UNKNOWN=65535
CameraStorageSlot:     EXTERN1=0 (SD), INTERNAL1=1, EXTERN2=2, EXTERN3=3, UNKNOWN=65535
CameraStorageLocation: SDCARD=0, EMMC=1, INTERNAL_SSD=2, UFS=3, UNKNOWN=65535
CameraStorageState:    NORMAL=0, NOT_INSERTED=1, INVALID=2, READ_ONLY=3, FORMAT_NEEDED=4, FORMATTING=5,
                       INVALID_FS=6, BUSY=7, FULL=8, SLOW=9, UNKNOWN_ERROR=10, NO_REMAINING_FILE_INDICES=11,
                       INITIALIZING=12, FORMAT_RECOMMENDED=13, RECOVERING_FILES=14, WRITING_SLOWLY=15, USB_CONNECTED=16
FileListRequestFilter: ALL=0, LIKED=1, DISLIKED=2, PHOTO_NORMAL=4, PHOTO_HDR=5, PHOTO_AEB=6, PHOTO_BURST=7,
                       PHOTO_INTERVAL=8, PHOTO_PANO=9, VIDEO_NORMAL=16, VIDEO_SLOWMOTION=17, VIDEO_TIMELAPSE=18,
                       VIDEO_HYPERLAPSE=19, VIDEO_HDR=20, VIDEO_LOOP=21, VIDEO_..._STORY=22, VIDEO_QUICKSHOT=23,
                       VIDEO_SHORTVIDEO=24, ALL_PHOTO=25, ALL_VIDEO=26, UNKNOWN=65535
FileListRequestOrderType: TIME=0, SIZE=1 ; TimeOrderType: OLD_FIRST=0, NEW_FIRST=1 ; SizeOrderType: LARGE_FIRST=0, SMALL_FIRST=1
MediaFileStarTag:      NONE=0, TAGGED=1, UNKNOWN=255
StreamFileDataType:    ORIGIN=0, PROXY=1, UNKNOWN=65535
PathType:              MAIN=1, MISC=2, UNKNOWN=65535
FileLocation:          SD_CARD=0, INTERNAL_STORAGE=1, EXTENDED_SD_CARD=2, UNKNOWN=65535
FileSystemInfoType:    DCF=0, CLIP=1, UNKNOWN=65535
SwitchPlaybackModeStrategy: COMMON_STRATEGY=0, SPECIAL_COMMAND_STRATEGY=1, NON_FLAT_MODE_STRATEGY=2
PlaybackStatus:        PREPARED=0, PLAYING=1, PAUSED=2, ENDED=3, STOPPED=4, BUFFERING=5, UNKNOWN=65535
```

---

## 9. WM160 (Mavic Mini 1) applicability

- These are **general (0x00)** and **camera (0x02)** commands — not model-specific camera variants. The
  media stack the Mini uses is the generic CSDK KeyValue/JNI media manager (`uav.media.*`,
  `JNIMediaTaskManager`, `FileOperateHelper`), the same one shown here. There is no separate "Mini media"
  class; the model only changes which `MediaFileType`/filters actually appear (Mini = JPEG/DNG photos,
  MP4/MOV video, QuickShots; no pano-fusion/SSD).
- Storage slot on the Mini = **`UAV_CAMERA_STORAGE_ID_EXTERN1` (0)** = the microSD (Mini has no internal
  storage) — hard-coded default in `FileOperateHelper`.
- Model gating: nothing here is behind a WM160 capability flag in the file/media path; gating in DJI Fly is
  on *editing/cloud* features, not list/download/delete. (Confirm the live capability matrix on connect per
  MASTER_REPORT §9, but the transport commands themselves are model-agnostic.)
- Caveat repeated from §0: the value→cmd binding and DUML header come from native; verify H1/H2/H6 once on
  hardware and the Python implementation below is exact.

---

## 10. IMPLEMENTATION — ready-to-code algorithms

Encoding helpers (match `ByteStreamHelper`, all little-endian):
```python
import struct
def w_i32(v):  return struct.pack("<i", v)                 # H / enum.value()
def w_i64(v):  return struct.pack("<q", v)                 # N
def w_f64(v):  return struct.pack("<d", v)                 # y
def w_bool(v): return struct.pack("<B", 1 if v else 0)     # i
def w_str(s):  b=s.encode();  return struct.pack("<i", len(b))+b   # T
def w_bytes(b):return struct.pack("<i", len(b))+b          # l
def w_list(items): return struct.pack("<i", len(items))+b"".join(items)  # c / K (items already encoded)
def w_datetime(y,mo,d,h,mi,s): return b"".join(w_i32(x) for x in (y,mo,d,h,mi,s))  # 24 B
```

### 10.1 Encode a LIST request (page) — body of DUML 0x00/0x20, receiver=camera(0x01)
```python
def file_list_request(index, count, *, file_type=0, slot=0,
                      is_all=False, is_sub=False, filters=(),
                      order_type=0, time_order=1, size_order=0):  # time_order 1=NEW_FIRST
    b  = w_i32(index) + w_i32(count) + w_i32(0)          # index, count, folderIndex
    b += w_i32(file_type) + w_i32(slot)                  # type(FileType.MEDIA=0), slotLocation(SD=0)
    b += w_i32(0) + w_i32(0)                             # receiver_type, receiver_index
    b += w_bool(is_all) + w_bool(is_sub)
    b += w_list([w_i32(f) for f in filters])             # filterlist
    b += w_i32(order_type) + w_i32(time_order) + w_i32(size_order)   # orderInfo (nested, 12 B)
    return b
```

### 10.2 Encode a DOWNLOAD/THUMBNAIL request — body of DUML 0x00/0x1F
```python
def file_data_request(file_index, media_file_type, data_type, off, size, *,
                      slot=0, sub_index=0, seg_sub=0, uuid=0,
                      nail=(0,0,0,0)):        # (thumbSize,screenSize,thumbOff,screenOff)
    b  = w_i32(file_index) + w_i32(1) + w_i32(data_type) + w_i32(slot)  # index,count,type,slot
    b += w_i64(off) + w_i64(size)                        # offSet, dataSize
    b += w_i32(sub_index) + w_i32(seg_sub)               # subIndex, segSubIndex
    b += w_i32(0) + w_i32(0)                             # receiver_type, receiver_index
    b += w_i32(media_file_type)                          # mediaFileType
    b += w_bool(False) + w_bool(size > 0xFFFFFFFF)       # callbackOnce, isSize64File
    b += w_i64(uuid)
    b += w_list([])                                      # physicalPathInfo (empty)
    b += b"".join(w_i64(x) for x in nail)                # nailInfo (32 B)
    b += w_bool(False) + w_i32(0)                        # callbackInConsumeWorker, downloadRateLimit
    return b
# full-res:  data_type=0 (ORIGIN), off=0, size=fileSize
# thumbnail: data_type=1 (THUMBNAIL), off=nail.thumbNailOffset, size=nail.thumbNailSize
# preview:   data_type=2 (SCREEN),   off=nail.screenNailOffset, size=nail.screenNailSize
```

### 10.3 Encode a DELETE — body of DUML 0x00/0x28
```python
def delete_single(mediafile_bytes, slot=0):
    # files = FilePackage{ type=MEDIA(0), media=[MediaFile], common=[], folder=[] }
    filepkg = w_i32(0) + w_list([mediafile_bytes]) + w_list([]) + w_list([])
    tag = w_bool(False)*4                                # MediaFileTag (unused for delete)
    return w_i32(slot) + w_i32(3) + filepkg + tag        # type=DELETE_SINGLE(3)
```
(Simplest addressing: keep the exact `MediaFile` bytes you decoded from the list response and pass them back.
`fileIndex`+`fileName`+`guid` are what the drone matches on.)

### 10.4 Download state machine (end-to-end)
```
1. (optional) 0x02/0x0C switch_playbackmode(COMMON_STRATEGY) to enter gallery mode.
2. total = None; index = 0; files = []
   loop:
     send 0x00/0x20 file_list_request(index, PAGE, file_type=MEDIA, filters=[ALL_PHOTO|ALL_VIDEO], time_order=NEW_FIRST)
     recv FileList → parse FilePackage.media as MediaFile[] (decode with §2.3 layout)
     files += page
     if page empty or last row.isPageLastFile or (task) listLeft==0: break
     index += len(page)
3. pick a MediaFile f. For the grid, first pull thumbnails:
     send 0x00/0x1F file_data_request(f.fileIndex, f.fileType, THUMBNAIL, f.nail.thumbOff, f.nail.thumbSize, nail=f.nail)
     collect (offset,data) chunks → JPEG thumbnail.
4. To download original:
     send 0x00/0x1F file_data_request(f.fileIndex, f.fileType, ORIGIN, 0, f.fileSize)
     open out = f.fileName
     on each recv chunk (offset,len,data): out.seek(offset); out.write(data); received += len
     progress = received/total  (or read receivedDataSize/totalDataSize from FileTaskResponse / 0x8C push)
     done when received==f.fileSize (or completion/success status). Resume: re-request with offSet=received.
5. To delete:  send 0x00/0x28 delete_single(f_bytes); wait FileActionResponse.allSucceeded.
```

### 10.5 Minimal "browse gallery" sequence (the coordinator's ask)
```
enter gallery : 0x02/0x0C switch_playbackmode(strategy=COMMON_STRATEGY)          (optional for pull-only)
list N        : 0x00/0x20 file_list_request(index=0, count=N, file_type=MEDIA,
                          filters=[ALL_PHOTO=25] or [ALL_VIDEO=26], order NEW_FIRST)   → MediaFile[]
thumbnails    : for each MediaFile → 0x00/0x1F type=THUMBNAIL using its nailInfo       → JPEG bytes
select one    : 0x02/0x7B single_playback_select(fileIndex)  (only if you want in-app video preview)
preview video : 0x02/0x7A video_playback_control(PLAY/PAUSE) + VideoPlayInfo(seek)     (optional)
download full : 0x00/0x1F type=ORIGIN offSet=0 dataSize=fileSize                        → .jpg/.mp4
delete        : 0x00/0x28 FileActionRequest type=DELETE_SINGLE files=[MediaFile]        → ack
```
Photo vs video: `MediaFile.fileType` (0/1 = JPEG/DNG photo, 2/3 = MOV/MP4 video) and/or the request
`filters` (ALL_PHOTO=25 / ALL_VIDEO=26). Group sets (burst/pano/AEB): `fileGroupIndex` + `subIndex` +
`isManualGroupFile`; children live in `MediaFile.subMediaFile` and are fetched with `subIndex`/`isSubMedia`.

---

## 11. Evidence index (class → file → what it proves)
- `uav/sdk/keyvalue/value/ByteStreamHelper.smali` (DEX-0451) — the serialization primitives & sizes (§1).
- `…/value/file/FileListRequest.smali` `toBytes` — LIST request layout (§2.2).
- `…/value/file/FileDataRequest.smali` `toBytes` — DOWNLOAD/THUMBNAIL request layout (§2.4/§3).
- `…/value/file/FileActionRequest.smali` + `…/value/media/MediaDeletionRequest.smali` — DELETE (§5).
- `…/value/file/FileList.smali`,`FilePackage.smali`,`CommonFile.smali`,`…/value/media/MediaFile.smali`,`MediaFileList.smali` — LIST response records (§2.3).
- `…/value/file/PhotoAndVideoNailInfo.smali` — thumbnail offsets/sizes (§3).
- `…/value/file/FileTaskRequest.smali`,`FileTaskResponse.smali` — task envelope & flow-control counters (§2.1/§2.5).
- `…/value/media/FileSystemInfo.smali` — 0x02/0x98 DCF info (§6.1); `…/value/camera/StorageInfoMsg.smali` — free/used/counts (§6.2).
- `…/value/camera/SwitchPlaybackModeStrategy.smali`,`…/value/media/PlaybackStatus.smali`,`VideoPlayInfo.smali` — playback (§4).
- `uav/media/album/jni/JNIMediaTaskManager.smali`,`FileTaskCallback.smali`,`FileActionCallback.smali` (DEX-03a5) — JNI boundary & callbacks (§0/§2.5).
- `uav/media/album/MediaFileDataTask.smali` + `$IFileDataTaskHolder` (DEX-03a5) — `(offset,data)`/progress/completion contract (§2.5).
- `uav/media/FileOperateHelper.smali` (DEX-03a5) — slot=EXTERN1 default, request assembly (§2.2/§6).
- `uav/midware/data/model/P3/DataCommonTransferFileData.smali` — the *other* 0x2A/0x26 transfer path (logs/upgrade), NOT media (§2.7).
- `reverse_docs/full_table.txt` lines 8/9/11-12/29/70/128-130/138, `cmdmap.txt` — cmd_set/cmd_id ↔ message names.
```
```
