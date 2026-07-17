"""
media.py — browse / download / delete photos and videos on the drone's SD card.

Built from reverse_docs/MEDIA_TRANSFER.md. DJI Fly drives media through the CSDK
"KeyValue" layer: Java serializes a value object with ByteStreamHelper and hands the
byte[] to native, which wraps it as a DUML frame. The REQUEST bodies are fully proven
here (the serializer was decoded). The RESPONSE record (`MediaFile`) contains several
nested structures whose exact widths are native, so a multi-record list cannot be parsed
with certainty offline — the first real capture from the drone pins it down. Every
response is therefore dumped to a file so the parser can be finalised from real bytes.

Wire framing: these go over the AOA/composite path (our only media-capable channel),
cmd_set 0x00 (general) receiver camera, per the report.
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
CID_FILE_LIST = 0x20
CID_FILE_DATA = 0x1F
CID_FILE_DELETE = 0x28


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

    def enter_playback(self, strategy=0):
        """Switch the camera into playback/download mode. Media list/download only answer
        in this mode (in liveview/record the drone silently drops 0x00/0x20). WM160 uses
        0x02/0x10 set_camera_working_mode=PLAYBACK, with 0x02/0x0C as an alias."""
        self.d.enter_playback()

    def _list_variants(self, index, count):
        """Candidate on-wire get_file_list_req layouts to probe. The drone answers with a
        1-byte status (0xe0 = bad request) until the layout matches the firmware struct, so
        we cycle these and watch the reply length grow past 1 byte. Ordered shortest-first
        (the firmware struct is shorter than the CSDK task envelope we started with)."""
        return [
            b"",                                              # 0: empty — "list everything"
            w_i32(index) + w_i32(count),                      # 1: index, count
            w_i32(index) + w_i32(count) + w_i32(0) + w_i32(0),# 2: + slot, type(MEDIA)
            w_i32(index) + w_i32(count) + w_i32(FT_MEDIA)
                + w_i32(0) + w_i32(0),                        # 3: count-first-then-type variant
            file_list_request(index, count),                 # 4: full CSDK envelope (original)
        ]

    def request_list(self, index=0, count=50, playback_first=True, variant=None):
        if playback_first:
            self.enter_playback(0)
        variants = self._list_variants(index, count)
        if variant is None:
            variant = getattr(self, "_variant_i", 0)
            self._variant_i = (variant + 1) % len(variants)
        self._last_variant = variant % len(variants)
        payload = variants[self._last_variant]
        self.last_note = f"list variant {self._last_variant} ({len(payload)}B) sent"
        self.d.send_raw(CMDSET_GENERAL, CID_FILE_LIST, payload, receiver=self.receiver)

    def on_list_response(self, payload: bytes):
        # Per-variant filename so pressing "list" 5 times keeps all 5 replies (no overwrite).
        v = getattr(self, "_last_variant", 0)
        path = f"{self.dump_dir}/media_v{v}_dump.bin"
        try:
            with open(path, "wb") as f:
                f.write(payload)
        except OSError:
            pass
        self.files, ok, note = parse_file_list(payload)
        self.last_note = note + f"  (raw {len(payload)}B -> {path})"
        return self.files

    def download(self, mf: MediaFile, dest: str):
        """Start a full-resolution download. Chunks are written via on_data_chunk()."""
        self._dl = {"file": open(dest, "wb"), "path": dest, "received": 0,
                    "size": mf.file_size, "index": mf.file_index}
        self.d.send_raw(CMDSET_GENERAL, CID_FILE_DATA,
                        file_data_request(mf.file_index, mf.file_type, DATA_ORIGIN, 0, mf.file_size),
                        receiver=self.receiver)

    def on_data_chunk(self, offset: int, data: bytes):
        if not self._dl:
            return
        f = self._dl["file"]
        f.seek(offset)
        f.write(data)
        self._dl["received"] += len(data)
        if self._dl["received"] >= self._dl["size"] > 0:
            f.close()
            self._dl = None

    def delete(self, mf: MediaFile):
        if not mf.raw:
            raise ValueError("no raw MediaFile bytes to address the delete")
        self.d.send_raw(CMDSET_GENERAL, CID_FILE_DELETE,
                        delete_single_request(mf.raw), receiver=self.receiver)
