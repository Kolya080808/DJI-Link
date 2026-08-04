# MEDIA PROTOCOL — STATIC TRUTH FROM DJI Fly v1.21.4 DEX

Scope: what the DEX of DJI Fly v1.21.4 (`reverse_docs/unpacked_app_dex/classes_*.dex`, 16 files)
statically proves about the SD-card media protocol (LIST / DOWNLOAD / DELETE / COUNT) on WM160
family, vs what `dji_link_beta/media.py` currently assumes.

Decompilation: jadx 1.5.6. Full dumps of `classes_016b200c.dex` and `classes_0451d00c.dex` plus
targeted `--single-class` decompiles (exact class path given for every fact below).

Legend: **CONFIRMED** = byte-accurate layout or enum value read from DEX code.
**HYPOTHESIS** = class exists but wire use for WM160 not proven.
**NOT IN DEX** = the logic lives in native code (libdjisdk JNI libs / `com.uav.crossplayback`
C++ .so, not present on disk) — cannot be confirmed without a wire capture.

---

## TL;DR

- `media.py`'s **legacy "litchis" request headers and inner layouts are CONFIRMED** (LIST and
  DOWNLOAD requests match the DEX `DataRequestList` / `DataRequestFile` exactly). That path is
  real code, but it is the *legacy Java* pipeline — see §6 on whether WM160 actually uses it.
- **Playback entry** into the media mode: `CmdSet=0x02 (CAMERA), CmdId=0x10 (SetMode),
  payload = {0x03}` where 3 = `CameraWorkMode.MEDIA_DOWNLOAD`. **CONFIRMED** in DEX (value of
  the enum read from `CameraWorkMode.java`; only the reference class `DataCameraGetMode` is
  stripped from the binary).
- **The actual file-list record format** the app parses today is `MediaFile.fromBytes`
  (uav.sdk.keyvalue), fed by native code. `fileSize` is **u64** and sits right after the
  name string; thumbnail offsets come from an embedded `nailInfo` block of 4×u64 —
  see §4 for the full layout.
- **DELETE has no Java `doPack` anywhere**: `CmdIdCommon.DeleteFile = 0x28` exists as an enum
  entry, but the class `DataCameraDeleteFile` (and `GetPushFiles`/`GetPushFile`) is **absent
  from all 16 dex files**. The app's delete path ends at
  `PlayBackManagerForAndroid.CppProxy.deleteFiles(...)` → **native only**. Any `0x28` payload
  layout in `media.py` (e.g. `count:u16 + indices`) is **UNVERIFIED — capture-pending**.
- **COUNT**: no dedicated Num request/response in active Java code. Legacy enum has
  `CmdId Num=3` (unused path); the modern SDK derives counts from the list task state
  (`isPageLastFile` flag per record, `MediaFileListStateMsg`). No wire-level count packet
  confirmed in DEX.

---

## 1. DUML envelope and the legacy file-transport header (FileSendPack / FileRecvPack)

Sources:
- `uav.midware.data.packages.P3.FileSendPack` — classes_0451d00c.dex
- `uav.midware.data.packages.P3.FileRecvPack` — classes_0451d00c.dex
- `uav.midware.data.config.litchis.DataConfig` (CmdId/CmdType/SubType enums) — classes_016b200c.dex

File-transport inner header (prepended to every file-transfer payload, **after** the normal
DUML header), 10 bytes, **CONFIRMED**:

```
[0]    0x0A | (version << 6)        # version = 10?? No: field 'a' = protocol version.
                                    # packed byte = 0x0A|(ver<<6); for ver=1 → 0x4A
[1]    (CmdId << 5) | CmdType       # see enums below
[2:4]  total length u16 LE          # (only low 12 bits meaningful on receive, see below)
[4:6]  sessionId u16 LE
[6:10] offset u32 LE (0 in requests)
```

`FileRecvPack` parses the same fields **except**: `total = u16@[2] >> 12` (top 4 bits =
packet index/total counter) and `len = u16@[2] & 0xFFF`. This asymmetry is in the DEX, not a
typo — mirror it if doing reassembly.

litchis `DataConfig` enums, **CONFIRMED**:

- `CmdId`: List=0, File=1, Stream=2, Num=3
- `CmdType`: REQUEST=0, DATA=1, ACK=2, PUSH=3, ABORT=4, DEL=5, PAUSE=6, RESUME=7
- `SubType`: ORG=0, THM=1, SCR=2, CLIP=3, Stream=4, ...

The DUML slot carrying the file transport: CmdSet `COMMON(0)`, CmdId
`RequestFile = 38 = 0x26`, sender APP(2) → receiver CAMERA(1). **CONFIRMED**
(`uav.midware.data.model.P3.DataAppRequest.start`).

`CmdIdCommon` also defines: RequestSendFiles=0x22, AckReceiveFiles=0x23,
GetPushFiles=0x24, SetResendFiles=0x25, RequestFile=0x26, GetPushFile=0x27,
DeleteFile=0x28. **Enum entries confirmed; model classes for 0x24/0x27/0x28 absent** (see §5).

`media.py` consistency: header pack/unpack and these enums match the DEX. **Confirmed part kept.**

---

## 2. LIST request — CONFIRMED

Source: `uav.midware.data.model.litchis.DataRequestList.doPack` (classes_0451d00c.dex).

Inner payload (after the 10-byte file header, CmdId=List(0), CmdType=REQUEST(0)), 7 bytes:

```
[0:4]  startIndex u32 LE ; byte[3] |= storage.value() << 6   # storage id in top 2 bits
[4:6]  count u16 LE                                          # requested count, NOT a session id
[6]    subType u8                                            # SubType enum (ORG=0...)
```

Matches `media.py`'s list request builder byte-for-byte. **Confirmed.**

Legacy list *response* container (rarely used path, see §6):
`DataCameraFileSystemListInfo` (PUSH, CmdId=List): payload =
`u32 startIndex? @0` + `u32 getDataLength @4`.
`DataCameraFileSystemFileData` first chunk (offset==0) carries a 13-byte pre-header:
`u32 size @0`, `u32 ? @4`, `u32 index @8`, `u8 nameLen @12`, then `name[nameLen]@13`.

---

## 3. DOWNLOAD request — CONFIRMED

Source: `uav.midware.data.model.litchis.DataRequestFile.doPack` (classes_0451d00c.dex).

Inner payload (CmdId=File(1), CmdType=REQUEST(0)), 16 bytes:

```
[0:4]   fileIndex u32 LE
[4:6]   subIndex u16 LE
[6]     subType u8
[7]     grade u8
[8:12]  offset u32 LE
[12:16] length u32 LE
```

Matches `media.py`. **Confirmed.**

Flow-control packets (same CmdId family), **CONFIRMED**:
- `DataRequestAck`: `[0:4] session/next-chunk u32, [4] count u8, then count × (u32,u32) pairs`.
- `DataRequestAbort`: CmdType=ABORT(4), `[0:4] reason u32` (Force=1).

---

## 4. Modern file-list record — `MediaFile.fromBytes` — CONFIRMED (the dataset the app really parses)

Source: `uav.sdk.keyvalue.value.media.MediaFile` (classes_0451d00c.dex), helpers
`ByteStreamHelper`: `F = i32 LE (4B)`, `L = i64 LE (8B)`, `g = u8 bool`, `w = f64 (8B)`,
`R = string (u32 len + bytes)`, plus nested-record decode from the same
`uav.sdk.keyvalue.value.*` package (all in classes_0451d00c.dex, fully decompiled).

Sequential field layout of one list entry (all little-endian):

```
u8   valid
u8   isManualGroupFile
u32  fileIndex
u32  fileType
str  fileName               # R: u32 len + bytes
u64  fileSize               # << AFTER the name, 8 bytes
date DateTime               # 6 x u32: year, month, day, hour, minute, second (24 B)
u32  starTag
u8   isCloudDownload
u64  duration
u32  orientation
u32  cameraOrientation
u32  frameRate
u32  resolution
u32  photoWidth
u32  photoHeight
u32  videoType
u32  photoType
u32  panoType
u32  videoEncodeType
u32  videoCompressType
u32  videoSpeedRatio
u32  panoCount
u32  panoHandleState
u8   hasOriginalFile
u64  guid
u32  fileGroupIndex
u32  subIndex
u32  segSubIndex
u32  timeLapseInterval
exif FileExifInfo           # variable, see below
u32  photoRatio
list subFiles               # List<MediaFile>, nested, same format (count-prefixed z-list)
dcf  DCFInfo                # str customKey; u32 cameraType, directoryIndex, fileIndex,
                            # fileSetId; DateTime time  → variable length
u8   isDcfSupported
u8   isEdcfSupported
u8   isPageLastFile         # << paging terminator: 1 = last record of the list
u32  dirIndex
vb   VideoBeautifySettingsInfo  # u8 control; 8 x u32 effects  (33 B)
u8   hasProxy
px   ProxyInfo                  # u32 proxyIndex; u64 proxySize; u64 proxyDuration;
                                # u32 proxyFrameRate; u32 proxyRotation; u32 proxyResolution (32 B)
u8   isSize64File
lut  CameraColorLutVersion      # u32 color; LutVersion { u32 main, u32 sub } (12 B)
nail PhotoAndVideoNailInfo      # u64 thumbNailSize, u64 screenNailSize,
                                # u64 thumbNailOffset, u64 screenNailOffset   (32 B)
u32  originFileState
u8   isVideoWithAudio
u32  bitDepth
pano PanoPicViewInfo            # 5 x f64: hfov, q0..q3  (40 B)
lp   LivePhotoInfo              # u64 thumbnailFrameId  (8 B)
```

Nested `FileExifInfo.fromBytes` (variable!): 8 x u8 enable-flags; 7 x u32
(exposureProgram, iso, meteringMode, lightSource, focalLength35mmFormat,
shutterNumerator, shutterDenominator); 3 x string (shutterSpeedText, apertureText,
exposureCompensationText); u8 hasProxy; ProxyInfo (32 B); count-prefixed
List\<PhysicalPathInfo\>.

Key consequences for capture/parse work:
- `fileSize` is u64 (LE) and follows the length-prefixed name — not a fixed-offset field.
- Thumbnail access is by file-internal offsets (`nailInfo`: thumbNailOffset/size,
  screenNailOffset/size, all u64) — thumbnails are carved out of the origin file,
  not downloaded over a separate command.
- End-of-list is signalled per-record by `isPageLastFile`, not by a COUNT response.

Who produces the bytes: native. The Java caller is
`JNIFile.transferList(FileListRequest)` / `PlayBackManagerForAndroid.*fetchMediaFiles*`
(native CppProxy methods, see §5/§6). Wire framing of the native list stream is
**capture-pending**.

---

## 5. DELETE — NOT IN DEX (native only)

Static findings:

1. `CmdIdCommon.DeleteFile = 0x28` enum entry exists, but its model class
   `DataCameraDeleteFile` is **absent from all 16 dex** (`all_classes.txt` negative;
   same for `GetPushFiles`(0x24) and `GetPushFile`(0x27)). CONCLUSION: CmdIdCommon
   0x28 is never packed by Java in v1.21.4.
2. Playback delete path (used by the real media UI):
   `V1PlaybackManageFileKt$v1PlaybackManageFileDeleteFiles$1` (classes_08fe100c.dex)
   maps UI `MediaFile`s to `com.uav.crossplayback.playback.PlaybackMediaFile` and calls
   `PlayBackManagerForAndroid.deleteFiles(ArrayList<PlaybackMediaFile>, DeleteCallback)`.
   `PlayBackManagerForAndroid.CppProxy.deleteFiles` (classes_0855200c.dex) is
   `static native` — a DJI djinni-style C++ bridge (`nativeRef` handle). Also native:
   `deleteOnlyOriginalFiles`, `cancelDeleteFile`, all `fetch*`, `downloadMediaFileRawData`,
   `fetchThumbnail`, `fetchPreview`, etc.
   Files are keyed by hash(fileIndex, subIndex, fileGroupIndex, segSubIndex, fileName,
   fileSize, fileType) → cached `PlaybackMediaFile`
   (`MediaFileExtensionKt.c`, classes_08fe100c.dex).
3. SDK-layer delete: `uav.media.FileOperateHelper` (classes_03a5700c.dex) →
   `JNIFile.batchAction(FileActionRequest{ DELETE_SINGLE, FilePackage(files) })` — also JNI.

So: **delete's wire bytes are native-only in v1.21.4.** Any Java-derived 0x28 payload layout
(`count u16 + index list`, etc.) in `media.py` is UNVERIFIED for this app version; treat as
**HYPOTHESIS; requires wire capture** (or static analysis of `libcrossplayback.so`).

Legacy vestige worth knowing: `DataSpecialControl` (deprecated) has multi-select/delete
playback enums (`MulDelValue`, `PlayBrowseType.DELETE`) — old playback-GUI remote-control,
not the current delete path. `DataOldSpecialControl` equally deprecated.

---

## 6. Which pipeline does WM160 actually speak? (status of HYPOTHESES)

**CONFIRMED from DEX:**

- Playback entry: `DataCameraSetMode` (classes_0451d00c.dex): CmdSet `CAMERA(2)` (value 0x02
  confirmed in `CmdSet.java`: `CAMERA(2, ...)`), CmdId `SetMode = 16 = 0x10`, payload
  `{ (byte) mode }`. The mode enum in use is `uav.sdk.keyvalue.value.camera.CameraWorkMode`
  (classes_0451d00c.dex) which defines **`PLAYBACK(2)` and `MEDIA_DOWNLOAD(3)`** — value 3 is
  the media-download entry. The referenced `DataCameraGetMode.MODE` class itself is absent
  from the dex listing (stripped), but its values are pinned by `CameraWorkMode` and by the
  legacy DataCameraGetMode lineage: 2=PLAYBACK, 3=MEDIA_DOWNLOAD. Entry command bytes:
  `cmdset=0x02, cmd=0x10, payload=0x03` (3 for media download; 2 for plain playback).
- Legacy handshake HYPOTHESIS (0x22/0x24 push): `DataCameraRequestSendFiles` (CmdId 0x22)
  has payload = 1 byte `FILE_SELECT_MODE` (CURRENT=0 / NEXT=1), and its error table includes
  `FileNotFound = 0x22 (34)` and `INVALID_CMD = 0xE0`. The class exists — the hypothesis that
  the drone pushes a list after 0x22 is structurally supported, **but WM160 answering 0x22
  with 0xE0 (INVALID_CMD) is consistent with this being legacy/dead for WM160.** HYPOTHESIS,
  capture-pending.
- `FileRecvPack` consumers: only ~6 litchis classes reference it; no modern uav.media /
  crossplayback code touches `FileSendPack`/`FileRecvPack`. Strong sign the legacy 0x26
  file-transport is **frozen/disused code in v1.21.4**, kept for old aircraft. For WM160 the
  active transport is whatever the native crossplayback sdk emits — **capture-pending**.
- Media defaults at SDK layer (`uav.media.UAVMediaManager`/`MediaFileListTask`,
  classes_03a5700c.dex): storage = `CameraStorageSlot.UAV_CAMERA_STORAGE_ID_EXTERN1`,
  request type `MediaRequestType.ORIGIN`, `index=1`, `count=-1`, `isAllList=TRUE`.

NOT FOUND (negative results, worth recording):

- No `CmdId 0x20` media command at CmdSet COMMON or CAMERA in v1.21.4 litchis/camera enums —
  `media.py`'s "0x20" assumption has **no DEX basis**.
- No Java implementation of 0x28 / 0x24 / 0x27 payload packing anywhere (see §5).
- No "special control" `cmdset=0x01,cmd=0x01` packet: `CmdSet(1)` is not a media set
  (0=COMMON, 2=CAMERA, 6=RC...). The `DataSpecialControl` class is `@Deprecated` flight-ctrl/
  playback-UI control, unrelated.

---

## 7. media.py — confirm/keep vs fix table

| media.py behavior | Verdict |
|---|---|
| File header `[0]=10\|ver<<6`, `[1]=cmdId<<5\|cmdType`, `[2:4] len u16`, `[4:6] sessionId`, `[6:10] offset u32` | **CONFIRMED** (FileSendPack `b()`); recv side uses `>>12` / `&0xFFF` split — mirror that |
| CmdId/CmdType/SubType enum values | **CONFIRMED** (litchis DataConfig) |
| LIST inner: u32 startIndex (+storage<<6 in byte3), u16 count, u8 subType (7 B) | **CONFIRMED** (DataRequestList) |
| DOWNLOAD inner: u32 index, u16 subIndex, u8 subType, u8 grade, u32 offset, u32 length (16 B) | **CONFIRMED** (DataRequestFile) |
| ACK layout (u32 + u8 count + pairs), ABORT (u32 reason, cmdType=4) | **CONFIRMED** (DataRequestAck/Abort) |
| CmdSet=COMMON(0), CmdId=0x26 RequestFile, APP→CAMERA | **CONFIRMED** (DataAppRequest) |
| Playback entry cmdset=0x02 cmd=0x10 payload=0x03 (MEDIA_DOWNLOAD) | **CONFIRMED** (DataCameraSetMode + CameraWorkMode enum; DataCameraGetMode class stripped but values pinned) |
| Legacy 0x22 request → 0x24 list push | **HYPOTHESIS** (classes exist; WM160 NAK with 0xE0 INVALID_CMD is consistent with legacy-only) |
| DELETE on cmd 0x00 with 0x28-style payload / any 0x28 layout (count u16 + indices) | **UNVERIFIED — native-only in v1.21.4; capture against WM160 required** |
| List record = `nailInfo` with 4×u64 (thumbSize, screenSize, thumbOffset, screenOffset); fileSize u64 after name | **CONFIRMED at dataset level** (MediaFile/PhotoAndVideoNailInfo fromBytes); wire framing native |
| COUNT as standalone cmd | **NOT IN DEX** — use list paging (`isPageLastFile`) |
| Cmd 0x20 | **NOT FOUND** — no DEX basis |

---

## 8. Capture-pending checklist (what only a wire capture against WM160 can settle)

1. Native list request/response framing produced by crossplayback (`fetchMediaFiles*` /
   `JNIFile.transferList`) — how the MediaFile record stream is chunked/framed on the wire.
2. DELETE bytes emitted by `PlayBackManagerForAndroid.deleteFiles` / `JNIFile.batchAction`
   (whether it is CmdIdCommon 0x28 at all, and its payload).
3. Whether WM160 accepts the legacy 0x26 RequestFile path at all (FileSendPack framing live).
4. Byte-level confirmation that the on-wire ordering of MediaFile fields equals
   `MediaFile.fromBytes` (strong static evidence, but the producer is native).

---

## Appendix A — Evidence index (class → dex → what it proves)

- `uav.midware.data.packages.P3.FileSendPack` / `FileRecvPack` — classes_0451d00c.dex — §1 header
- `uav.midware.data.config.litchis.DataConfig` — classes_016b200c.dex — §1 enums
- `uav.midware.data.config.P3.CmdSet` (COMMON=0 @line 221, CAMERA=2 @517; CmdIdCommon
  0x22/0x23/0x24/0x25/0x26/0x27/0x28; CmdIdCamera SetMode=0x10, GetMode=0x11) — classes_016b200c.dex
- `uav.midware.data.model.P3.DataAppRequest` — classes_0451d00c.dex — DUML slot 0x26 APP→CAMERA
- `uav.midware.data.model.litchis.DataRequestList` / `DataRequestFile` / `DataRequestAck` /
  `DataRequestAbort` — classes_0451d00c.dex — §2/§3 payloads
- `uav.midware.data.model.P3.DataCameraRequestSendFiles` — classes_0451d00c.dex — 0x22 legacy,
  errors FileNotFound=0x22 / INVALID_CMD=0xE0
- `uav.midware.data.model.P3.DataCameraSetMode` — classes_0451d00c.dex — `{0x02,0x10,0x03}` entry
- `uav.sdk.keyvalue.value.camera.CameraWorkMode` — classes_0451d00c.dex — PLAYBACK=2, MEDIA_DOWNLOAD=3
- `uav.sdk.keyvalue.value.media.MediaFile` (+ DateTime, FileExifInfo, ProxyInfo, DCFInfo,
  VideoBeautifySettingsInfo, PanoPicViewInfo, LivePhotoInfo, CameraColorLutVersion,
  LutVersion, PhysicalPathInfo) — classes_0451d00c.dex — §4 record layout
- `uav.sdk.keyvalue.value.file.PhotoAndVideoNailInfo` — classes_0451d00c.dex — nailInfo 4×u64
- `uav.sdk.keyvalue.value.common.ByteStreamHelper` — F/L/g/w/R widths — classes_0451d00c.dex
- `uav.midware.data.model.P3.DataSpecialControl` / `DataOldSpecialControl` — classes_0451d00c.dex
  — deprecated, playback-UI control enums only
- `com.uav.flymodel.handwrite.cameradevice.playback.v1.V1PlaybackManageFileKt*` —
  classes_08fe100c.dex — UI delete → crossplayback
- `com.uav.flymodel.handwrite.cameradevice.filelist.MediaFileExtensionKt` —
  classes_08fe100c.dex — MediaFile→PlaybackMediaFile keying, MediaFileLoaderKind map
- `com.uav.crossplayback.playback.PlayBackManagerForAndroid$CppProxy` — classes_0855200c.dex
  — ALL list/download/delete entry points are `native`
- `uav.media.FileOperateHelper`, `uav.media.UAVMediaManager`, `MediaFileListTask` —
  classes_03a5700c.dex — JNIFile.batchAction / transferList wrappers; defaults §6

Original C++ sources (PlayBackManagerForAndroid.jinji-era) live in libcrossplayback / libdjisdk
.so — **not present on disk**; its disassembly is the next step if capture is impossible.
