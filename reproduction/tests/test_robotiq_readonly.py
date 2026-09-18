import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1] / 'probe_robotiq_readonly.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class ReadOnlyProtocol(unittest.TestCase):
    def test_fixed_read_request_crc(self):
        self.assertEqual(probe.REQUEST.hex(), '090307d00003040e')
    def test_decode_and_reject_corruption(self):
        body = bytes.fromhex('09 03 06 f9 00 00 80 7f 02')
        reply = body + probe.crc16(body)
        result = probe.decode(reply)
        self.assertEqual(result['gSTA'], 3)
        self.assertEqual(result['actual_position_raw'], 127)
        for bad in [reply[:-1], reply[:-2] + b'\x00\x00', b'']:
            with self.assertRaises(ValueError):
                probe.decode(bad)
    def test_exception_response(self):
        body = bytes.fromhex('09 83 02')
        with self.assertRaisesRegex(ValueError, 'exception 2'):
            probe.decode(body + probe.crc16(body))
