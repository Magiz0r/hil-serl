import copy
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from franka_env.spacemouse import pyspacemouse


class WirelessBT(unittest.TestCase):
    def test_usb_device_is_recognized_without_opening(self):
        enumeration = SimpleNamespace(find=lambda: [
            SimpleNamespace(vendor_id=0x256F, product_id=0xC63A, path="/dev/hidraw5"),
            SimpleNamespace(vendor_id=0x1234, product_id=0x5678, path="/dev/hidraw6"),
        ])
        with patch.object(pyspacemouse, "Enumeration", return_value=enumeration):
            self.assertEqual(pyspacemouse.list_devices(), ["SpaceMouse Wireless BT"])

    def test_duplicate_collections_open_once_but_distinct_devices_remain(self):
        device = SimpleNamespace(
            vendor_id=0x256F, product_id=0xC63A,
            path="/dev/hidraw5", set_nonblocking=Mock(),
        )
        enumeration = SimpleNamespace(find=lambda: [device, device, device])
        with patch.object(pyspacemouse, "Enumeration", return_value=enumeration), \
             patch.object(pyspacemouse.DeviceSpec, "open") as opened, \
             patch.object(pyspacemouse, "_active_device", None):
            self.assertEqual(pyspacemouse.list_devices(), ["SpaceMouse Wireless BT"])
            result = pyspacemouse.open()
            self.assertIs(result.device, device)
            self.assertEqual(len(pyspacemouse._active_device), 1)
            opened.assert_called_once()
            device.set_nonblocking.assert_called_once_with(True)
        second = SimpleNamespace(vendor_id=0x256F, product_id=0xC63A, path="/dev/hidraw6")
        self.assertEqual(len(list(pyspacemouse._unique_hid_devices([device, device, second]))), 2)

    def test_signed_axis_report_matches_observed_descriptor(self):
        device = copy.deepcopy(pyspacemouse.device_specs["SpaceMouse Wireless BT"])
        device.process(list(bytes([1]) + struct.pack("<6h", 350, -350, 175, -175, 70, -70)))
        state = device.tuple_state
        self.assertEqual((state.x, state.y, state.z), (1.0, 1.0, -0.5))
        self.assertEqual((state.pitch, state.roll, state.yaw), (0.5, -0.2, -0.2))

    def test_two_buttons_press_and_release_independently(self):
        device = copy.deepcopy(pyspacemouse.device_specs["SpaceMouse Wireless BT"])
        for mask, expected in ((1, [1, 0]), (2, [0, 1]), (3, [1, 1]), (0, [0, 0])):
            device.process([3, mask, 0])
            self.assertEqual(device.tuple_state.buttons, expected)


if __name__ == "__main__":
    unittest.main()
