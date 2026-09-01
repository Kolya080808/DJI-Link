#!/usr/bin/env python3

import unittest

from drone import Drone
from flight_mode_probe import GroundSnapshot, build_probe, ground_state_is_fresh


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

    def test_probe_contract_is_explicit(self):
        packet = build_probe(0x06, 0x11, "sport")
        self.assertEqual((packet.receiver, packet.cmd_set, packet.cmd_id), (0x06, 0x06, 0x11))
        self.assertEqual(packet.payload, bytes.fromhex("00000000"))

    def test_probe_rejects_values_outside_research_set(self):
        with self.assertRaises(ValueError):
            build_probe(0x03, 0x11, "sport")
        with self.assertRaises(ValueError):
            build_probe(0x06, 0x7A, "sport")

    def test_ground_check_fails_closed(self):
        self.assertFalse(ground_state_is_fresh(None, now=10.0))
        ground = GroundSnapshot(9.0, False, False, 0.0, "GPS_Atti")
        self.assertTrue(ground_state_is_fresh(ground, now=10.0))
        self.assertFalse(ground_state_is_fresh(ground, now=12.0))
        self.assertFalse(ground_state_is_fresh(
            GroundSnapshot(9.0, True, False, 0.0, "GPS_Atti"), now=10.0))
        self.assertFalse(ground_state_is_fresh(
            GroundSnapshot(9.0, False, False, -1.0, "GPS_Atti"), now=10.0))


if __name__ == "__main__":
    unittest.main()
