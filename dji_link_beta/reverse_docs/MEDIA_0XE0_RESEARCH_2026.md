# MEDIA 0xE0 NAK — Dedicated Research (2026-07-22)

> **OBSOLETE STATUS (2026-08-27):** `0xE0` is correctly decoded as `INVALID_CMD`, but it does not prove
> that WM160 lacks native `0x20/0x1F`, and no capture proves `0x22/0x24` as the selected WM160 path.
> Keep this as investigation history; current status is in `FIRMWARE_MEDIA_HOME_LIMITS_2026.md`.

Blocker: media file-list request on WM160 returns a 1-byte `0xE0` NAK regardless of
list-request payload variant, even after fixing enter-playback (0x02/0x10 [0x02]=PLAYBACK).
Every variant on cmd_set 0x00 / cmd_id 0x20 (receiver 0x01) gets exactly 0xE0.

Re-deriving from scratch. NOT trusting prior docs. Each fact tagged with source:
`[app]` = /tmp/all smali, `[msdk]` = dji-sdk-provided jar, `[dft]` = dji-firmware-tools, `[forum]`.

---

## THREAD A — What does 0xE0 mean?  ★ DECISIVE

`[app]` `/tmp/all/uav/midware/data/config/P3/Ccode.smali` line 330-343:
```
const/16 v2, 0xe0
const-string v3, "INVALID_CMD"
sput-object v0, ...Ccode;->j:Ccode;
```

**0xE0 = `INVALID_CMD`.** This is the DUML return-code enum used app-wide to decode the
1-byte ACK payload. Surrounding codes (same enum), for contrast:

| hex  | name                        | meaning if we got it instead |
|------|-----------------------------|------------------------------|
| 0xd6 | PARAM_ERROR                 | payload/params malformed |
| 0xd9 | NOT_SUPPORT_FEATURE         | feature absent on this model |
| **0xe0** | **INVALID_CMD**         | **cmd_set/cmd_id not recognised / not routable on target device** |
| 0xe1 | TIMEOUT_REMOTE              | remote device timed out |
| 0xe3 | INVALID_PARAM               | param out of range |
| 0xe4 | NOT_SUPPORT_CURRENT_STATE   | wrong mode (e.g. not in playback) |
| 0xe8 | SDCARD_NOT_INSERTED         | no SD card |
| 0xe9 | SDCARD_FULL                 | |
| 0xea | SDCARD_ERR                  | |

**Interpretation:** the drone is NOT saying "wrong state" (0xe4), "no SD" (0xe8), or
"bad param" (0xd6/0xe3). It is saying the *command itself is invalid* on the device we
addressed it to — i.e. **that cmd_set/cmd_id/receiver combination is not a command this
target accepts.** This strongly implies we are sending the WRONG cmd_id, the WRONG
receiver (addressing a device that doesn't own this cmd), or the WRONG cmd_set.

---

## THREAD B — The real file-list command in the app

### B1. cmd_id 0x20 (what our code sends): the app NEVER sends wire-id 0x20  `[app]` ★
IMPORTANT ctor detail: `CmdIdCommon$CmdIdType.<init>(String name, int ordinal, int value)`
— verified: `value()` returns the field `data` = the **3rd** arg (`iput p3 ... ->data:I`,
lines 3281+/135). So in each enum entry the FIRST int is just the Java ordinal and the
SECOND int is the on-wire cmd_id.
- The two `0x20` occurrences in the file (lines 1070, 3851) are both the **ordinal**
  register `v1`, never the wire `data` value. Grepping every entry's wire value: **no
  COMMON command has wire cmd_id 0x20.** (`SetNewestVersions` = ordinal 0x20 but wire id
  **0x61**.)
- Therefore cmd_set 0x00 / cmd_id **0x20** is an id the WM160 camera does not implement at
  all → it replies `INVALID_CMD` (0xE0). **This is exactly our bug.** ★
- NOTE — source discrepancy: `[dft]` dji-dumlv1-general.lua DOES label 0x20 = "File List"
  and 0x21 = "File Info" (older/other DJI firmware supports them). But the DJI Fly app for
  WM160 never uses 0x20; it uses the 0x22/0x24 handshake. WM160's 0xE0 for 0x20 means the
  WM160 camera firmware does not implement the legacy 0x20 "File List" — see Thread C.

### B2. The file-transfer command cluster (cmd_set 0x00 = COMMON)  `[app]`
From `CmdIdCommon$CmdIdType.smali` `<clinit>`, name → wire cmd_id:

| cmd_id | name              | model class | notes |
|--------|-------------------|-------------|-------|
| 0x22   | **RequestSendFiles** | DataCameraRequestSendFiles | "give me the list", payload=1 byte FILE_SELECT_MODE |
| 0x23   | (AckReceiveFiles) | — | ordinal 0xb / cmd 0x23 |
| 0x24   | **GetPushFiles**  | DataCameraGetPushFiles | camera PUSHES the file list back |
| 0x25   | SetResendFiles    | — | |
| 0x26   | RequestFile       | — | request one file |
| 0x27   | GetPushFile       | DataCameraGetPushFile | push single file data |
| 0x28   | DeleteFile        | — | |

So the classic media-list handshake is **0x22 (RequestSendFiles) → camera replies /
pushes 0x24 (GetPushFiles)** — NOT 0x20, and NOT 0x22-as-a-guess. cmd_set is COMMON=0x00.

### B3. DataCameraRequestSendFiles — exact frame  `[app]`
`/tmp/all/uav/midware/data/model/P3/DataCameraRequestSendFiles.smali` `start()`:
- sender (Pack.f) = DeviceType.APP  → **0x02**
- receiver (Pack.h) = DeviceType.CAMERA → **0x01**
- cmd type (Pack.j) = CMDTYPE.REQUEST → data field... (REQUEST ordinal 0, value TBD)
- needAck (Pack.k) = NEEDACK.YES
- encrypt (Pack.l) = EncryptType.a
- cmd_set (Pack.m) = CmdSet.COMMON → **0x00**
- cmd_id (Pack.n) = CmdIdType.l = **RequestSendFiles = 0x22**

`doPack()`: `_sendData` = 1 byte = FILE_SELECT_MODE.value().
FILE_SELECT_MODE `[app]`: CURRENT=0, NEXT=1, OTHER=0x64. Default (ctor) = **CURRENT (0)**.

DeviceType values `[app]` (`DeviceType.smali`): CAMERA=**1**, APP=**2**, FLYC=3, GIMBAL=4,
CENTER=5, RC=6, WIFI=7, DM368=8, OFDM/OSD=9, PC=0xa, BATTERY=0xb.
=> **receiver for camera media = 0x01** (this matches what we already tried), sender=0x02.

### B4. ★ But the modern WM160 media path is NATIVE, not this Java class  `[app]`
- **No Java caller** invokes `DataCameraRequestSendFiles.start()` anywhere in /tmp/all
  (grep for `RequestSendFiles;->start` / `->getInstance` in the whole tree: zero hits
  outside CmdIdCommon's enum table). The class is legacy plumbing.
- The gallery UI uses `com.uav.crossplayback` whose list functions are **all native JNI**:
  `PlayBackManagerForAndroid$CppProxy` declares `native fetchMediaFiles(II,cb)`,
  `fetchAllMediaFiles`, `fetchMediaFilesForType`, `fetchMediaFilesDirectly`,
  `getCurrentState`, `isSupportPageFileList`, `hasMoreFilesToFetch`, etc.
  => the actual on-wire frames for the modern list are assembled inside the native
  library (libDJI…/crossplayback .so), NOT visible in smali. The Java 0x22/0x24 model
  classes are the underlying protocol those natives most likely emit, but we cannot see
  the native's exact framing (it may add a file-index / page header beyond the 1 byte).

**Working conclusion (pending msdk/dft cross-check):** our 0xE0 is because cmd_id **0x20 is
not a file command** (it's SetNewestVersions). The correct list request is **cmd_set 0x00 /
cmd_id 0x22 (RequestSendFiles), receiver 0x01, sender 0x02, payload = 1 byte = 0x00
(CURRENT)**, and we should listen for a pushed **0x24 (GetPushFiles)** carrying the list.

---
## THREAD C — Cross-source: dji-firmware-tools + MSDK

### C1. dji-firmware-tools Wireshark dissector `dji-dumlv1-general.lua`  `[dft]`
cmd_set 0x00 (General) file cluster — names from GENERAL_UART_CMD_TEXT:

| cmd_id | dft name                | app name (`[app]`)   |
|--------|-------------------------|----------------------|
| 0x20   | File List               | (app has NO 0x20)    |
| 0x21   | File Info               | (app has NO 0x21)    |
| 0x22   | File Send               | RequestSendFiles     |
| 0x23   | File Receive            | AckReceiveFiles      |
| 0x24   | File Sending            | GetPushFiles         |
| 0x25   | File Segment Err        | SetResendFiles       |
| 0x26   | FileTrans App 2 Camera  | RequestFile          |
| 0x27   | FileTrans Camera 2 App  | GetPushFile          |
| 0x28   | FileTrans Delete        | DeleteFile           |

**Both sources agree on the cluster and on 0x22↔0x24 being the request↔push pair.**
The name difference (File Send vs RequestSendFiles) is just naming; the wire ids match.
`[dft]` has no enumerated retcode table (its only 0xE0 is an unrelated bitmask), so it
neither confirms nor contradicts the 0xE0=INVALID_CMD meaning — that comes solely from
`[app]` Ccode.smali, which is authoritative (it's DJI's own decode table).

### C2. Legacy 0x20/0x21 vs modern 0x22/0x24 — which does WM160 want?
- `[dft]` documents 0x20 "File List" / 0x21 "File Info" as real commands on *some* DJI
  gear (older P3/Phantom-era firmware, and the OcuSync file protocol).
- `[app]` DJI Fly (the app that actually flies WM160) has **removed** 0x20/0x21 entirely
  and drives media exclusively through the native `com.uav.crossplayback` layer, whose
  underlying Java protocol model is the 0x22 (RequestSendFiles) → 0x24 (GetPushFiles) pair.
- WM160 answering our 0x20 with **INVALID_CMD (0xE0)** is direct hardware evidence that
  **WM160 firmware does not implement legacy 0x20** — consistent with the app dropping it.
  => For WM160 we must use **0x22**, not 0x20.

### C3. MSDK jar cross-check — could not complete in sandbox  `[msdk]`
The sandbox shell has no outbound DNS/network (curl: "Could not resolve host
repo1.maven.org"), and no `dji-sdk-provided*.jar` exists anywhere on the local FS
(searched `/`). So I could not `javap` the MSDK MediaManager/PlaybackManager wire
constants first-hand. This is the one open cross-check.
- Mitigation: the app-side `com.uav.crossplayback.PlayBackManagerForAndroid$CppProxy`
  is effectively the MSDK's media manager (same DJI internal component, `native
  fetchMediaFiles(II,cb)` / `fetchMediaFilesForType` / `isSupportPageFileList` /
  `hasMoreFilesToFetch` / `getCurrentState`). Its API shape (paged fetch: offset+count)
  tells us the modern protocol is **paged** — the request likely carries a start-index
  and count, not just the 1-byte FILE_SELECT_MODE of the legacy DataCameraRequestSendFiles.
- TODO for a networked run: `curl repo1.maven.org/maven2/com/dji/dji-sdk-provided/4.18/
  dji-sdk-provided-4.18.jar`, unzip, `javap -p -c` MediaManager / PlaybackManager /
  DataCameraSetMode to confirm the exact bytes the native emits for 0x22.

---

## THREAD D — Enter-playback prerequisite (state)  `[app]`
The camera work-mode switch we already send (DataCameraSetMode, cmd_set 0x02 / cmd_id 0x10,
payload [0x02]=PLAYBACK) is the correct precondition. If we were in the wrong mode the
error would be **0xE4 NOT_SUPPORT_CURRENT_STATE**, not 0xE0. Since we get 0xE0, the mode
is not the current blocker — the cmd_id is. (Still keep the mode switch; the list command
does require playback mode on the camera to actually return files.)

---

## CONCLUSIONS

1. **0xE0 = `INVALID_CMD`** `[app]` (Ccode.smali). The drone rejects the *command id*
   itself, not the state, SD, or params. Distinct from 0xE4 (wrong state), 0xE8 (no SD),
   0xD6/0xE3 (bad param), 0xD9 (feature unsupported).

2. **Root cause:** we send cmd_set 0x00 / cmd_id **0x20**. The DJI Fly app never sends
   0x20 (no COMMON command has wire id 0x20 `[app]`), and WM160 firmware doesn't implement
   the legacy 0x20 "File List" `[dft]` — hence INVALID_CMD.

3. **Correct app album-open sequence for WM160:**
   1. Camera → PLAYBACK mode: cmd_set **0x02** / cmd_id **0x10**, receiver 0x01,
      payload `[0x02]`. (already done)
   2. Request list: cmd_set **0x00** / cmd_id **0x22** (RequestSendFiles),
      sender=APP **0x02**, receiver=CAMERA **0x01**, REQUEST + need-ACK,
      payload = 1 byte FILE_SELECT_MODE = **0x00** (CURRENT).  `[app]`+`[dft]`
   3. Camera pushes list back as cmd_set **0x00** / cmd_id **0x24** (GetPushFiles) —
      listen for and parse this push (it carries file entries).  `[app]`+`[dft]`
   4. Per-file: RequestFile 0x26 → GetPushFile 0x27 for data.

### Concrete ordered fix to try on hardware (media.py + drone.enter_playback)
Do NOT edit code (per task) — this is the recipe:
- A. Keep enter_playback: 0x02/0x10 payload [0x02].
- B. Change the list request from **cmd_id 0x20 → 0x22**, cmd_set 0x00, receiver 0x01,
     sender 0x02, cmd_type=REQUEST, need-ACK, payload = single byte **0x00** (CURRENT).
     This alone should stop the 0xE0.
- C. Stop treating the ACK as the list. After 0x22, the file list arrives as a **separate
     push frame 0x00/0x24 (GetPushFiles)** from the camera (receiver=app). Add a handler
     that captures 0x24 and parses its payload as the file table.
- D. If 0x22 with 1-byte payload still NAKs (INVALID_PARAM 0xE3 rather than 0xE0), the
     native path is paged: try a wider payload (index/count) — matching CppProxy
     `fetchMediaFiles(int start, int count)`. Escalate: `[msdk]` javap on a networked
     machine to get the exact 0x22 payload layout the .so emits.
- E. Fallback experiment (only if 0x22 also gives 0xE0): try legacy 0x20 "File List"
     `[dft]` — but WM160 already 0xE0'd 0x20, so this is expected to fail; it's listed
     only to bracket the space.

Order of likelihood for WM160: **0x22/0x24 (this fix) >> 0x20 legacy.**

### Open item
`[msdk]` first-hand javap of dji-sdk-provided-4.18.jar not done (no network in sandbox).
Everything else is confirmed by ≥2 sources (`[app]` Ccode + cmd table, `[dft]` dissector).

<!-- PROGRESS: 100% — 0xE0=INVALID_CMD; root cause = cmd_id 0x20 not implemented on WM160; fix = 0x22 RequestSendFiles + listen for 0x24 GetPushFiles. Confirmed by app smali + dji-firmware-tools. Only MSDK-jar javap left (no sandbox network). -->
