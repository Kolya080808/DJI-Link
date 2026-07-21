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

# --- ByteStreamHelper primitives (little-endian), matching the app serializer ---
def w_i32(v):  return struct.pack("<i", int(v))
def w_i64(v):  return struct.pack("<q", int(v))
def w_bool(v): return struct.pack("<B", 1 if v else 0)
def w_list(items): return struct.pack("<i", len(items)) + b"".join(items)

# FileType filters (§8)
FT_MEDIA = 0
FILTER_ALL_PHOTO = 25
FILTER_ALL_VIDEO = 26
# FileDataType
DATA_ORIGIN = 0
DATA_THUMBNAIL = 1
DATA_SCREEN = 2
# MediaFileType
MFT_JPEG, MFT_DNG, MFT_MOV, MFT_MP4 = 0, 1, 2, 3
TIME_NEW_FIRST = 1
DELETE_SINGLE = 3

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


def file_list_request_native(index: int, count: int, slot: int = 0, ftype: int = FT_MEDIA) -> bytes:
    """Best-guess NATIVE get_file_list_req (0x00/0x20): u32 index, u16 count, u8 slot, u8 type.
    The app re-serializes this in C++, so the exact widths are capture-pending — this is the
    shortest-plausible struct to try first; parse_file_list dumps the reply to pin it."""
    return struct.pack("<IHBB", index & 0xFFFFFFFF, count & 0xFFFF, slot & 0xFF, ftype & 0xFF)


def file_data_request_native(file_index: int, data_type: int, off: int, size: int,
                             slot: int = 0) -> bytes:
    """Best-guess NATIVE get_file_data_req (0x00/0x1F): u32 index, u8 subtype(ORIGIN/THUMB/
    SCREEN), u8 slot, u32 offset, u32 size. Capture-pending, same caveat as the list req."""
    return struct.pack("<IBBII", file_index & 0xFFFFFFFF, data_type & 0xFF, slot & 0xFF,
                       off & 0xFFFFFFFF, size & 0xFFFFFFFF)


# ---------------------------------------------------------------- request encoders
def file_list_request(index: int, count: int, *, file_type=FT_MEDIA, slot=0,
                      is_all=False, is_sub=False, filters=(FILTER_ALL_PHOTO, FILTER_ALL_VIDEO),
                      order_type=0, time_order=TIME_NEW_FIRST, size_order=0) -> bytes:
    b = w_i32(index) + w_i32(count) + w_i32(0)
    b += w_i32(file_type) + w_i32(slot)
    b += w_i32(0) + w_i32(0)
    b += w_bool(is_all) + w_bool(is_sub)
    b += w_list([w_i32(f) for f in filters])
    b += w_i32(order_type) + w_i32(time_order) + w_i32(size_order)
    return b


def file_data_request(file_index: int, media_file_type: int, data_type: int,
                      off: int, size: int, *, slot=0, sub_index=0, seg_sub=0, uuid=0,
                      nail=(0, 0, 0, 0)) -> bytes:
    b = w_i32(file_index) + w_i32(1) + w_i32(data_type) + w_i32(slot)
    b += w_i64(off) + w_i64(size)
    b += w_i32(sub_index) + w_i32(seg_sub)
    b += w_i32(0) + w_i32(0)
    b += w_i32(media_file_type)
    b += w_bool(False) + w_bool(size > 0xFFFFFFFF)
    b += w_i64(uuid)
    b += w_list([])
    b += b"".join(w_i64(x) for x in nail)
    b += w_bool(False) + w_i32(0)
    return b


def delete_single_request(mediafile_bytes: bytes, slot=0) -> bytes:
    filepkg = w_i32(FT_MEDIA) + w_list([mediafile_bytes]) + w_list([]) + w_list([])
    tag = w_bool(False) * 4
    return w_i32(slot) + w_i32(DELETE_SINGLE) + filepkg + tag


# ---------------------------------------------------------------- response parse (best-effort)
class MediaFile:
    __slots__ = ("file_index", "file_type", "file_name", "file_size", "duration_ms", "raw")

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
            # Diagnostic: paged-native probe = 0x22 with index/count, in case the 1-byte
            # form is rejected as INVALID_PARAM on this firmware.
            payload = file_list_request_native(index, count)
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

    def download(self, mf: MediaFile, dest: str, data_type=DATA_ORIGIN):
        """Start a full-resolution download via RequestFile 0x00/0x26 (the file-transfer
        request in the confirmed 0x22/0x24/0x26/0x27 cluster). Data streams back as 0x00/0x27
        (GetPushFile) pushes → on_data_chunk. cmd_id 0x1F is NOT implemented on WM160 (would
        NAK 0xE0, same as 0x20). The 0x26 payload beyond the file index is native/paged — the
        first HW capture pins it (responses dumped)."""
        self._dl = {"file": open(dest, "wb"), "path": dest, "received": 0,
                    "size": mf.file_size, "index": mf.file_index, "seen": set()}
        self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE,
                        file_data_request_native(mf.file_index, data_type, 0, mf.file_size),
                        receiver=self.receiver)

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

    def delete(self, mf: MediaFile):
        if not mf.raw:
            raise ValueError("no raw MediaFile bytes to address the delete")
        self.d.send_raw(CMDSET_GENERAL, CID_FILE_DELETE,
                        delete_single_request(mf.raw), receiver=self.receiver)
