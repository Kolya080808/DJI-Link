"""
Parsing and reassembly of the DJI Mavic Mini 1 (WM160) liveview video stream — from reversing.

Video goes over a separate libwlm channel (NOT wrapped in the DUML 0x55 envelope). Packet:
  [16-byte header][H.264 slice payload]

Header (LE), magic 0x6d = WM160 format:
  0x00      magic (0x6d)
  0x01      bit0 is_i | bits1-2 video_chan | bits3-4 video_fmt(0=H264) | bit5 clear_cache
  0x02-0x05 u32 frm_idx  (low16 — frame ordering key)
  0x06-0x09 bits0-19 pkt_len | bits20-31 ssfn
  0x0a      video_fps
  0x0b-0x0e bit0 frm_end | b1-5 slice_idx | b6 pkt_enable | b7 slice_end |
            b8 pkt_is_last | b9-15 pkt_idx | b16-25 frm_len_KB
  0x0f      hdr_crc (ignored)

Reassembly: group by frm_idx, order by (slice_idx, pkt_idx), concatenate the first
pkt_len bytes of payload; frame is complete on frm_end. is_i = keyframe.
"""

from __future__ import annotations
from dataclasses import dataclass
import struct

MAGIC_NEW = 0x6D
HDR_LEN = 16


@dataclass
class LvHeader:
    magic: int
    is_i: int
    video_chan: int
    video_fmt: int
    clear_cache: int
    frm_idx: int
    pkt_len: int
    ssfn: int
    video_fps: int
    frm_end: int
    slice_idx: int
    slice_end: int
    pkt_is_last: int
    pkt_idx: int
    frm_len_kb: int

    @classmethod
    def parse(cls, b: bytes) -> "LvHeader":
        f1 = b[1]
        frm_idx = struct.unpack_from("<I", b, 2)[0]
        w6 = struct.unpack_from("<I", b, 6)[0]
        w11 = struct.unpack_from("<I", b, 0x0B)[0]
        return cls(
            magic=b[0], is_i=f1 & 1, video_chan=(f1 >> 1) & 3,
            video_fmt=(f1 >> 3) & 3, clear_cache=(f1 >> 5) & 1,
            frm_idx=frm_idx, pkt_len=w6 & 0xFFFFF, ssfn=(w6 >> 20) & 0xFFF,
            video_fps=b[0x0A],
            frm_end=w11 & 1, slice_idx=(w11 >> 1) & 0x1F,
            slice_end=(w11 >> 7) & 1, pkt_is_last=(w11 >> 8) & 1,
            pkt_idx=(w11 >> 9) & 0x7F, frm_len_kb=(w11 >> 16) & 0x3FF,
        )


def build_header(is_i=0, video_chan=0, video_fmt=0, clear_cache=0,
                 frm_idx=0, pkt_len=0, ssfn=0, video_fps=30,
                 frm_end=0, slice_idx=0, slice_end=0, pkt_is_last=0,
                 pkt_idx=0, frm_len_kb=0) -> bytes:
    """Build a 16-byte header (for tests)."""
    f1 = (is_i & 1) | (video_chan & 3) << 1 | (video_fmt & 3) << 3 | (clear_cache & 1) << 5
    w6 = (pkt_len & 0xFFFFF) | (ssfn & 0xFFF) << 20
    w11 = ((frm_end & 1) | (slice_idx & 0x1F) << 1 | (slice_end & 1) << 7 |
           (pkt_is_last & 1) << 8 | (pkt_idx & 0x7F) << 9 | (frm_len_kb & 0x3FF) << 16)
    return (bytes([MAGIC_NEW, f1]) + struct.pack("<I", frm_idx) +
            struct.pack("<I", w6) + bytes([video_fps & 0xFF]) +
            struct.pack("<I", w11) + bytes([0]))


class LiveviewReassembler:
    """Liveview packets -> whole H.264 frames (Annex-B). on_frame(bytes, is_keyframe)."""

    def __init__(self, on_frame, max_open=8):
        self.on_frame = on_frame
        self.frames = {}        # frm_idx16 -> {(slice_idx,pkt_idx): payload}
        self.is_key = {}        # frm_idx16 -> bool
        self.max_open = max_open

    def feed_packet(self, hdr: LvHeader, payload: bytes) -> None:
        fi = hdr.frm_idx & 0xFFFF
        parts = self.frames.setdefault(fi, {})
        parts[(hdr.slice_idx, hdr.pkt_idx)] = payload[:hdr.pkt_len]
        if hdr.is_i:
            self.is_key[fi] = True
        if hdr.frm_end:
            frame = b"".join(parts[k] for k in sorted(parts.keys()))
            self.on_frame(frame, self.is_key.get(fi, False))
            self.frames.pop(fi, None)
            self.is_key.pop(fi, None)
        # clean up overly old unfinished frames
        if len(self.frames) > self.max_open:
            oldest = min(self.frames.keys())
            self.frames.pop(oldest, None)
            self.is_key.pop(oldest, None)


class LiveviewStream:
    """Scan the raw stream: 0x6d -> video packets (into reassembler), 0x55 -> into duml_stream.

    Heuristic demux (the exact DUSS mux envelope is not cracked yet) — we cut by magic
    and the length field; on garbage we shift by a byte. To be refined on real bytes from the Pi.
    """

    def __init__(self, reassembler: LiveviewReassembler, duml_stream=None,
                 max_pkt=0x100000):
        self.buf = bytearray()
        self.reasm = reassembler
        self.duml = duml_stream
        self.max_pkt = max_pkt

    def feed(self, data: bytes):
        self.buf += data
        while True:
            if not self.buf:
                break
            b0 = self.buf[0]
            if b0 == MAGIC_NEW:
                if len(self.buf) < HDR_LEN:
                    break
                hdr = LvHeader.parse(bytes(self.buf[:HDR_LEN]))
                total = HDR_LEN + hdr.pkt_len
                if hdr.pkt_len == 0 or hdr.pkt_len > self.max_pkt:
                    del self.buf[0]            # false magic — resync
                    continue
                if len(self.buf) < total:
                    break                      # wait for the rest of the packet
                payload = bytes(self.buf[HDR_LEN:total])
                del self.buf[:total]
                self.reasm.feed_packet(hdr, payload)
            elif b0 == 0x55 and self.duml is not None:
                # hand over one DUML frame (using the length from the DUML header)
                if len(self.buf) < 4:
                    break
                total = (self.buf[1] | (self.buf[2] << 8)) & 0x3FF
                if total < 13 or len(self.buf) < total:
                    if total < 13:
                        del self.buf[0]; continue
                    break
                self.duml.feed(bytes(self.buf[:total]))
                del self.buf[:total]
            else:
                del self.buf[0]                # garbage — shift
