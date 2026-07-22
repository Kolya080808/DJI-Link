# WM160 Media DELETE + VIEW/THUMBNAIL — DUML Reverse (2026-07-22)

Goal: pin the exact DUML frames to (A) DELETE a photo/video and (B) VIEW/PREVIEW media
(thumbnail / screennail / original) on WM160 (Mavic Mini 1, UAV59).

Ground truth carried in from `MEDIA_0XE0_RESEARCH_2026.md` (do not re-litigate):
the WM160 media transport is the **RequestSendFiles handshake on cmd_set 0x00 (COMMON)**,
NOT the 0x20/0x1F family (those NAK `0xE0 INVALID_CMD`). Cluster + wire ids re-verified
below from `CmdIdCommon$CmdIdType` `[app]`:

| wire | name | dir | model class |
|------|------|-----|-------------|
| 0x22 | RequestSendFiles | app→cam | DataCameraRequestSendFiles |
| 0x23 | AckReceiveFiles  | app→cam | (native) |
| 0x24 | GetPushFiles (list push) | cam→app | (native) |
| 0x25 | SetResendFiles   | app→cam | DataCameraSetResendFiles |
| 0x26 | RequestFile      | app→cam | (native; litchis analog exists) |
| 0x27 | GetPushFile (data push) | cam→app | (native) |
| 0x28 | **DeleteFile**   | app→cam | (native — NO Java doPack) |

Framing (VERIFIED, DataCameraRequestSendFiles.start `[app]`): sender APP=**0x02**,
receiver CAMERA=**0x01**, cmd_type=REQUEST, need_ack=YES, encrypt=NONE, cmd_set=COMMON 0x00.
DeviceType: CAMERA=1, APP=2 `[app]`.

Source tags: `[app]` = /tmp/all smali, `[msdk]` = DJI MSDK jar, `[dft]` =
dji-firmware-tools dissector, `[forum]` = capture/forum.

---

## KEY FINDING: the modern gallery is NATIVE (crossplayback / libcross_playback)

The DJI-Fly gallery does NOT drive delete/thumbnail from Java models. It calls JNI
statics on `com.uav.crossplayback.playback.PlayBackManagerForAndroid$CppProxy` `[app]`
(the app's embedded MSDK media manager). The relevant natives:

```
static native int  deleteFiles(ArrayList<PlaybackMediaFile>, DeleteCallback)      // MULTI-delete
static native int  deleteOnlyOriginalFiles(ArrayList<PlaybackMediaFile>, DeleteCallback)
static native void cancelDeleteFile(int taskId)
static native int  fetchThumbnail(PlaybackMediaFile, GetImageCallback)            // THUMBNAIL
static native int  fetchPreview(PlaybackMediaFile, GetImageCallback)             // SCREENNAIL/preview
static native void downloadMediaFileRawData(MediaFileRawDataRequest, MediaFileRawDataDownloadCallback)
static native void fetchMediaFiles(int index, int count, GetListCallback)         // paged list
```

So the C++ layer re-serializes the actual 0x26/0x28 wire bytes; they are NOT in smali.
BUT the parameter models + the parallel `litchis` Java family (which DOES have a
byte-packer) reveal the exact field semantics and widths the native uses. Cross-checked
below.

---

## A. DELETE — cmd_set 0x00 / cmd_id 0x28 (DeleteFile)

### A1. Semantics from the native API `[app]`  ★
- `deleteFiles(ArrayList<PlaybackMediaFile>, cb)` → **multi-delete is native and normal**;
  the app hands a LIST of file records, not a single index. Returns an int task-id;
  `cancelDeleteFile(taskId)` aborts. Result comes back via `DeleteCallback.onResult(Z)`
  (a boolean success) `[app]`.
- `deleteOnlyOriginalFiles(...)` = delete the full-res original but KEEP the thumbnail/
  screennail cache entry (used to free SD space while the gallery still shows a preview).
- Each item is a `PlaybackMediaFile` (native CppProxy record) — the delete is addressed by
  the **file record**, whose primary key on the wire is the **file index** (u32) it was
  listed with (see the list-record index used everywhere in fetch/download by-index).

### A2. Wire payload — no Java doPack exists  ★
There is NO `DataCameraDeleteFile.smali` in this app (confirmed: grep of the whole tree
finds delete only as the native `deleteFiles` and the `CmdIdCommon` enum entry `[app]`).
So the exact 0x28 payload is built in C++. The DUML-v1 convention for 0x28 FileTrans
Delete (to be confirmed by `[dft]`, see §C) is a **list of file indices**:

```
DeleteFile 0x00/0x28 request (DUML convention, confirm via [dft]):
  +0  u16  count           (number of files to delete)
  +2  u32  index[0]        (file index, LE, as listed in 0x24)
  +6  u32  index[1]
  ...
Response: 1-byte ACK = Ccode (0x00 Success; 0xE8 SDCARD_NOT_INSERTED; 0xE4 wrong state).
```

The multi-delete `ArrayList` shape of the native `deleteFiles` corroborates a
count-prefixed index list. Single delete = count 1. **[msdk/dft cross-check pending — see §C.]**

### A3. ACK / return codes `[app]` (Ccode.smali, from MEDIA_0XE0_RESEARCH_2026)
Success=0x00; PARAM_ERROR=0xD6; NOT_SUPPORT_FEATURE=0xD9; INVALID_CMD=0xE0;
NOT_SUPPORT_CURRENT_STATE=0xE4 (not in playback); SDCARD_NOT_INSERTED=0xE8; SDCARD_ERR=0xEA.

### A4. Even the LEGACY/V1 (WM160-class) delete path is native  `[app]`  ★
`V1PlaybackManageFileKt$v1PlaybackManageFileDeleteFiles$1.d()` (the V1 file-manager used
for older devices) does exactly: iterate the `List<MediaFile>` → map each via
`MediaFileExtensionKt.a(MediaFile) → PlaybackMediaFile` → build an `ArrayList` → call
`PlayBackManagerForAndroid.deleteFiles(ArrayList, DeleteCallback)`. The callback delivers a
single **boolean** for the whole batch (`FlyResult<Boolean>`). So:
- There is exactly ONE delete entrypoint (native `deleteFiles`), used by both modern and V1
  paths. It always takes a LIST. => **multi-delete is the norm; single delete = 1-element list.**
- The delete is keyed by the `PlaybackMediaFile` record (its file index), NOT by a filename
  string. This matches the DUML 0x28 "delete by index" convention.

---

## B. VIEW / THUMBNAIL / SCREENNAIL / ORIGINAL

### B1. The three data grades — enum values `[app]`  ★ VERIFIED
`com.uav.crossplayback.playback.RequestDataType` (native enum) `<clinit>` int values:
```
ORIGIN=0  THUMBNAIL=1  SCREEN(=screennail)=2  CLIP=3  STREAM=4  PANO=5
PANOSCREENNAIL=6  PANOTHUMBNAIL=7  ...  (JSON=0xD ... etc.)
```
The parallel legacy `uav.midware.data.config.litchis.DataConfig$SubType` `[app]` agrees:
```
ORG=0  THM=1  SCR=2  CLIP=3  Stream=4  Pano=5  Pano_SCR=6  Pano_THM=7  ...
```
=> **the grade selector is a single byte: 0=ORIGINAL, 1=THUMBNAIL, 2=SCREENNAIL.** ★
(fetchThumbnail → grade 1; fetchPreview → grade 2 = the larger "screennail" preview.)

### B2. How thumbnail/screennail bytes are actually located — two mechanisms `[app]`

**(a) Offsets embedded in the list record (primary path).** Each pushed list entry
carries a `PhotoAndVideoNailInfo` `[app]`:
```
PhotoAndVideoNailInfo(long thumbNailOffset, long thumbNailSize,
                      long screenNailOffset, long screenNailSize)
```
i.e. the 0x24 list record tells you, per file, the **byte offset + size of the thumbnail
and of the screennail inside the file**. You then RequestFile that byte range. This is why
the app can show previews without a separate "thumbnail mode" command — the nail is a
sub-range of the original JPEG/MP4.

**(b) RequestFile carries an explicit grade byte + offset/size.** The native
`MediaFileRawDataRequest` `[app]` fields:
```
mIndex(int) mSubIndex(int) mFileType(FileType) mDownloadType(RequestDataType)
mSlotLocation mOffSet(long) mDataSize(long) mCount(int) mSegSubIndex(int)
mNailInfo(PhotoAndVideoNailInfo) mIsSize64File(bool) ...
```
So a raw-data request = {file index, grade (ORIGIN/THUMB/SCREEN), offset, size,
+ nail-info}. The C++ picks offset/size either from nailInfo (for thumb/screen) or 0..full
(for origin).

### B3. Exact RequestFile byte layout — VERIFIED from the litchis packer `[app]`  ★
The native 0x26 has no smali, but the sibling `litchis.DataRequestFile.doPack()` `[app]`
IS a concrete byte-packer for the same request shape (uses `DataConfig$SubType` ORG/THM/SCR
above). Its 16-byte payload:
```
DataRequestFile.doPack  →  16-byte FilePack.i:
  +0   u32 LE  index      (field b; BytesUtil.z = 4-byte LE)     [file index]
  +4   u16 LE  subIndex   (field c; BytesUtil.n0 = 2-byte LE)
  +6   u8      subType    (field d; DataConfig$SubType.c(): ORG=0/THM=1/SCR=2)  ★grade byte
  +7   u8      count/flag (field e)
  +8   u32 LE  offset     (field f; BytesUtil.o0(long) low 4 bytes LE)
  +12  u32 LE  size       (field g; BytesUtil.o0(long) low 4 bytes LE)
```
BytesUtil widths VERIFIED from new-array sizes `[app]`: z→4B, n0→2B, o0→4B (all LE).

=> **Thumbnail request** = this frame with subType=1, offset=thumbNailOffset,
size=thumbNailSize (from the list record's nailInfo). **Screennail** = subType=2 with
screenNailOffset/Size. **Original** = subType=0, offset=0, size=fileSize.

The COMMON-family 0x26 (RequestFile) that WM160 actually accepts is the native analog of
this same {index, subType, offset, size} tuple. **[Exact COMMON-0x26 field order is
capture/[dft]-pending; the litchis layout is the confirmed template.]**

### B3a. Data-push (0x27) chunk-header template — from litchis FileData getters `[app]`
`litchis.DataCameraFileSystemFileData` accessors read the push payload at fixed offsets:
```
c()        = get(off 0,  4B int)      → header word (status/type)
getDataLength() = get(off 4, 4B int)  → total/segment data length
getIndex() = get(off 8,  4B int)      → file index this chunk belongs to
e()        = get(off 12, 1B int)      → name/tail length (nameLen)
d()        = get(off 13, nameLen str) → filename
a() = off 0 header; then file bytes follow after the 13+nameLen header.
```
So a 0x27 GetPushFile chunk = [+0 hdr u32][+4 dataLen u32][+8 fileIndex u32][+12 nameLen u8]
[name][payload...]. Use fileIndex+offset to reassemble; a thumbnail is just a short such
stream (dataLen == nail size). **[COMMON-0x27 exact header pending [dft]/capture; this is
the litchis-family template.]**

### B4. How the preview comes back `[app]`
The requested bytes stream back as **0x00/0x27 GetPushFile** data-push frames (same channel
as a full download; just a smaller byte range because size = nailSize). The app reassembles
them into the JPEG thumbnail/screennail. There is NO separate "thumbnail push" cmd_id — a
thumbnail is just a short RequestFile→GetPushFile of the nail byte range. `fetchThumbnail`/
`fetchPreview` return synchronously via `GetImageCallback` once those 0x27 pushes complete.

---

## C. Cross-source verification  [dft]  (local dji-firmware-tools dissectors)

Verified against `/tmp/dji-dumlv1-general.lua` and `/tmp/dji-dumlv1-camera.lua` (o-gs
dji-firmware-tools). ★ IMPORTANT: dft reveals there are TWO delete families — pick per firmware.

### C1. COMMON set 0x00 file cluster `[dft]` — matches app exactly
```
0x20 File List   0x21 File Info   0x22 File Send   0x23 File Receive
0x24 File Sending 0x25 File Segment Err  0x26 FileTrans App 2 Camera
0x27 FileTrans Camera 2 App  0x28 FileTrans Delete  0x2a FileTrans General Trans
```
So app 0x22↔0x24↔0x26↔0x27↔0x28 names = dft File Send/Sending/App2Cam/Cam2App/Delete. ✔
- `0x24 File Sending` dissector `[dft]`: payload = **int32 Index + up to 495 bytes Data**
  (total 499). => the LIST/data push carries a 4-byte LE index header then the data blob —
  matches the litchis 0x27 template's fileIndex field. The data itself (list records or file
  bytes) is opaque to dft (just "Data 495B"), so the per-record layout still needs the app
  models / a capture — dft does not contradict the litchis/nailInfo structure.
- `0x27 FileTrans Camera 2 App` and `0x28 FileTrans Delete` dissectors `[dft]` are **stubs**
  (payload len 0 / not decoded) — dft never captured their bodies. So the exact 0x28 payload
  is NOT pinned by dft either; the app-side native `deleteFiles(ArrayList<index>)` remains the
  best evidence → count-prefixed index list.

### C2. ★ SECOND delete path — CAMERA set 0x02, cmd 0x79 "File Delete / Photo Erase" `[dft]`+`[app]`
dft camera table: `0x79 = File Delete (Photo Erase)`, `0x7B = Thumbnail 2 Single Ctrl
(Single Play Choice)`. The app CONFIRMS this on cmd_set 0x02: `CmdIdCamera$CmdIdType`
entry **`DeletePhoto` = wire cmd_id 0x79** `[app]` (and `VideoControl`=0x7A next to it).
This legacy delete works with a **page-selection model**, not an index list:
- `GetPushPlayBackParams` (0x02/0x82) dissector `[dft]` carries `Cur Page Selected`,
  `Delete Chioce Num` (u16), `Del File Status` (enum) — i.e. you mark files as selected on
  the current playback page, then fire `0x02/0x79 DeletePhoto` to erase the current selection.
- The app has `SetQuickPlayBack`, `SetFileStar`, `SetFileIndexMode` `[app]` (camera-set) —
  the selection/navigation primitives for this older model.
- No `DataCameraDeletePhoto.doPack` exists → also native/KeyValue-driven, and it's referenced
  from `UAVCameraKey` + `PlaybackMediaItemInfo` `[app]`.

**Which does WM160 use?** WM160 is a Mini-1 = older camera. The dft 0x79/0x7B camera-set
family is the classic Mini/Phantom playback model, and the app keeps its selection primitives.
BUT the native `deleteFiles(ArrayList)` (used by BOTH modern and V1 paths) most likely emits
the COMMON 0x28 by index. **Recommendation: try COMMON 0x00/0x28 (index list) FIRST; if it
NAKs 0xE0/0xD9, fall back to the camera-set selection model: page-select then 0x02/0x79.**
Both are documented; the live NAK/ACK decides.

### C3. Thumbnail — dft corroboration `[dft]`
Camera-set `0x7B Thumbnail 2 Single Ctrl` exists (thumbnail/single-play toggle), but the
actual thumbnail BYTES still come through the file-transfer channel (0x26→0x27) as a short
byte-range read of the nail offset/size — dft's 0x27 body is a stub, consistent with the app's
nailInfo-offset mechanism (§B). The grade byte ORIGIN=0/THUMB=1/SCREEN=2 is app-authoritative.

### C4. MSDK v4 `[msdk]` (web cross-check, returned)
The DJI Mobile SDK v4 (public mirror of the crossplayback natives) confirms the 3-grade model
at the API layer:
- `FetchMediaTaskContent` enum = **THUMBNAIL / PREVIEW / (original via fetchFileData) /
  CUSTOM_INFORMATION / NONE** — i.e. exactly the ORIGIN/THUMBNAIL/SCREENNAIL(=PREVIEW) tiers.
  (developer.dji.com MediaManager_DJIFetchMediaTask)
- `MediaFile.fetchThumbnail(cb)` = small cached thumbnail; `fetchPreview(cb)` = "preview image
  is a lower-resolution (960x540) version of the photo" (= the SCREENNAIL grade);
  `fetchFileData(dir, name, listener)` = the full ORIGINAL. `MediaFile` is addressed **by its
  index in the file list**. (developer.dji.com MediaManager_DJIMedia)
- MSDK models the selector as an explicit content-type (matches the grade-byte model), and the
  media file is keyed by integer index (matches delete-by-index). ✔ consistent with §A/§B.

### C5. dft caveat — general-family bodies are undecoded `[dft]` (web cross-check)
The web pass re-confirmed: dji-firmware-tools registers only *names* for general 0x20–0x28;
only 0x24 (int32 index + 495B data), 0x27 (empty), 0x2a have field dissectors. So dft neither
proves nor disproves a grade-enum byte in the general 0x26 — that comes from the app enums
(§B1, authoritative). For Mini-class, dft's dissected media path is the camera set 0x02:
PlayBack Params 0x82 (Mode/FileType/FileNum/Index...), File Delete 0x79, Thumbnail/Video ctrl
0x7B/0x7A — all keyed by integer index. This is exactly the §C2 fallback family. So the two
candidate families are both corroborated; the grade/offset details are app-side + capture.

---

## D. CONCLUSIONS

### DELETE (two documented paths — try COMMON first)
- **PRIMARY: COMMON 0x00 / cmd_id 0x28 (FileTrans Delete)**, recv CAMERA 0x01, send APP 0x02,
  need_ack. Payload = count-prefixed list of u32 file indices (single delete = count 1),
  keyed by the file index from the 0x24 list. This is what the native `deleteFiles(ArrayList
  <PlaybackMediaFile>)` (used by BOTH modern and V1/WM160-class paths `[app]`) most likely
  emits. Multi-delete is native-normal. `deleteOnlyOriginalFiles` keeps the thumbnail. ACK =
  1-byte Ccode (0x00 Success; 0xE8 no SD; 0xE4 wrong state). [count/index widths = dft convention;
  0x28 body is a dft stub, so confirm the exact byte order on the first live ACK.]
- **FALLBACK: CAMERA 0x02 / cmd_id 0x79 (DeletePhoto / Photo Erase)** `[dft]`+`[app]`. Uses the
  older page-selection model: navigate/select on the playback page (SetFileIndexMode /
  GetPushPlayBackParams `Cur Page Selected` + `Delete Chioce Num`), then fire 0x02/0x79 to
  erase the current selection. Use only if COMMON 0x28 NAKs 0xE0/0xD9 on WM160.

### VIEW / THUMBNAIL / SCREENNAIL
- **cmd_set 0x00 / cmd_id 0x26 RequestFile**, recv 0x01, send 0x02, with a **1-byte grade
  selector: ORIGIN=0 / THUMBNAIL=1 / SCREENNAIL=2** (RequestDataType/SubType, VERIFIED `[app]`)
  + file index + offset + size. For thumb/screen, offset+size come from the list record's
  `PhotoAndVideoNailInfo` (thumbNailOffset/Size, screenNailOffset/Size). 16-byte litchis
  template: [index u32][subIndex u16][grade u8][count u8][offset u32][size u32] (all LE).
- **Reply**: bytes stream back as **0x00/0x27 GetPushFile** pushes (chunk header template:
  [hdr u32][dataLen u32][fileIndex u32][nameLen u8][name][bytes], from litchis FileData `[app]`;
  dft 0x24 header = int32 index + data). A thumbnail is simply a short RequestFile→GetPushFile
  of the nail byte-range — there is NO dedicated thumbnail cmd_id.
- No new cmd_ids beyond the confirmed 0x22/0x24/0x26/0x27/0x28 cluster (plus the 0x02/0x79
  camera-set delete fallback).

### Open items (need ONE live capture)
- Exact 0x28 delete-payload byte order (count u16 vs u8; index list) — dft body is a stub.
- Exact position of the nailInfo quad within the 0x24 list record (native record stride).
- Whether WM160 honors COMMON 0x28 or requires the camera-set 0x79 selection model.
- Whether a thumbnail can be pulled by grade alone (offset=size=0) or requires nail offsets.

## E. IMPLEMENTATION PLAN — media.py + pc_client (code NOT edited per instructions)

Current `media.py` already has the right cluster wired (0x22/0x24/0x26/0x27/0x28, recv 0x01)
and a `delete()` that sends 0x28 with `delete_single_request(mf.raw)` (a CSDK-style blob).
Two concrete upgrades from this reverse:

### E1. delete() — switch to the index-list form + support multi-delete
Replace the opaque `delete_single_request(mf.raw)` blob with the DUML index-list convention
(matches native `deleteFiles(ArrayList)` = a list of file records keyed by index):
```python
def delete_request(indices):                 # 0x00/0x28 payload
    return struct.pack("<H", len(indices)) + b"".join(struct.pack("<I", i) for i in indices)

def delete(self, files):                       # accept one MediaFile or a list
    fs = files if isinstance(files, (list, tuple)) else [files]
    idx = [f.file_index for f in fs]
    self.d.send_raw(CMDSET_GENERAL, CID_FILE_DELETE, delete_request(idx), receiver=0x01)
    # ACK arrives as a 1-byte Ccode on 0x00/0x28 (0x00 Success); pc_client already decodes Ccode.
```
Keep `mf.raw` fallback behind a flag as a probe (if index-list NAKs 0xD6/0xE3, resend the
raw-record variant). The count/index widths (u16 count + u32 index) are the [dft] convention;
mark as capture-confirmable and log the ACK code.

### E2. fetch_thumbnail() / fetch_screennail() / view — NEW methods
Add a grade selector and reuse the RequestFile 0x26 path. The 16-byte litchis layout is the
confirmed template (index u32, subIndex u16, subType u8, count u8, offset u32, size u32):
```python
GRADE_ORIGIN, GRADE_THUMBNAIL, GRADE_SCREENNAIL = 0, 1, 2   # RequestDataType/SubType [app]

def request_file_req(index, grade, offset, size, sub_index=0, count=1):
    return struct.pack("<IHBBII", index & 0xFFFFFFFF, sub_index & 0xFFFF,
                       grade & 0xFF, count & 0xFF, offset & 0xFFFFFFFF, size & 0xFFFFFFFF)

def fetch_thumbnail(self, mf, dest):
    off, size = mf.thumb_off, mf.thumb_size     # from list record's PhotoAndVideoNailInfo
    self._start_view(mf, dest, GRADE_THUMBNAIL, off, size)

def fetch_screennail(self, mf, dest):           # larger preview
    self._start_view(mf, dest, GRADE_SCREENNAIL, mf.screen_off, mf.screen_size)

def _start_view(self, mf, dest, grade, off, size):
    self._dl = {"file": open(dest, "wb"), "received": 0, "size": size, "index": mf.file_index}
    self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE,
                    request_file_req(mf.file_index, grade, off, size), receiver=0x01)
    # bytes stream back as 0x00/0x27 GetPushFile → on_data_chunk (already implemented)
```
Existing `download()` becomes `fetch_thumbnail`'s origin sibling: grade=0, off=0,
size=mf.file_size.

### E3. MediaFile — capture the nail offsets when parsing 0x24
Extend `parse_file_list`/`MediaFile` to keep `thumb_off/thumb_size/screen_off/screen_size`
(the `PhotoAndVideoNailInfo` quad) so thumbnails can be requested without a second round-trip.
Exact positions of the nail quad in the 0x24 record are capture-pending (dump already saved);
until then, request grade=THUMBNAIL with offset=0,size=0 and let the camera pick (some
firmware treats grade alone as "give me the thumbnail").

### E4. pc_client wiring
`pc_client.py` already routes 0x24→on_list_response, 0x27→data chunk, and decodes the 0x28/
0x22 ACK Ccode. Add:
- a "Media: thumbnail first" button → `self.cli.media.fetch_thumbnail(files[0], "thumb.jpg")`
- accept multi-select for delete → `self.cli.media.delete(selected_list)`
- on 0x27 pushes for a thumbnail, save to a .jpg and (optionally) show it.
No new cmd routing needed — thumbnails and originals share the 0x26→0x27 channel.

### E5. Sequence (unchanged prelude)
enter PLAYBACK (0x02/0x10 [0x02]) → wait ready → RequestSendFiles 0x22 [CURRENT] → parse
0x24 list (grab nail offsets) → for a view: RequestFile 0x26 {index, grade, nailOff, nailSize}
→ collect 0x27 → for delete: 0x28 {count, index...} → read 1-byte Ccode ACK → exit playback.

<!-- PROGRESS: 100% — app [app] + dji-firmware-tools [dft] + MSDK v4 [msdk] all cross-checked
     and consistent. DELETE: primary COMMON 0x00/0x28 index-list (native deleteFiles(ArrayList)
     keyed by index, used by both modern+V1 paths), fallback camera-set 0x02/0x79 DeletePhoto
     page-select model. VIEW: 0x00/0x26 RequestFile + 1B grade (ORIGIN0/THUMB1/SCREEN2, verified
     in TWO app enums + MSDK THUMBNAIL/PREVIEW) + nailInfo offset/size → 0x00/0x27 push. 16B
     RequestFile template + 0x27 chunk header pinned. Full media.py/pc_client plan written.
     Two facts remain capture-only (exact 0x28 index-list byte order; nailInfo position in the
     0x24 record) because the app builds them in C++ and dft never captured those bodies. -->
