"""One Robotiq Modbus RTU status read; never writes control registers.

Protocol matches the inspected NUC DROID comModbusRtu: 115200 8N1,
slave 9, function 03, holding registers 0x07D0..0x07D2.
No import of robot/gripper clients, activation, reset or retry.
"""
import json
import struct

DEVICE = '/dev/serial/by-id/usb-FTDI_USB_TO_RS-485_DAAQM50H-if00-port0'


def crc16(data):
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return struct.pack('<H', crc)


REQUEST_BODY = bytes.fromhex('09 03 07 d0 00 03')
REQUEST = REQUEST_BODY + crc16(REQUEST_BODY)


def decode(reply):
    if len(reply) < 5 or crc16(reply[:-2]) != reply[-2:]:
        raise ValueError('Missing/truncated response or CRC mismatch')
    if reply[0] != 9:
        raise ValueError('Unexpected slave ID')
    if reply[1] == 0x83:
        raise ValueError(f'Modbus read exception {reply[2]}')
    if len(reply) != 11 or reply[1:3] != bytes([3, 6]):
        raise ValueError('Unexpected function, register count or response length')
    data = reply[3:9]
    return dict(gACT=data[0] & 1, gGTO=(data[0] >> 3) & 1,
                gSTA=(data[0] >> 4) & 3, gOBJ=(data[0] >> 6) & 3,
                fault_byte=data[2], requested_position_raw=data[3],
                actual_position_raw=data[4], current_raw=data[5])


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default=DEVICE)
    args = parser.parse_args()
    import serial
    import fcntl
    import termios
    with serial.Serial(args.device, baudrate=115200, bytesize=8, parity='N',
                       stopbits=1, timeout=0.6, write_timeout=0.6,
                       exclusive=True) as port:
        fcntl.ioctl(port.fileno(), termios.TIOCEXCL)
        # One constant read-only request. No reset_input_buffer or control write.
        port.write(REQUEST)
        port.flush()
        reply = port.read(11)
    print(json.dumps(dict(device=args.device, request_hex=REQUEST.hex(),
                          response_hex=reply.hex(), status=decode(reply)), indent=2))


if __name__ == '__main__':
    main()
