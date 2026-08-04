"""
media.py — SD card list / download / thumbnail / delete for WM160 (Mavic Mini 1).

Source of truth: `dji_link_beta/reverse_docs/MEDIA_PROTOCOL_DEX_TRUTH.md`
(static jadx decompile of DJI Fly v1.21.4 classes_*.dex — NOT the older
hand-written reverse_docs, which contradicted each other).

Playback entry (CONFIRMED): cmdset=0x02 CAMERA, cmd=0x10 SetMode,
  payload={0x03} = CameraWorkMode.MEDIA_DOWNLOAD. The camera answers 0x02/0x80
  pushes with CameraWorkMode at payload[4]; media ops are accepted at mode 3
  (media download), not plain 2 (playback).

Legacy litchis transport (CONFIRMED in DEX, likely disused on WM160):
  All requests ride cmd_set 0x00 / cmd_id 0x26 (RequestFile), receiver=CAMERA(0x01).
  Camera answers on 0x00/0x27 (GetPushFile) DATA frames.
  0x28 DeleteFile exists as a CmdIdCommon enum entry but its model class is
  ABSENT from the dex — delete is implemented only in native `libcrossplayback.so`
  this app ships; the layout below is therefore CAPTURE-PENDING (best known).

FileChannel inner header (10 bytes, LE), the 0x26/0x27 DUML payload:
  [0]    0x0A | (version<<6)   version=1 → 0x4A (low 6 bits = header length = 10)
  [1]    (cmdId<<5) | cmdType  cmdId: List=0 File=1 Stream=2 Num=3 (unused)
                               cmdType: REQ=0 DATA=1 ACK=2 PUSH=3 ABORT=4 DEL=5
  [2-3]  total length u16 (header + inner payload).  NOTE asymmetry on RX:
         FileRecvPack does total=u16>>12 (top 4 bits = pkt-idx), len=u16 & 0xFFF.
  [4-5]  sessionId u16         (new per transfer, echoed in replies)
  [6-9]  offset u32            (0 on requests; file byte-offset on DATA chunks)
  [10..] inner payload

LIST request inner payload (7B): [startIndex u32; storage in top 2 bits of byte3]
                                 [count u16][subType u8=0]
LIST response (0x27 DATA, after 10B header): [count u32][dataLen u32][records...]
  record: [typeword u32][reserved u32][index u32][nameLen u8][name ASCII]
  (fileSize NOT present on the wire here — dataset-level MediaFile.fromBytes has
   fileSize u64 after a length-prefixed name; that path is native crossplayback.)

FILE request inner payload (16B): [index u32][subCount u16=0][subType u8][grade u8=0]
                                  [offset u32=0][length u32=0 → whole]
  subType: ORG=0 THM=1 SCR=2
"""

from __future__ import annotations

import struct
import time

try:
    import applog
    _LOG = applog.get_logger()
    def _log(*a): _LOG.info("[media] " + " ".join(str(x) for x in a))
except Exception:
    def _log(*a): print("[media]", *a, flush=True)

# ---------------------------------------------------------------- constants

CMDSET_GENERAL = 0x00
CID_REQUEST_FILE = 0x26   # RequestFile — outer frame for List/File/Stream
CID_PUSH_FILE    = 0x27   # GetPushFile — camera → app DATA/PUSH
CID_DELETE       = 0x28   # DeleteFile

# FileChannel cmdId (inner header byte[1] high 3 bits)
FC_LIST, FC_FILE, FC_STREAM, FC_NUM = 0, 1, 2, 3
# FileChannel cmdType (inner header byte[1] low 5 bits)
FCT_REQ, FCT_DATA, FCT_ACK, FCT_PUSH, FCT_ABORT, FCT_DEL = 0, 1, 2, 3, 4, 5

# SubType (data grade)
SUB_ORG, SUB_THM, SUB_SCR = 0, 1, 2

STORAGE_INTERNAL, STORAGE_SD = 0, 1

# File type heuristic from name extension (records don't carry a clean type byte)
def _is_video_name(name: str) -> bool:
    return name.upper().endswith((".MP4", ".MOV"))


# ---------------------------------------------------------------- FileChannel framing

def fc_header(cmd_id: int, cmd_type: int, inner: bytes,
              session: int, offset: int = 0) -> bytes:
    """Build the 10-byte FileChannel header + inner payload (the 0x26 DUML payload)."""
    tot = 10 + len(inner)
    h = bytearray(10)
    h[0] = 0x0A | (1 << 6)                    # version=1, headerLen=10 → 0x4A
    h[1] = ((cmd_id & 7) << 5) | (cmd_type & 0x1F)
    h[2:4] = struct.pack("<H", tot & 0xFFFF)
    h[4:6] = struct.pack("<H", session & 0xFFFF)
    h[6:10] = struct.pack("<I", offset & 0xFFFFFFFF)
    return bytes(h) + inner


def parse_fc(payload: bytes):
    """Parse an incoming 0x27 payload. Returns (cmd_id, cmd_type, session, offset, inner)."""
    if len(payload) < 10:
        return None
    hlen = payload[0] & 0x3F
    cid = payload[1] >> 5
    ctype = payload[1] & 0x1F
    session = struct.unpack_from("<H", payload, 4)[0]
    offset = struct.unpack_from("<I", payload, 6)[0]
    return cid, ctype, session, offset, payload[hlen:]


# ---------------------------------------------------------------- data types

class MediaFile:
    __slots__ = ("file_index", "file_name", "file_size", "raw")

    def __init__(self, file_index, file_name, file_size=None, raw=None):
        self.file_index = file_index
        self.file_name = file_name
        self.file_size = file_size
        self.raw = raw

    @property
    def is_video(self):
        return _is_video_name(self.file_name or "")

    def __repr__(self):
        kind = "video" if self.is_video else "photo"
        sz = f"{self.file_size/1_000_000:.1f}MB" if self.file_size else "?"
        return f"<{self.file_name} {kind} {sz}>"


def parse_file_list(blob: bytes):
    """Parse the reassembled LIST DATA payload (after FileChannel headers stripped).
      [count u32][dataLen u32][records...]
      record: [typeword u32][reserved u32][index u32][nameLen u8][name ASCII]
    Returns (files, note).
    """
    try:
        if len(blob) < 8:
            return [], f"list blob too short ({len(blob)}B)"
        count = struct.unpack_from("<I", blob, 0)[0]
        if count > 100_000:
            return [], f"implausible count {count} — layout differs, dump saved"
        files = []
        p = 8
        for _ in range(count):
            if p + 13 > len(blob):
                break
            start = p
            # [0-3] typeword, [4-7] reserved, [8-11] index, [12] nameLen
            index = struct.unpack_from("<I", blob, p + 8)[0]
            n = blob[p + 12]
            name = blob[p + 13:p + 13 + n].decode("ascii", "replace")
            files.append(MediaFile(file_index=index, file_name=name,
                                   raw=blob[start:p + 13 + n]))
            p += 13 + n
        note = f"parsed {len(files)}/{count} file(s)"
        return files, note
    except Exception as e:
        return [], f"list parse failed ({e}); dump saved"


# ---------------------------------------------------------------- high-level client

class MediaClient:
    """SD card media over the litchis FileChannel (0x00/0x26 out, 0x00/0x27 in).

    Wire into the DUML push dispatcher:
      note_camera_state(payload)  on every 0x02/0x80 push  (playback gate)
      on_push(payload)            on every 0x00/0x27 push  (all FileChannel replies)
    """

    def __init__(self, drone, receiver: int = 0x01, dump_dir: str = "."):
        self.d          = drone
        self.receiver   = receiver
        self.dump_dir   = dump_dir
        self.files: list[MediaFile] = []
        self.last_note  = ""
        self._session   = 0
        self._cam_mode  = -1
        self._playback_ready = False
        # per-session reassembly buffers: session -> {"cid","buf":bytearray,"kind","file","path","meta_stripped"}
        self._sessions  = {}

    # ---- session id ----
    def _new_session(self) -> int:
        self._session = (self._session + 1) & 0xFFFF
        if self._session == 0:
            self._session = 1
        return self._session

    # ---- playback gate ----
    def note_camera_state(self, payload: bytes) -> None:
        """0x02/0x80 push — byte[4] is CameraWorkMode. DEX: PLAYBACK=2, MEDIA_DOWNLOAD=3.
        Media ops require mode 3 (MEDIA_DOWNLOAD); plain playback (=2) is not enough."""
        if len(payload) > 4:
            self._cam_mode = payload[4]
            if self._cam_mode == 3:
                self._playback_ready = True

    def enter_playback(self) -> None:
        self._playback_ready = False
        self.d.enter_playback()   # drone enters MEDIA_DOWNLOAD(3) since v0.9.2

    def exit_playback(self) -> None:
        self.d.exit_playback()

    def wait_playback_ready(self, timeout: float = 5.0, retry: bool = True) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._playback_ready:
                return True
            time.sleep(0.05)
        if retry:
            self.enter_playback()
            return self.wait_playback_ready(timeout, retry=False)
        self.last_note = "playback gate timed out"
        return False

    # ---- LIST ----
    def request_list(self, playback_first: bool = True, start: int = 0,
                     count: int = 200, storage: int = STORAGE_SD) -> None:
        """Send a FileChannel LIST request. The camera answers with a PUSH announcement
        (cmdType=3) carrying the total transfer length, then streams DATA (cmdType=1)
        ONLY after we ACK (cmdType=2). on_push() drives that flow-control handshake.

        storage: WM160's SD card is storage 1 (confirmed on hardware — storage 0
        INTERNAL stays silent, storage 1 announced a 4110-byte list).
        """
        if playback_first:
            self.enter_playback()
            if not self.wait_playback_ready():
                self.last_note = "request_list: playback gate timed out"; return
        self.files = []
        self._list_storage = storage
        self._list_count = count
        sess = self._new_session()
        inner = bytearray(7)
        struct.pack_into("<I", inner, 0, start & 0x3FFFFFFF)
        inner[3] = (inner[3] & 0x3F) | ((storage & 0x03) << 6)
        struct.pack_into("<H", inner, 4, count & 0xFFFF)
        inner[6] = SUB_ORG
        self._sessions[sess] = {"cid": FC_LIST, "buf": bytearray(), "storage": storage}
        frame = fc_header(FC_LIST, FCT_REQ, bytes(inner), sess)
        _log(f"LIST send: 0x00/0x26 sess={sess} storage={storage} "
             f"inner={bytes(inner).hex()} full_fc={frame.hex()}")
        self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE, frame, receiver=self.receiver)
        self.last_note = f"LIST req sent (session={sess}, storage={storage}, count={count})"

    def sweep_list_params(self, storages=(0, 1, 2, 3), subtypes=(0, 1, 2),
                          count: int = 200) -> None:
        """Brute-force LIST params: send one LIST per (storage, subType) combo, spaced
        so replies don't collide. The frame format is smali-confirmed correct, so this
        hunts the semantic combo that makes the camera return count>0. on_push() logs
        'LIST PUSH announce'/'LIST complete' per session — watch for a non-zero count.
        Each session records its (storage, subType) so the log names the winner."""
        import threading
        combos = [(st, sub) for st in storages for sub in subtypes]
        _log(f"SWEEP start: {len(combos)} combos {combos}")
        def fire(i):
            if i >= len(combos):
                _log("SWEEP done — check for any 'count>0' above"); return
            st, sub = combos[i]
            sess = self._new_session()
            inner = bytearray(7)
            inner[3] = (st & 0x03) << 6
            struct.pack_into("<H", inner, 4, count & 0xFFFF)
            inner[6] = sub
            self._sessions[sess] = {"cid": FC_LIST, "buf": bytearray(),
                                    "storage": st, "subtype": sub, "sweep": True}
            frame = fc_header(FC_LIST, FCT_REQ, bytes(inner), sess)
            _log(f"SWEEP[{i}] LIST sess={sess} storage={st} subType={sub} "
                 f"inner={bytes(inner).hex()}")
            self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE, frame,
                            receiver=self.receiver)
            threading.Timer(0.6, fire, args=(i + 1,)).start()
        fire(0)

    def _send_ack(self, cmd_id: int, session: int, seek: int = 0,
                  ranges=None) -> None:
        """FileChannel ACK (cmdType=2) — tells the camera to (keep) streaming.
        inner = z(seek)[4] + count(1) + [z(offset)[4] + z(len)[4]] * count.
        Default (ranges=None): one range [seek .. 0xffffffff] = 'send the whole thing'.
        Matches DataRequestAck.doPack: null list → z(c) then z(-1) at offsets 5 and 9."""
        if ranges is None:
            ranges = [(seek, 0xFFFFFFFF)]
        inner = bytearray(struct.pack("<I", seek & 0xFFFFFFFF))
        inner.append(len(ranges) & 0xFF)
        for off, length in ranges:
            inner += struct.pack("<II", off & 0xFFFFFFFF, length & 0xFFFFFFFF)
        frame = fc_header(cmd_id, FCT_ACK, bytes(inner), session)
        _log(f"ACK send: cid={cmd_id} sess={session} seek={seek} "
             f"ranges={ranges} full_fc={frame.hex()}")
        self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE, frame, receiver=self.receiver)

    # ---- DOWNLOAD / THUMBNAIL ----
    def download(self, mf: MediaFile, dest: str) -> None:
        """Full original file (subType=ORG)."""
        self._start_file(mf, dest, SUB_ORG)

    def fetch_thumbnail(self, mf: MediaFile, dest: str) -> None:
        """Thumbnail (subType=THM)."""
        self._start_file(mf, dest, SUB_THM)

    def fetch_screennail(self, mf: MediaFile, dest: str) -> None:
        """Larger preview (subType=SCR)."""
        self._start_file(mf, dest, SUB_SCR)

    def fetch_index(self, index: int, dest: str, sub_type: int = SUB_ORG) -> None:
        """Request a file by RAW index (no MediaFile object needed) — for the brute
        probe that bypasses the broken LIST. Data streams back as 0x00/0x27 FILE frames."""
        self._start_file(MediaFile(file_index=index, file_name=dest), dest, sub_type)

    def _start_file(self, mf: MediaFile, dest: str, sub_type: int) -> None:
        sess = self._new_session()
        inner = bytearray(16)
        struct.pack_into("<I", inner, 0, mf.file_index & 0xFFFFFFFF)  # index
        struct.pack_into("<H", inner, 4, 0)                           # subCount
        inner[6] = sub_type                                          # subType
        inner[7] = 0                                                # grade
        struct.pack_into("<I", inner, 8, 0)                          # offset
        struct.pack_into("<I", inner, 12, 0)                         # length 0=whole
        self._sessions[sess] = {"cid": FC_FILE, "file": open(dest, "wb"),
                                "path": dest, "meta_stripped": False, "received": 0,
                                "last_rx": time.monotonic()}
        self.d.send_raw(CMDSET_GENERAL, CID_REQUEST_FILE,
                        fc_header(FC_FILE, FCT_REQ, bytes(inner), sess),
                        receiver=self.receiver)
        self.last_note = f"FILE req sent (session={sess}, index={mf.file_index}, sub={sub_type} → {dest})"

    # ---- DELETE (CAPTURE-PENDING — not confirmed against WM160) ----
    def delete(self, mf) -> None:
        """0x00/0x28 DeleteFile — count-prefixed index list. CmdIdCommon.DeleteFile=0x28
        is only an enum entry in v1.21.4; the packer class is stripped (native-only).
        This layout (count u16 + indices u32) is our best-known guess — WATCH for
        0xD6 PARAM_ERROR; if seen, the real format came from libcrossplayback.so and
        needs a wire capture to fix."""
        fs = [mf] if not isinstance(mf, (list, tuple)) else list(mf)
        payload = struct.pack("<H", len(fs)) + b"".join(
            struct.pack("<I", f.file_index & 0xFFFFFFFF) for f in fs)
        self.d.send_raw(CMDSET_GENERAL, CID_DELETE, payload, receiver=self.receiver)
        self.last_note = f"DELETE sent for {[f.file_index for f in fs]}"

    # ---- incoming 0x00/0x27 DATA/PUSH ----
    def on_push(self, payload: bytes):
        """Feed every 0x00/0x27 push here. Routes by session to list/file reassembly.
        Returns the completed file list (list[MediaFile]) when a LIST finishes, else None.
        """
        parsed = parse_fc(payload)
        if parsed is None:
            _log(f"0x27 recv but too short/unparseable: {payload[:16].hex()}")
            return None
        cid, ctype, session, offset, inner = parsed
        _log(f"0x27 recv: fc_cid={cid} fc_type={ctype} sess={session} off={offset} "
             f"inner_len={len(inner)} inner={inner[:24].hex()}")
        sess = self._sessions.get(session)
        if sess is None:
            _log(f"0x27 for UNKNOWN session {session} (known={list(self._sessions)}) — dumping")
            self._dump(f"media_push_unknown_s{session}.bin", payload)
            return None

        if sess["cid"] == FC_LIST:
            # Flow control: PUSH(3) announces the transfer (header offset field carries
            # the total length); we must ACK(2) to make the camera stream DATA(1). Each
            # DATA chunk is written at its header offset; we ACK progress to keep it going.
            if ctype == FCT_PUSH:
                # PUSH announces the result count in inner[0:4] (u32). count>0 = files exist.
                cnt = struct.unpack_from("<I", inner, 0)[0] if len(inner) >= 4 else -1
                tag = (f"storage={sess.get('storage')} subType={sess.get('subtype')}"
                       if sess.get("sweep") else "")
                marker = "  <<< NON-ZERO! FILES FOUND" if cnt > 0 else ""
                _log(f"LIST PUSH: count={cnt} {tag}{marker}")
                sess["total"] = cnt
                if cnt > 0:
                    self._send_ack(FC_LIST, session, seek=0)
                return None
            if ctype == FCT_DATA:
                buf = sess["buf"]
                # Place chunk at its absolute offset (frames may not be contiguous).
                if offset + len(inner) > len(buf):
                    buf += b"\x00" * (offset + len(inner) - len(buf))
                buf[offset:offset + len(inner)] = inner
                sess["got"] = sess.get("got", 0) + len(inner)
                total = sess.get("total", 0)
                _log(f"LIST DATA: off={offset} +{len(inner)} got={sess['got']}/{total}")
                # ACK our progress so the camera sends the next window.
                self._send_ack(FC_LIST, session, seek=sess["got"])
                if total and sess["got"] >= total:
                    files, note = parse_file_list(bytes(buf))
                    self.last_note = note
                    self._dump(f"media_list_s{session}.bin", bytes(buf))
                    del self._sessions[session]
                    self.files = files
                    _log(f"LIST complete: {len(files)} file(s), {sess['got']}B")
                    return files
                return None
            if ctype == FCT_ABORT:
                # Camera ended the transfer. Parse whatever we have.
                files, note = parse_file_list(bytes(sess["buf"]))
                self.last_note = note or "LIST aborted by camera"
                self._dump(f"media_list_s{session}_abort.bin", bytes(sess["buf"]))
                got, total = sess.get("got", 0), sess.get("total", 0)
                _log(f"LIST ABORT: got={got}/{total}, parsed {len(files)} file(s)")
                del self._sessions[session]
                self.files = files
                return files
            return None

        if sess["cid"] == FC_FILE:
            data = inner
            if not sess["meta_stripped"] and offset == 0 and len(data) >= 13:
                n = data[12]
                data = data[13 + n:]
                sess["meta_stripped"] = True
            f = sess["file"]
            # After metadata strip, offset is the absolute file position for this chunk.
            write_at = offset if (sess["meta_stripped"] and offset > 0) else sess["received"]
            f.seek(write_at)
            f.write(data)
            sess["received"] += len(data)
            sess["last_rx"] = time.monotonic()
            f.flush()
            return None
        return None

    def has_active_download(self) -> bool:
        return any(st.get("cid") == FC_FILE for st in self._sessions.values())

    def reap_finished_downloads(self, idle: float = 2.0) -> list:
        """Close file sessions that got no chunk for `idle` seconds. Returns list of
        (path, bytes) for finished transfers. Call this periodically from the UI loop."""
        done = []
        now = time.monotonic()
        for s, st in list(self._sessions.items()):
            if st.get("cid") != FC_FILE:
                continue
            if st.get("received", 0) > 0 and now - st.get("last_rx", now) > idle:
                try:
                    st["file"].close()
                except Exception:
                    pass
                done.append((st["path"], st["received"]))
                del self._sessions[s]
        return done

    def close_file(self, session: int = None) -> None:
        """Close any open file-download sessions (call when a transfer is done/aborted)."""
        for s, st in list(self._sessions.items()):
            if st.get("cid") == FC_FILE and (session is None or s == session):
                try:
                    st["file"].close()
                except Exception:
                    pass
                del self._sessions[s]

    def _dump(self, name: str, data: bytes) -> None:
        try:
            with open(f"{self.dump_dir}/{name}", "wb") as f:
                f.write(data)
        except OSError:
            pass
