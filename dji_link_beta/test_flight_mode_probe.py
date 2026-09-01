#!/usr/bin/env python3

import unittest

from drone import Drone
from duml import DumlPacket
from pc_client import decode_flight_mode_capture, validate_flight_mode_captures


class CaptureTransport:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(frame)


class FlightModeProbeTest(unittest.TestCase):
    def test_named_mode_does_not_send_unconfirmed_frame(self):
        transport = CaptureTransport()
        drone = Drone(transport)
        with self.assertRaises(NotImplementedError):
            drone.set_flight_mode("sport")
        self.assertEqual(transport.frames, [])

    def test_capture_is_replayed_without_guessing_fields(self):
        captured = DumlPacket(sender=0x02, receiver=0x06, cmd_set=0x06, cmd_id=0xA4,
                              seq=123, cmd_type=0x40, payload=bytes.fromhex("010203")).encode()
        packet = decode_flight_mode_capture(captured.hex())
        self.assertEqual((packet.receiver, packet.cmd_set, packet.cmd_id), (0x06, 0x06, 0xA4))
        self.assertEqual(packet.payload, bytes.fromhex("010203"))

    def test_capture_rejects_non_app_request(self):
        response = DumlPacket(sender=0x06, receiver=0x02, cmd_set=0x06, cmd_id=0xA4,
                              seq=123, cmd_type=0x80, payload=b"\x00").encode()
        with self.assertRaises(ValueError):
            decode_flight_mode_capture(response.hex())

    def test_three_captures_must_share_route_and_differ_in_payload(self):
        def packet(payload, cmd_id=0xA4):
            return DumlPacket(sender=0x02, receiver=0x06, cmd_set=0x06, cmd_id=cmd_id,
                              cmd_type=0x40, payload=bytes([payload]))

        validate_flight_mode_captures({"cine": packet(0), "normal": packet(1),
                                       "sport": packet(2)})
        with self.assertRaises(ValueError):
            validate_flight_mode_captures({"cine": packet(0), "normal": packet(1)})
        with self.assertRaises(ValueError):
            validate_flight_mode_captures({"cine": packet(0), "normal": packet(1),
                                           "sport": packet(2, cmd_id=0xA5)})


if __name__ == "__main__":
    unittest.main()
