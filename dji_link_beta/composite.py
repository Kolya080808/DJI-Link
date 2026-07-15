"""
DUSS composite-mux demux for the raw AOA stream (confirmed by reversing:
duss_parse_composite_data @0x491a070, thread mb_route_usb_data_recv_task).

Raw bulk from AOA = a stream of units:
  [0]=0x55 [1]=0xCC | type u16 LE | length u32 LE | payload[length]
  full unit size = 8 + length

Routing by type (index = type - 0x5749):
  0x5749 -> DUML channel  (payload = standard DUML frame, starts with 0x55)
  0x574A / 0x574D -> video (payload starts with 0x6d, 16-byte liveview header)
  0x574B / 0x574C / 0x7530 -> other channels (ignored for now)

A unit can be split across USB reads -> we buffer; resync on 0x55 0xCC.
"""

from __future__ import annotations
import struct

SOF0, SOF1 = 0x55, 0xCC
TYPE_DUML = 0x5749
TYPE_VIDEO = (0x574A, 0x574D)


def wrap(payload: bytes, typ: int = TYPE_DUML) -> bytes:
    """Wrap a payload (e.g. a DUML frame) into a composite unit for sending over AOA.
    The Pi simply forwards this to the RC without parsing anything (jump-host)."""
    return bytes([SOF0, SOF1]) + struct.pack("<H", typ) + struct.pack("<I", len(payload)) + payload


class CompositeDemux:
    def __init__(self, on_duml=None, on_video=None, on_unit=None, max_unit=0x200000):
        self.buf = bytearray()
        self.on_duml = on_duml       # callback(payload: bytes) — DUML frame(s)
        self.on_video = on_video     # callback(payload: bytes) — starts with 0x6d
        # callback(typ: int, payload: bytes) for EVERY unit, including types we do not
        # route. Without it, unknown channels (e.g. 0x574B carries the RC's ASCII log)
        # are invisible.
        self.on_unit = on_unit
        self.max_unit = max_unit
        self.units = 0
        self.type_counts: dict[int, int] = {}

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            if len(self.buf) < 8:
                break
            if not (self.buf[0] == SOF0 and self.buf[1] == SOF1):
                # resync: look for the next 55 CC
                idx = self.buf.find(bytes([SOF0, SOF1]), 1)
                if idx < 0:
                    keep = 1 if self.buf[-1] == SOF0 else 0   # partial SOF
                    del self.buf[:len(self.buf) - keep]
                    break
                del self.buf[:idx]
                continue
            typ = struct.unpack_from("<H", self.buf, 2)[0]
            length = struct.unpack_from("<I", self.buf, 4)[0]
            if length > self.max_unit:
                del self.buf[0]                 # garbage — shift
                continue
            total = 8 + length
            if len(self.buf) < total:
                break                            # wait for the rest (split between reads)
            payload = bytes(self.buf[8:total])
            del self.buf[:total]
            self.units += 1
            self.type_counts[typ] = self.type_counts.get(typ, 0) + 1
            if self.on_unit:
                self.on_unit(typ, payload)
            if typ == TYPE_DUML and self.on_duml:
                self.on_duml(payload)
            elif typ in TYPE_VIDEO and self.on_video:
                self.on_video(payload)
            # other types — reported via on_unit only


def feed_video_payload(reassembler, payload: bytes) -> bool:
    """video unit payload = [16-byte LvHeader][H.264 slice] -> into the reassembler.

    Returns False when the payload does not carry a liveview header, so the caller can
    report it. Dropping silently made a stream of 1000+ unparsed packets look identical
    to no stream at all.
    """
    from liveview import LvHeader, HDR_LEN, MAGIC_NEW
    if len(payload) < HDR_LEN or payload[0] != MAGIC_NEW:
        return False
    hdr = LvHeader.parse(payload[:HDR_LEN])
    reassembler.feed_packet(hdr, payload[HDR_LEN:HDR_LEN + hdr.pkt_len])
    return True


def looks_like_h264(payload: bytes) -> bool:
    """True if the payload begins with an H.264 Annex-B start code (3- or 4-byte)."""
    return payload[:4] == b"\x00\x00\x00\x01" or payload[:3] == b"\x00\x00\x01"
