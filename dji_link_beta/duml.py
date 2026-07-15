"""
DUML transport codec (DJI Universal Markup Language).

DUML v1 frame (what is exchanged over the AOA bulk channel between DJI Fly and the drone/RC):

    off  size  field
    0    1     magic          = 0x55
    1    2     len(10 bits) + version(6 bits), little-endian:
                 bits  0..9  = full frame length in bytes (including magic and CRC16)
                 bits 10..15 = protocol version (usually 0)
    3    1     header_crc8    = CRC-8 (poly 0x31 refl=0x8C, seed 0x77) over bytes [0..2]
    4    1     sender  (SrcID  = dev_type<<0 ... effectively a 1-byte address)
    5    1     receiver(DstID)
    6    2     seq_num        little-endian
    8    1     cmd_type/flags (bit7=ack request, bit5=encrypted, ...)
    9    1     cmd_set        (CmdSet)
    10   1     cmd_id         (CmdId)
    11   ..    payload
    N-2  2     crc16          (poly 0x1021 refl=0x8408, seed 0x3692) over the whole frame up to CRC16

String from libsdk_jni.so confirming the CRC check on the drone/app side:
    "package crc verify fail, cmdset %d, cmdid 0x%X"

CRC tables are generated from the reflected polynomials — verified against the first
elements of known DJI tables (crc8[1]=0x5e, crc16[1]=0x1189), matches.
"""

from __future__ import annotations
from dataclasses import dataclass, field

MAGIC = 0x55
CRC8_SEED = 0x77
CRC16_SEED = 0x3692


def _gen_table(poly: int, width: int) -> list[int]:
    """Reflected (LSB-first) CRC table of 256 values."""
    mask = (1 << width) - 1
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c >> 1) ^ poly) if (c & 1) else (c >> 1)
            c &= mask
        table.append(c)
    return table


_CRC8_TAB = _gen_table(0x8C, 8)      # refl(0x31) — DJI header CRC8
_CRC16_TAB = _gen_table(0x8408, 16)  # refl(0x1021) — DJI frame CRC16


def crc8(data: bytes, seed: int = CRC8_SEED) -> int:
    c = seed
    for b in data:
        c = _CRC8_TAB[(c ^ b) & 0xFF]
    return c & 0xFF


def crc16(data: bytes, seed: int = CRC16_SEED) -> int:
    c = seed
    for b in data:
        c = ((c >> 8) ^ _CRC16_TAB[(c ^ b) & 0xFF]) & 0xFFFF
    return c & 0xFFFF


class DumlError(Exception):
    pass


@dataclass
class DumlPacket:
    sender: int
    receiver: int
    cmd_set: int
    cmd_id: int
    payload: bytes = b""
    seq: int = 0
    cmd_type: int = 0x00      # 0x40 = ACK required; 0x00 = normal
    version: int = 1          # real DJI frames use version=1 (verified against a dump)

    def encode(self) -> bytes:
        body = bytes([
            self.sender & 0xFF,
            self.receiver & 0xFF,
            self.seq & 0xFF, (self.seq >> 8) & 0xFF,
            self.cmd_type & 0xFF,
            self.cmd_set & 0xFF,
            self.cmd_id & 0xFF,
        ]) + bytes(self.payload)

        total = 1 + 2 + 1 + len(body) + 2   # magic + len2 + crc8 + body + crc16
        if total > 0x3FF:
            raise DumlError(f"frame too long: {total} bytes (max 1023)")

        len_ver = (total & 0x3FF) | ((self.version & 0x3F) << 10)
        header = bytes([MAGIC, len_ver & 0xFF, (len_ver >> 8) & 0xFF])
        header += bytes([crc8(header)])
        frame = header + body
        frame += crc16(frame).to_bytes(2, "little")
        return frame

    @classmethod
    def decode(cls, frame: bytes) -> "DumlPacket":
        if len(frame) < 13:
            raise DumlError(f"frame shorter than minimum: {len(frame)} bytes")
        if frame[0] != MAGIC:
            raise DumlError(f"invalid magic: {frame[0]:#04x}")
        len_ver = frame[1] | (frame[2] << 8)
        total = len_ver & 0x3FF
        version = (len_ver >> 10) & 0x3F
        if crc8(frame[0:3]) != frame[3]:
            raise DumlError("header CRC8 mismatch")
        if total != len(frame):
            raise DumlError(f"length in header {total} != actual {len(frame)}")
        if crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
            raise DumlError("frame CRC16 mismatch (package crc verify fail)")
        return cls(
            sender=frame[4], receiver=frame[5],
            seq=frame[6] | (frame[7] << 8),
            cmd_type=frame[8], cmd_set=frame[9], cmd_id=frame[10],
            payload=frame[11:-2], version=version,
        )

    def __str__(self) -> str:
        return (f"DUML seq={self.seq} {self.sender:#04x}->{self.receiver:#04x} "
                f"set={self.cmd_set:#04x} id={self.cmd_id:#04x} "
                f"type={self.cmd_type:#04x} len={len(self.payload)} "
                f"data={self.payload.hex()}")


class DumlStream:
    """Accumulator of bytes from the bulk channel -> whole DUML frames.

    AOA does not guarantee that one bulk-read = one frame, so we cut the stream
    by magic 0x55 and the length field from the header.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[DumlPacket]:
        self._buf += data
        out: list[DumlPacket] = []
        while True:
            # discard garbage up to the nearest magic
            start = self._buf.find(MAGIC)
            if start < 0:
                self._buf.clear()
                break
            if start > 0:
                del self._buf[:start]
            if len(self._buf) < 4:
                break
            if crc8(bytes(self._buf[0:3])) != self._buf[3]:
                # false magic — shift by 1 byte
                del self._buf[0]
                continue
            total = (self._buf[1] | (self._buf[2] << 8)) & 0x3FF
            if total < 13 or total > 0x3FF:
                del self._buf[0]
                continue
            if len(self._buf) < total:
                break  # wait for the rest of the frame
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            try:
                out.append(DumlPacket.decode(frame))
            except DumlError:
                pass  # corrupt frame — skip
        return out
