#!/usr/bin/env python3
"""Ground-only harness for testing one explicit WM160 SoftSwitchMode hypothesis."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass

from drone import DEV_APP
from duml import DumlPacket, DumlStream
from telemetry import Telemetry
from transport import SerialTransport


CMD_IDS = {0x06, 0x11, 0x19}
RECEIVERS = {0x02, 0x06}
GEAR_PAYLOADS = {
    "sport": bytes.fromhex("00000000"),
    "normal": bytes.fromhex("01000000"),
    "cine": bytes.fromhex("02000000"),
}
MAX_OSD_AGE_S = 2.0


def parse_byte(value: str) -> int:
    parsed = int(value, 0)
    if not 0 <= parsed <= 0xFF:
        raise argparse.ArgumentTypeError("must be a byte")
    return parsed


def build_probe(receiver: int, cmd_id: int, mode: str, seq: int = 1) -> DumlPacket:
    if receiver not in RECEIVERS:
        raise ValueError(f"receiver must be one of {sorted(RECEIVERS)}")
    if cmd_id not in CMD_IDS:
        raise ValueError(f"cmd_id must be one of {sorted(CMD_IDS)}")
    return DumlPacket(sender=DEV_APP, receiver=receiver, cmd_set=0x06, cmd_id=cmd_id,
                      seq=seq, cmd_type=0x40, payload=GEAR_PAYLOADS[mode])


@dataclass(frozen=True)
class GroundSnapshot:
    received_at: float
    motors_on: bool | None
    is_flying: bool | None
    altitude_m: float | None
    flight_mode_name: str | None


def ground_state_is_fresh(snapshot: GroundSnapshot | None, now: float | None = None) -> bool:
    if snapshot is None or (now or time.monotonic()) - snapshot.received_at > MAX_OSD_AGE_S:
        return False
    return (snapshot.motors_on is False and snapshot.is_flying is False
            and snapshot.altitude_m is not None and -0.5 <= snapshot.altitude_m <= 0.5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("mode", choices=GEAR_PAYLOADS)
    parser.add_argument("--receiver", required=True, type=parse_byte)
    parser.add_argument("--cmd-id", required=True, type=parse_byte)
    parser.add_argument("--send", action="store_true",
                        help="permit transmission after interactive confirmation")
    args = parser.parse_args()

    try:
        packet = build_probe(args.receiver, args.cmd_id, args.mode)
    except ValueError as exc:
        parser.error(str(exc))
    frame = packet.encode()
    print(f"candidate: receiver={packet.receiver:#04x} cmd=0x06/{packet.cmd_id:#04x} "
          f"mode={args.mode} payload={packet.payload.hex()}")
    print(f"frame: {frame.hex()}")
    if not args.send:
        print("preview only; add --send to enable the guarded hardware step")
        return 0

    transport = SerialTransport(args.port)
    stream = DumlStream()
    lock = threading.Lock()
    ground_snapshot = None
    packets = []
    running = True
    rx_error = None

    def receive():
        nonlocal ground_snapshot, rx_error
        try:
            while running:
                for incoming in stream.feed(transport.recv(timeout_ms=200)):
                    received_at = time.monotonic()
                    snapshot = None
                    if (incoming.cmd_set, incoming.cmd_id) in ((0x03, 0x43), (0x09, 0x01)):
                        packet_telemetry = Telemetry()
                        packet_telemetry.feed_packet(incoming)
                        state = packet_telemetry.state
                        snapshot = GroundSnapshot(received_at, state.motors_on, state.is_flying,
                                                  state.altitude_m, state.flight_mode_name)
                    with lock:
                        packets.append((received_at, incoming))
                        if snapshot is not None:
                            ground_snapshot = snapshot
        except Exception as exc:
            with lock:
                rx_error = exc
                ground_snapshot = None

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with lock:
                safe = rx_error is None and ground_state_is_fresh(ground_snapshot)
            if safe:
                break
            time.sleep(0.1)
        else:
            print("blocked: no fresh telemetry proving motors off and aircraft on ground")
            return 2

        with lock:
            before = ground_snapshot.flight_mode_name
        expected = f"SEND {packet.receiver:#04x} {packet.cmd_id:#04x} {packet.payload.hex()}"
        print(f"FLYC_STATE before: {before}")
        if input(f"type exactly '{expected}' to transmit: ").strip() != expected:
            print("cancelled")
            return 3
        with lock:
            if rx_error is not None or not ground_state_is_fresh(ground_snapshot):
                print("blocked: receive path failed or ground telemetry became stale")
                return 2
            packet.seq = int(time.monotonic() * 1000) & 0xFFFF
            sent_at = time.monotonic()
            transport.send(packet.encode())

        deadline = sent_at + 3.0
        after = before
        ack = None
        while time.monotonic() < deadline:
            with lock:
                if ground_snapshot is not None and ground_snapshot.received_at > sent_at:
                    after = ground_snapshot.flight_mode_name
                ack = next((p for received_at, p in packets if received_at > sent_at and
                            p.seq == packet.seq and p.cmd_set == packet.cmd_set and
                            p.cmd_id == packet.cmd_id and p.sender == packet.receiver and
                            p.receiver == packet.sender and (p.cmd_type & 0x80)), None)
            if ack is not None and after != before:
                break
            time.sleep(0.05)
        print(f"result: ACK={None if ack is None else ack.payload.hex()} "
              f"FLYC_STATE={before}->{after}")
        return 0 if ack is not None or after != before else 4
    finally:
        running = False
        thread.join(timeout=0.5)
        transport.close()


if __name__ == "__main__":
    sys.exit(main())
