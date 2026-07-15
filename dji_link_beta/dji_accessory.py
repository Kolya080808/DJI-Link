#!/usr/bin/env python3
"""
DJI LogicLink — BETA accessory emulator.

What it does:
  1. Finds a plugged-in Android phone, switches it into AOA accessory mode,
     presenting itself as DJI/com.dji.logiclink -> Android launches DJI Fly.
  2. Opens the bulk channel and listens to DUML traffic from the app, logging frames.
  3. Answers the most basic DUML requests so the app sees a "device"
     (stubs — the real payloads still need to be reverse-engineered from libsdk_jni.so).

This is a beta for a "rough check": the goal is to see that DJI Fly reacts to
our accessory and that the DUML codec sends/receives valid frames.

Modes:
  python3 dji_accessory.py --selftest   # test the DUML codec without hardware
  python3 dji_accessory.py --scan       # show USB candidates and their AOA version
  python3 dji_accessory.py              # full run (needs a phone + pyusb)
  python3 dji_accessory.py --model WM160
"""

from __future__ import annotations
import argparse
import sys
import time

from duml import DumlPacket, DumlStream, crc8, crc16
import aoa

# DUML roles (device addresses). Approximate values, from public DUML dumps.
DEV_APP = 0x0a      # mobile app / PC
DEV_RC = 0x02       # remote controller
DEV_FC = 0x03       # flight controller

# A couple of the most common startup requests from the app:
CMDSET_COMMON = 0x00
CMDID_GET_VERSION = 0x01     # request device version/type


def hexdump(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


# ---------------------------------------------------------------------------
# self-test: exercise the DUML codec without USB
# ---------------------------------------------------------------------------
def selftest() -> int:
    ok = True

    # 1. CRC tables match the known DJI values
    from duml import _CRC8_TAB, _CRC16_TAB
    assert _CRC8_TAB[1] == 0x5e, f"crc8[1]={_CRC8_TAB[1]:#x}"
    assert _CRC16_TAB[1] == 0x1189, f"crc16[1]={_CRC16_TAB[1]:#x}"
    print(f"[ok] CRC tables: crc8[1]=0x5e  crc16[1]=0x1189")

    # 2. Header of correct length and header CRC8
    p = DumlPacket(sender=DEV_APP, receiver=DEV_FC,
                   cmd_set=CMDSET_COMMON, cmd_id=CMDID_GET_VERSION,
                   seq=1, cmd_type=0x40, payload=b"")
    frame = p.encode()
    print(f"[..] encode GetVersion -> {hexdump(frame)}")
    assert frame[0] == 0x55, "magic"
    assert frame[3] == crc8(frame[0:3]), "header crc8"
    # compare with the real DJI GetVersion header (len=13, version=1): 55 0d 04 33
    assert frame[:4] == bytes.fromhex("550d0433"), f"header={hexdump(frame[:4])}"
    print("[ok] header == real DJI GetVersion: 55 0d 04 33")
    assert (frame[1] | (frame[2] << 8)) & 0x3FF == len(frame), "len field"
    assert crc16(frame[:-2]) == int.from_bytes(frame[-2:], "little"), "crc16"
    print(f"[ok] header/len/crc8/crc16 consistent, length {len(frame)}")

    # 3. Roundtrip encode->decode with a non-empty payload
    p2 = DumlPacket(sender=DEV_FC, receiver=DEV_APP,
                    cmd_set=0x0e, cmd_id=0x01, seq=0x1234,
                    cmd_type=0x80, payload=bytes(range(20)))
    d2 = DumlPacket.decode(p2.encode())
    for fld in ("sender", "receiver", "cmd_set", "cmd_id", "seq", "cmd_type", "payload"):
        a, b = getattr(p2, fld), getattr(d2, fld)
        assert a == b, f"roundtrip {fld}: {a!r} != {b!r}"
    print("[ok] encode->decode roundtrip matched on all fields")

    # 4. Streaming parse: two frames + garbage in one buffer
    stream = DumlStream()
    blob = b"\xde\xad" + p.encode() + b"\x00" + p2.encode()
    pkts = stream.feed(blob)
    assert len(pkts) == 2, f"expected 2 frames, got {len(pkts)}"
    print(f"[ok] DumlStream extracted 2 frames from a noisy buffer")

    # 5. CRC corruption is caught
    bad = bytearray(p2.encode())
    bad[-1] ^= 0xFF
    try:
        DumlPacket.decode(bytes(bad))
        ok = False
        print("[FAIL] corrupted CRC16 not caught")
    except Exception:
        print("[ok] corrupted CRC16 rejected (as 'package crc verify fail')")

    # 6. Keyboard -> sticks -> valid DUML flight frame
    from control import keys_to_sticks, build_flight_frame
    s_fwd = keys_to_sticks({"w"})
    assert s_fwd.pitch == 1.0 and s_fwd.roll == 0.0, "W should give pitch=+1"
    s_up = keys_to_sticks({"space", "shift"})
    assert s_up.throttle == 0.0, "Space+Shift cancel out -> throttle 0"
    s_diag = keys_to_sticks({"w", "d", "space", "e"})
    fr = build_flight_frame(s_diag, seq=7)
    DumlPacket.decode(fr)   # raises if the frame is invalid
    print(f"[ok] WASD->sticks->DUML: W+D+Space+E -> {hexdump(fr)}")

    # 7. Full stack via LogTransport + Drone API (without hardware)
    from transport import LogTransport
    from drone import Drone
    lt = LogTransport(verbose=False)
    drn = Drone(lt)
    drn.set_sticks(1.0, 0.5, -0.5, 1.0)
    drn.takeoff()
    drn.take_photo()
    assert len(lt.sent) == 3, f"expected 3 frames, sent {len(lt.sent)}"
    for f in lt.sent:
        DumlPacket.decode(f)   # each frame is valid
    print(f"[ok] Drone API -> 3 valid DUML frames through the transport")

    print("\nSELFTEST:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def keyboard(model: str, connect: bool, pi: str | None, serial_port: str | None) -> int:
    """Game-style keyboard control.
      no flags     -> loopback (LogTransport): see frames without hardware
      --pi HOST    -> via the bridge on the Pi Zero (pi/bridge.py) to the remote controller -> drone
      --connect    -> direct AOA to the phone (stack demo, not the path to the drone)"""
    from transport import LogTransport
    from drone import Drone
    import control

    if serial_port:
        from transport import SerialTransport
        try:
            t = SerialTransport(serial_port)
            print(f"[+] opened serial {serial_port}")
        except Exception as e:
            print(f"[!] failed to open {serial_port} ({e})")
            return 2
    elif pi:
        host, _, p = pi.partition(":")
        from transport import NetTransport
        try:
            t = NetTransport(host, int(p) if p else 9910)
            print(f"[+] connected to the Pi bridge {host}:{p or 9910}")
        except Exception as e:
            print(f"[!] failed to connect to the Pi ({e})")
            return 2
    elif connect:
        try:
            from transport import AoaTransport
            t = AoaTransport.connect(model)
            print("[+] connected to the device via AOA")
        except Exception as e:
            print(f"[!] failed to connect ({e}); falling back to loopback log")
            t = LogTransport()
    else:
        print("[*] loopback mode (without drone): DUML frames are printed to the console")
        t = LogTransport()

    drn = Drone(t)
    drn.start_rx()
    try:
        control.run_keyboard(drn)
    except RuntimeError as e:
        print(e)
        return 2
    finally:
        drn.stop()
    return 0


# ---------------------------------------------------------------------------
# scan: enumerate USB devices and their AOA version
# ---------------------------------------------------------------------------
def scan() -> int:
    try:
        devs = aoa.find_candidate_devices()
    except aoa.AoaError as e:
        print(e)
        return 2
    if not devs:
        print("no USB devices found (is the phone plugged in? do you have permissions?)")
        return 1
    print(f"{'VID:PID':>12}  {'AOA':>4}  description")
    for d in devs:
        proto = aoa.get_protocol(d)
        try:
            name = f"{d.manufacturer or '?'} {d.product or ''}".strip()
        except Exception:
            name = "?"
        flag = f"v{proto}" if proto else "no"
        print(f"  {d.idVendor:04x}:{d.idProduct:04x}  {flag:>4}  {name}")
    return 0


# ---------------------------------------------------------------------------
# basic DUML responder (stubs)
# ---------------------------------------------------------------------------
def handle_packet(pkt: DumlPacket) -> DumlPacket | None:
    """Minimal responses so the app doesn't consider the channel dead.
    NOTE: the payloads are stubs; the real values must be pulled from
    libsdk_jni.so / dumps of a real remote controller."""
    # Answer only packets that need an ACK (bit6/bit7 in cmd_type).
    if not (pkt.cmd_type & 0x40) and not (pkt.cmd_type & 0x80):
        return None
    resp_payload = b"\x00"          # ret_code = 0 (OK) — universal stub
    return DumlPacket(
        sender=pkt.receiver, receiver=pkt.sender,
        cmd_set=pkt.cmd_set, cmd_id=pkt.cmd_id,
        seq=pkt.seq,
        cmd_type=0x80,              # response
        payload=resp_payload,
    )


def run(model: str, timeout_ms: int) -> int:
    try:
        identity = dict(aoa.DJI_IDENTITY)
        identity[1] = model
        print(f"[*] identity: manufacturer='{identity[0]}' model='{identity[1]}'")

        candidates = aoa.find_candidate_devices()
        target = None
        for d in candidates:
            if aoa.get_protocol(d) >= 1:
                target = d
                break
        if target is None:
            print("[!] didn't find a device with AOA support. Check:")
            print("    - the phone is plugged into a HOST/OTG port, screen unlocked")
            print("    - USB permissions (udev rule or sudo)")
            print("    - `python3 dji_accessory.py --scan`")
            return 1

        print(f"[*] AOA handshake with {target.idVendor:04x}:{target.idProduct:04x} ...")
        proto = aoa.switch_to_accessory(target, identity)
        print(f"[+] START sent (AOA v{proto}). Waiting for accessory mode...")

        dev, ep_in, ep_out = aoa.open_accessory()
        print(f"[+] Channel open. IN=0x{ep_in.bEndpointAddress:02x} "
              f"OUT=0x{ep_out.bEndpointAddress:02x}")
        print("[*] Listening to DUML. Ctrl-C to exit.\n")

        stream = DumlStream()
        while True:
            try:
                data = dev.read(ep_in.bEndpointAddress, ep_in.wMaxPacketSize,
                                timeout=timeout_ms)
            except Exception as e:
                if "timeout" in str(e).lower():
                    continue
                raise
            if not data:
                continue
            for pkt in stream.feed(bytes(data)):
                print(f"  <- {pkt}")
                resp = handle_packet(pkt)
                if resp is not None:
                    frame = resp.encode()
                    dev.write(ep_out.bEndpointAddress, frame, timeout=timeout_ms)
                    print(f"  -> {resp}")
    except aoa.AoaError as e:
        print(f"[!] {e}")
        return 2
    except KeyboardInterrupt:
        print("\n[*] exit")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DJI LogicLink accessory (beta)")
    ap.add_argument("--selftest", action="store_true",
                    help="test the DUML codec without hardware")
    ap.add_argument("--scan", action="store_true",
                    help="show USB devices and their AOA version")
    ap.add_argument("--keyboard", action="store_true",
                    help="game-style keyboard control (WASD/Space/Shift)")
    ap.add_argument("--connect", action="store_true",
                    help="with --keyboard: direct AOA to the phone (stack demo)")
    ap.add_argument("--pi", default=None, metavar="HOST[:PORT]",
                    help="with --keyboard: address of the bridge on the Pi Zero (pi/bridge.py)")
    ap.add_argument("--serial", default=None, metavar="PORT",
                    help="with --keyboard: remote controller USB Virtual COM (COM4 / /dev/ttyACM0)")
    ap.add_argument("--model", default=aoa.DJI_IDENTITY[1],
                    help="AOA model (com.dji.logiclink | WM160 | com.dji.link)")
    ap.add_argument("--timeout", type=int, default=1000,
                    help="USB read/write timeout, ms")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.scan:
        return scan()
    if args.keyboard:
        return keyboard(args.model, args.connect, args.pi, args.serial)
    return run(args.model, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
