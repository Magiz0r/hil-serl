"""Direct Modbus RTU backend for a Robotiq 2F-85 gripper.

Constructing this class performs one status-register read and no control-register
write.  Activation and motion remain explicit method calls from the HTTP server.
"""

import threading

from robot_servers.gripper_server import GripperServer


def modbus_crc(data):
    """Return the little-endian Modbus RTU CRC for *data*."""
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc.to_bytes(2, "little")


def decode_status_response(response, slave_id=9):
    """Decode the six Robotiq input bytes from an FC03 response."""
    if len(response) < 5 or modbus_crc(response[:-2]) != response[-2:]:
        raise IOError("Missing/truncated Robotiq response or CRC mismatch")
    if response[0] != slave_id:
        raise IOError("Unexpected Robotiq slave ID")
    if response[1] == 0x83:
        raise IOError(f"Robotiq Modbus exception {response[2]}")
    if len(response) != 11 or response[1:3] != bytes((0x03, 0x06)):
        raise IOError("Unexpected Robotiq function, byte count, or response length")

    data = response[3:9]
    return {
        "gACT": data[0] & 0x01,
        "gGTO": (data[0] >> 3) & 0x01,
        "gSTA": (data[0] >> 4) & 0x03,
        "gOBJ": (data[0] >> 6) & 0x03,
        "gFLT": data[2],
        "gPR": data[3],
        "gPO": data[4],
        "gCU": data[5],
    }


class RobotiqRS485GripperServer(GripperServer):
    """Robotiq 2F-85 backend using its USB/RS485 Modbus RTU interface."""

    def __init__(
        self,
        device="/dev/ttyUSB0",
        baudrate=115200,
        timeout=0.3,
        slave_id=9,
        serial_port=None,
    ):
        super().__init__()
        self.slave_id = slave_id
        self._lock = threading.Lock()
        self._command = {
            "rACT": 0,
            "rGTO": 0,
            "rATR": 0,
            "rPR": 0,
            "rSP": 255,
            "rFR": 30,
        }

        if serial_port is None:
            import fcntl
            import serial
            import termios

            serial_port = serial.Serial(
                device,
                baudrate=baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=timeout,
                write_timeout=timeout,
                exclusive=True,
            )
            try:
                fcntl.ioctl(serial_port.fileno(), termios.TIOCEXCL)
            except Exception:
                serial_port.close()
                raise
        self._serial = serial_port
        try:
            self.last_status = self.read_status()
        except Exception:
            self._serial.close()
            raise

    def _request(self, body, response_length):
        request = body + modbus_crc(body)
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(request)
            self._serial.flush()
            response = self._serial.read(response_length)
        if len(response) < 5 or modbus_crc(response[:-2]) != response[-2:]:
            raise IOError("Missing/truncated Robotiq response or CRC mismatch")
        if response[0] != self.slave_id:
            raise IOError("Unexpected Robotiq slave ID")
        return response

    def read_status(self):
        """Read input state only (FC03); this never changes gripper state."""
        body = bytes((self.slave_id, 0x03, 0x07, 0xD0, 0x00, 0x03))
        status = decode_status_response(self._request(body, 11), self.slave_id)
        self.last_status = status
        self.gripper_pos = 1.0 - status["gPO"] / 255.0
        return status

    def _write_command(self):
        flags = (
            self._command["rACT"]
            | (self._command["rGTO"] << 3)
            | (self._command["rATR"] << 4)
        )
        payload = bytes(
            (
                flags,
                0,
                0,
                self._command["rPR"],
                self._command["rSP"],
                self._command["rFR"],
            )
        )
        body = bytes((self.slave_id, 0x10, 0x03, 0xE8, 0x00, 0x03, 0x06)) + payload
        response = self._request(body, 8)
        if response[1:6] != bytes((0x10, 0x03, 0xE8, 0x00, 0x03)):
            raise IOError("Unexpected Robotiq write acknowledgement")
        return self.read_status()

    @staticmethod
    def _byte(value):
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("Gripper speed/force must be an integer in [0, 255]")
        return value

    def activate_gripper(self, *, speed=255, force=30, go_to=True):
        speed, force = self._byte(speed), self._byte(force)
        if type(go_to) is not bool:
            raise ValueError("go_to must be boolean")
        self._command.update(rACT=1, rGTO=int(go_to), rATR=0, rPR=0,
                             rSP=speed, rFR=force)
        return self._write_command()

    def reset_gripper(self):
        self._command.update(rACT=0, rGTO=0, rATR=0)
        return self._write_command()

    def open(self):
        return self.move(0)

    def close(self):
        return self.move(255)

    def close_slow(self):
        self._command["rSP"] = 50
        try:
            return self.move(255)
        finally:
            self._command["rSP"] = 255

    def move(self, position, *, speed=None, force=None):
        updates = {}
        if speed is not None:
            updates['rSP'] = self._byte(speed)
        if force is not None:
            updates['rFR'] = self._byte(force)
        self._command.update(updates)
        self._command.update(rACT=1, rGTO=1, rPR=max(0, min(255, int(position))))
        return self._write_command()

    def stop(self):
        """Clear go-to while retaining activation and the last requested position.

        This does not release a held object. Activation itself must be cancelled
        with reset_gripper(), because rGTO does not govern calibration motion.
        """
        self._command.update(rACT=self.last_status['gACT'], rGTO=0, rATR=0,
                             rPR=self.last_status['gPR'])
        return self._write_command()

    def get_position(self):
        self.read_status()
        return self.gripper_pos

    def shutdown(self):
        self._serial.close()
