import unittest

from robot_servers.robotiq_rs485_gripper_server import (
    RobotiqRS485GripperServer,
    decode_status_response,
    modbus_crc,
)


def status_reply(status_byte=0x31, fault=0, requested=0, actual=3, current=0):
    body = bytes((9, 3, 6, status_byte, 0, fault, requested, actual, current))
    return body + modbus_crc(body)


def write_reply():
    body = bytes.fromhex("09 10 03 e8 00 03")
    return body + modbus_crc(body)


class FakeSerial:
    def __init__(self, responses):
        self.responses = list(responses)
        self.writes = []
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass

    def read(self, _length):
        return self.responses.pop(0)

    def close(self):
        self.closed = True


class RS485Backend(unittest.TestCase):
    def test_constructor_is_read_only(self):
        serial = FakeSerial([status_reply(fault=9)])
        backend = RobotiqRS485GripperServer(serial_port=serial)
        self.assertEqual(len(serial.writes), 1)
        self.assertEqual(serial.writes[0].hex(), "090307d00003040e")
        self.assertEqual(backend.last_status["gFLT"], 9)
        self.assertAlmostEqual(backend.gripper_pos, 1 - 3 / 255)

    def test_explicit_move_writes_then_reads(self):
        serial = FakeSerial([status_reply(), write_reply(), status_reply(actual=100)])
        backend = RobotiqRS485GripperServer(serial_port=serial)
        status = backend.move(100)
        self.assertEqual(len(serial.writes), 3)
        self.assertEqual(serial.writes[1][1], 0x10)
        self.assertEqual(serial.writes[1][10], 100)
        self.assertEqual(status["gPO"], 100)

    def test_bad_crc_is_rejected(self):
        response = status_reply()[:-2] + b"\x00\x00"
        with self.assertRaises(IOError):
            decode_status_response(response)

    def test_shutdown_only_closes_port(self):
        serial = FakeSerial([status_reply()])
        backend = RobotiqRS485GripperServer(serial_port=serial)
        backend.shutdown()
        self.assertTrue(serial.closed)
        self.assertEqual(len(serial.writes), 1)

    def test_get_position_refreshes_read_only_status(self):
        serial = FakeSerial([status_reply(actual=3), status_reply(actual=128)])
        backend = RobotiqRS485GripperServer(serial_port=serial)
        position = backend.get_position()
        self.assertAlmostEqual(position, 1 - 128 / 255)
        self.assertEqual(len(serial.writes), 2)
        self.assertTrue(all(request[1] == 0x03 for request in serial.writes))


if __name__ == "__main__":
    unittest.main()
