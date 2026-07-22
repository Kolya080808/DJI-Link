"""
media.py — browse / download / delete photos and videos on the drone's SD card.

Rewritten from reverse_docs/MEDIA_LIST_DOWNLOAD_RESEARCH_2026.md (fresh reverse of THIS
WM160 app). What is VERIFIED statically:
  - The root cause of the old 0xE0/silence was the MODE bug: enter-playback must send
    0x02/0x10 [0x02]=PLAYBACK (drone.enter_playback now does). List/download only answer
    once the camera confirms PLAYBACK.
  - Command IDs on the general set 0x00 are correct: list=0x20, data=0x1F, delete=0x28.
  - Framing: sender APP=0x02, receiver CAMERA=0x01, cmd_set 0x00 is non-encrypted.
  - Readiness signal: DataCameraGetPushStateInfo.getMode() = byte[4] of the 0x02/0x80
    push; PLAYBACK == 2. A 0x02/0x82 GetPushPlayBackParams push also confirms media state.

What is CAPTURE-ONLY (the app builds these natively in C++, so the exact on-wire bytes
cannot be finalised offline — confirmed, not guessed-away):
  - the get_file_list_req (0x20) / get_file_data_req (0x1F) request field bytes,
  - the MediaFile list-record stride/layout,
  - the 0x1F data-push window header + selective-ACK mask format.
So every response is dumped to a file and the request uses a best-guess native struct
first (with the old CSDK envelope kept as a probe variant). The first hardware capture
via pc_client pins the exact bytes. A legacy 0x22/0x24/0x27 family fallback is provided
behind a flag in case the firmware ignores 0x20.
"""

from __future__ import annotations

import struct
import time

# MediaFileType (0x24 list-record fileType field): photo vs video
MFT_JPEG, MFT_DNG, MFT_MOV, MFT_MP4 = 0, 1, 2, 3

CMDSET_GENERAL = 0x00
# NOTE: cmd_ids 0x20 (get_file_list) / 0x1F (get_file_data) are NOT implemented on WM160 —
# the drone answers them with 0xE0 = INVALID_CMD (confirmed on hardware + app Ccode enum,
# MEDIA_0XE0_RESEARCH_2026.md). WM160 uses the RequestSendFiles handshake below instead.
CID_FILE_DELETE = 0x28      # DeleteFile
CID_REQ_SEND_FILES = 0x22   # RequestSendFiles: 1B {CURRENT=0, NEXT=1} → list pushed back as 0x24
CID_ACK_RECV_FILES = 0x23   # AckReceiveFiles: 1B {Success=0, UnableReceive=0x22}
CID_PUSH_FILES = 0x24       # GetPushFiles (list push, drone->app)
CID_SET_RESEND = 0x25       # SetResendFiles: u32 LE index
CID_REQUEST_FILE = 0x26     # RequestFile: ask for one file's bytes (data pushes back as 0x27)
CID_PUSH_FILE = 0x27        # GetPushFile (data push, drone->app)
SELECT_CURRENT, SELECT_NEXT = 0, 1
ACK_SUCCESS, ACK_ABORT = 0x00, 0x22

# Data grade for RequestFile (RequestDataType / litchis SubType — verified in two app enums
# + MSDK THUMBNAIL/PREVIEW; MEDIA_DELETE_VIEW_RESEARCH_2026.md §B1).
GRADE_ORIGIN, GRADE_THUMBNAIL, GRADE_SCREENNAIL = 0, 1, 2


def request_file_req(index: int, grade: int, offset: int, size: int,
                     sub_index: int = 0, count: int = 1) -> bytes:
    """RequestFile 0x00/0x26 payload — 16-byte layout confirmed from litchis.DataRequestFile
    .doPack (MEDIA_DELETE_VIEW_RESEARCH_2026.md §B3):
      [index u32][subIndex u16][grade u8][count u8][offset u32][size u32]  (all LE)
    grade: ORIGIN=0 (full file, off=0 size=fileSize) / THUMBNAIL=1 / SCREENNAIL=2 (larger
    preview) — for thumb/screen, off+size come from the list record's PhotoAndVideoNailInfo.
    The COMMON-0x26 the native emits is the analog of this tuple; exact field order is
    capture-confirmable (the reply is dumped)."""
    return struct.pack("<IHBBII", index & 0xFFFFFFFF, sub_index & 0xFFFF, grade & 0xFF,
                       count & 0xFF, offset & 0xFFFFFFFF, size & 0xFFFFFFFF)


def delete_request(indices) -> bytes:
    """DeleteFile 0x00/0x28 payload — count-prefixed list of u32 file indices (native
    deleteFiles(ArrayList) is keyed by index; multi-delete is normal). Single = one index.
    Widths are the dji-firmware-tools convention; confirm on the first live ACK."""
    idx = list(indices)
    return struct.pack("<H", len(idx)) + b"".join(struct.pack("<I", i & 0xFFFFFFFF) for i in idx)


# NOTE: the old ByteStreamHelper "CSDK envelope" encoders (file_list_request / file_data_request
# / delete_single_request) were removed — they were the MediaManager (newer-camera) format and
# never matched WM160's wire. The confirmed WM160 encoders are request_file_req / delete_request
# above (MEDIA_0XE0_RESEARCH_2026.md + MEDIA_DELETE_VIEW_RESEARCH_2026.md).


# ---------------------------------------------------------------- response parse (best-effort)
class MediaFile:
    __slots__ = ("file_index", "file_type", "file_name", "file_size", "duration_ms", "raw",
                 "thumb_off", "thumb_size", "screen_off", "screen_size")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def is_video(self):
        return self.file_type in (MFT_MOV, MFT_MP4)

    def __repr__(self):
        kind = "video" if self.is_video else "photo"
        return f"<{self.file_name} {kind} {self.file_size}B>"


class _R:
    """Sequential little-endian reader with bounds checks."""
    def __init__(self, b: bytes):
        self.b = b
        self.p = 0

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.p)[0]; self.p += 4; return v

    def i64(self):
        v = struct.unpack_from("<q", self.b, self.p)[0]; self.p += 8; return v

    def u8(self):
        v = self.b[self.p]; self.p += 1; return v

    def string(self):
        n = self.i32()
        if n < 0 or self.p + n > len(self.b):
            raise ValueError("bad string length")
        s = self.b[self.p:self.p + n].decode("utf-8", "replace"); self.p += n
        return s

    def datetime(self):
        return tuple(self.i32() for _ in range(6))     # Y M D h m s


def parse_file_list(payload: bytes):
    """Best-effort decode of a FileList response. Returns (files, ok, note).

    Only the leading, unambiguous MediaFile fields (through `duration`) are decoded, which
    is enough for a name/size/type/duration listing and for downloading by fileIndex. The
    trailing nested fields are native-width and are skipped for the next record, so if a
    record's leading fields don't line up we stop and report — the raw payload is dumped by
    the caller so the layout can be finalised from a real capture.
    """
    try:
        r = _R(payload)
        r.i32()                       # slotLocation
        r.i32()                       # FilePackage.type
        n = r.i32()                   # media list count
        if n < 0 or n > 100000:
            return [], False, f"implausible media count {n} — layout differs, see dump"
        files = []
        for _ in range(n):
            start = r.p
            valid = r.u8(); r.u8()    # valid, isManualGroupFile
            fi = r.i32()
            ft = r.i32()
            name = r.string()
            size = r.i64()
            r.datetime()              # capture time (24 B)
            r.i32()                   # starTag
            r.u8()                    # isCloudDownload
            dur = r.i64()             # duration
            files.append(MediaFile(file_index=fi, file_type=ft, file_name=name,
                                   file_size=size, duration_ms=dur, raw=payload[start:r.p]))
            # We cannot skip the remaining native-width fields reliably, so we stop after
            # the first record unless a real capture teaches us the record stride.
            break
        note = ("parsed 1 record; multi-file parsing needs a real capture "
                "(dump saved) to learn the record stride") if files else "no records"
        return files, bool(files), note
    except Exception as e:
        return [], False, f"parse failed ({e}); raw dump saved"


# ---------------------------------------------------------------- high-level client
class MediaClient:
    """Issues media requests through a Drone and collects the raw responses.

    Responses arrive asynchronously as DUML frames (cmd_set 0x00, id 0x20/0x1F) routed by
    the client. Feed them in with on_response(); downloads accumulate into a file.
    """
    def __init__(self, drone, receiver=0x01, dump_dir="."):
        self.d = drone
        self.receiver = receiver
        self.dump_dir = dump_dir
        self.files: list[MediaFile] = []
        self.last_note = ""
        self._dl = None               # (file, remaining, path)
        self._playback_ready = False  # set by note_camera_state / note_playback_params

    # --- readiness gate (VERIFIED signal; wire it from the push dispatcher) ------------
    # The camera silently refuses file ops until it has actually entered PLAYBACK. The app
    # gates on DataCameraGetPushStateInfo.getMode() (byte[4] of the 0x02/0x80 push) == 2,
    # or on a 0x02/0x82 GetPushPlayBackParams push. Feed those pushes in via these hooks.
    def note_camera_state(self, payload: bytes) -> None:
        """Call on every 0x02/0x80 push. Marks playback-ready when getMode()==PLAYBACK(2)."""
        if len(payload) > 4 and payload[4] == 2:
            self._playback_ready = True

    def note_playback_params(self, payload: bytes) -> None:
        """Call on every 0x02/0x82 push — its arrival alone confirms media/playback state."""
        self._playback_ready = True

    def enter_playback(self, strategy=0):
        """Switch the camera into playback/download mode. Media list/download only answer
        in this mode (in liveview/record the drone silently drops 0x00/0x20). WM160 uses
        0x02/0x10 set_camera_working_mode=PLAYBACK ([0x02])."""
        self._playback_ready = False
        self.d.enter_playback()

    def wait_playback_ready(self, timeout: float = 3.0, retry: bool = True) -> bool:
        """Block until the camera confirms PLAYBACK (via the note_* hooks), re-sending
        enter-playback once on timeout. Returns True if ready. Requires the push dispatcher
        to be feeding note_camera_state/note_playback_params."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._playback_ready:
                return True
            time.sleep(0.05)
        if retry:
            self.enter_playback(0)
            return self.wait_playback_ready(timeout, retry=False)
        self.last_note = "playback not confirmed (no 0x02/0x80 mode==2 or 0x02/0x82 push)"
        return False

    def request_list(self, index=0, count=50, playback_first=True, variant=None):
        # HW-CONFIRMED FIX (MEDIA_0XE0_RESEARCH_2026.md): the drone returns 0xE0 = INVALID_CMD
        # for cmd_id 0x20 — WM160 firmware does NOT implement the legacy "File List" 0x20.
        # The DJI Fly app uses RequestSendFiles 0x00/0x22 (payload 1 byte FILE_SELECT_MODE,
        # CURRENT=0), and the camera PUSHES the list back as 0x00/0x24 GetPushFiles. So the
        # list does NOT come in the ACK — the client listens for a separate 0x24 push (routed
        # to on_list_response by pc_client). Confirmed by app smali (Ccode + CmdIdCommon) and
        # dji-firmware-tools. Keeps `variant` as a manual probe hook for the paged native
        # layout (0x22 with a wider index/count payload) if the 1-byte form ever NAKs 0xE3.
        if playback_first:
            self.enter_playback(0)
            self.wait_playback_ready()
        if variant is None:
            payload = bytes([SELECT_CURRENT])                 # default: RequestSendFiles CURRENT
            self.last_note = "list: RequestSendFiles 0x22 [CURRENT] sent (list arrives as 0x24 push)"
        else:
            # Diagnostic: paged-native probe = 0x22 with index/count (matches the native
            # fetchMediaFiles(start,count) API), in case the 1-byte form NAKs INVALID_PARAM.
            payload = struct.pack("<IH", index & 0xFFFFFFFF, count & 0xFFFF)
            self.last_note = f"list: RequestSendFiles 0x22 paged ({len(payload)}B) sent"
        self.d.send_raw(CMDSET_GENERAL, CID_REQ_SEND_FILES, payload, receiver=self.receiver)

    def request_next(self):
        """Advance the album cursor (RequestSendFiles NEXT) — the camera pushes the next
        page/file as another 0x24."""
        self.d.send_raw(CMDSET_GENERAL, CID_REQ_SEND_FILES, bytes([SELECT_NEXT]),
                        receiver=self.receiver)

    def on_list_response(self, payload: bytes):
        # This is the 0x00/0x24 GetPushFiles payload = the file list. Dump each reply (rotating
        # name) so the exact native record layout can be pinned from a real capture.
        n = getattr(self, "_dump_i", 0)
        self._dump_i = n + 1
        path = f"{self.dump_dir}/media_list{n}_dump.bin"
        try:
            with open(path, "wb") as f:
                f.write(payload)
        except OSError:
            pass
        self.files, ok, note = parse_file_list(payload)
        self.last_note = note + f"  (raw {len(payload)}B -> {path})"
        return self.files

    def _start_view(self, mf: MediaFile, dest: str, grade: int, off: int, size: int):
        """Common path: RequestFile 0x00/0x26 with a grade byte; bytes stream back as 0x00/0x27
        (GetPushFile) → on_data_chunk. Used for original / thumbnail / screennail alike — a
        thumbnail is just a short byte-range read (MEDIA_DELETE_VIEW_RESEARCH_2026.md §B)."""
        self._dl = {"file": open(dest, "wb"), "path": dest, "received": 0,
                    "size": size, "index": mf.file_index, "seen": set()}
        self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE,
                        request_file_req(mf.file_index, grade, off, size),
                        receiver=self.receiver)

    def download(self, mf: MediaFile, dest: str):
        """Download the full-resolution ORIGINAL (grade 0, whole file)."""
        self._start_view(mf, dest, GRADE_ORIGIN, 0, mf.file_size or 0)

    def fetch_thumbnail(self, mf: MediaFile, dest: str):
        """Small cached thumbnail (grade 1). Offset/size come from the list record's nailInfo
        when known; else 0/0 and let the camera pick (some firmware treats grade alone)."""
        self._start_view(mf, dest, GRADE_THUMBNAIL, mf.thumb_off or 0, mf.thumb_size or 0)

    def fetch_screennail(self, mf: MediaFile, dest: str):
        """Larger preview image (grade 2, ~960x540 "screennail")."""
        self._start_view(mf, dest, GRADE_SCREENNAIL, mf.screen_off or 0, mf.screen_size or 0)

    def on_data_chunk(self, offset: int, data: bytes, seq: int | None = None):
        if not self._dl:
            return
        f = self._dl["file"]
        f.seek(offset)
        f.write(data)
        self._dl["received"] += len(data)
        # Selective-ACK: the drone streams a window of units and waits for an ACK before
        # sending the next window; without it the transfer stalls after the first window.
        # The exact window/mask format is capture-only (native), so ACK the unit we just got
        # by its seq/offset and let the first HW capture refine the mask. (2026 §4, §7)
        if seq is not None:
            self._dl["seen"].add(seq)
            self._ack_data(seq)
        if self._dl["received"] >= self._dl["size"] > 0:
            f.close()
            self._dl = None

    def _ack_data(self, seq: int):
        """Acknowledge a received data unit. Placeholder wire format (u32 LE seq) — the real
        selective-ACK mask is native and gets pinned from the first capture."""
        self.d.send_raw(CMDSET_GENERAL, CID_ACK_RECV_FILES,
                        struct.pack("<BI", ACK_SUCCESS, seq & 0xFFFFFFFF), receiver=self.receiver)

    def ack_receive(self, ok=True):
        """AckReceiveFiles 0x00/0x23 — acknowledge a received unit (Success=0 to keep the
        stream flowing, abort otherwise). The camera's file/list pushes may gate on this."""
        self.d.send_raw(CMDSET_GENERAL, CID_ACK_RECV_FILES,
                        bytes([ACK_SUCCESS if ok else ACK_ABORT]), receiver=self.receiver)

    def resend(self, index: int):
        """SetResendFiles 0x00/0x25 — ask the camera to resend a missed unit index."""
        self.d.send_raw(CMDSET_GENERAL, CID_SET_RESEND, struct.pack("<I", index & 0xFFFFFFFF),
                        receiver=self.receiver)

    def delete(self, files):
        """Delete one or more files by index (0x00/0x28 DeleteFile, count-prefixed index list).
        Pass a single MediaFile or a list. Multi-delete is native-normal (native deleteFiles
        takes an ArrayList keyed by index). If 0x28 NAKs 0xE0/0xD9, fall back to the camera-set
        0x02/0x79 selection model (MEDIA_DELETE_VIEW_RESEARCH_2026.md §A+§C2)."""
        fs = files if isinstance(files, (list, tuple)) else [files]
        self.d.send_raw(CMDSET_GENERAL, CID_FILE_DELETE,
                        delete_request([f.file_index for f in fs]), receiver=self.receiver)
