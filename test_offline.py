"""Offline protocol and archive regression checks: python3 -m unittest -v."""
import json
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from spectrum import fields, floating, number, tlv, append, read_since, BIN_DATA
import spectrum
from capture_spectrum import parser


class Tests(unittest.TestCase):
    def test_capture_windows(self):
        for window, wire_id in spectrum.WINDOW_TYPES.items():
            with self.subTest(window=window):
                args = parser().parse_args(['--window', window, '--min-mhz', '1',
                                            '--max-mhz', '1.009', '--duration', '1', '--settle', '0'])
                clock = [0.0]
                status = {}

                class FakeRadio:
                    def __init__(self, *args):
                        pass

                    def close(self):
                        pass

                    def request(self, settings=b''):
                        if settings:
                            status.update(spectrum.fields(b'\x01' + settings + b'\0'))
                        status.update({spectrum.RF_LEVEL_CAL: struct.pack('>f', 1.4),
                                       spectrum.IF_POWER: struct.pack('>f', -20+clock[0]),
                                       spectrum.RF_ATTEN: struct.pack('>f', 10),
                                       spectrum.RF_GAIN: struct.pack('>f', 20+clock[0]),
                                       spectrum.FIRST_LO_FREQUENCY: struct.pack('>d', 0),
                                       spectrum.FE_LOW_EDGE: struct.pack('>f', 0),
                                       spectrum.FE_HIGH_EDGE: struct.pack('>f', 30e6),
                                       spectrum.NOISE_BW: struct.pack('>f', 4500),
                                       spectrum.BIN_DATA: np.ones(int.from_bytes(status[spectrum.BIN_COUNT], 'big'), dtype='>f4').tobytes()})
                        return status, '127.0.0.1'

                def sleep(seconds):
                    clock[0] += seconds

                with patch.object(spectrum, 'Radio', FakeRadio), \
                     patch.object(spectrum.time, 'monotonic', side_effect=lambda: clock[0]), \
                     patch.object(spectrum.time, 'time', side_effect=lambda: 100+clock[0]), \
                     patch.object(spectrum.time, 'sleep', side_effect=sleep):
                    result = spectrum.capture(args)
                self.assertEqual(int.from_bytes(status[spectrum.WINDOW_TYPE], 'big'), wire_id)
                self.assertEqual(result[3]['window'], window)
                self.assertEqual(result[3]['noise_bw_hz'], 4500)
                receiver = result[3]['receiver_samples']
                self.assertEqual(len(receiver), result[2])
                self.assertEqual(receiver[0], {'elapsed_seconds': 0, 'ad_level_dbfs': -20,
                                               'attenuation_db': 10, 'vga_gain_db': 20})
                self.assertEqual(receiver[1]['ad_level_dbfs'], -19.5)
                self.assertEqual(receiver[1]['vga_gain_db'], 20.5)
                with tempfile.TemporaryDirectory() as directory:
                    path = str(Path(directory)/'receiver.sqlite3')
                    append(path, result)
                    self.assertEqual(list(read_since(path, 0))[0][3], result[3])
                if window == 'kaiser':
                    self.assertEqual(number(status[spectrum.SPECTRUM_SHAPE]), 7.0)
                    self.assertEqual(result[3]['kaiser_beta'], 7.0)
        self.assertEqual(parser().parse_args([]).window, 'kaiser')

    def test_protocol(self):
        vector = np.arange(10000, dtype='>f4').tobytes()
        self.assertEqual(fields(b'\0'+tlv(BIN_DATA, vector)+b'\0')[BIN_DATA], vector)
        self.assertEqual(number(b''), 0)
        self.assertEqual(number(struct.pack('>f', -123.5)), -123.5)
        self.assertEqual(number(b'\x80\0\0'), struct.unpack('>f', b'\0\x80\0\0')[0])
        for packet in (b'\0\x01', b'\0\x01\x82\xff', b'\0\x01\x03\x00'):
            with self.assertRaises(ValueError):
                fields(packet)

    def test_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'archive.sqlite3')
            median = np.array([-120, -100], dtype=np.float32)
            for t in (100, 200, 300):
                append(path, (t, 20, 40, {'bins': 2}, median, median+10))
            rows = list(read_since(path, 200))
            self.assertEqual([r[0] for r in rows], [200, 300])
            np.testing.assert_array_equal(rows[0][4], median)
            with sqlite3.connect(path) as db:
                plan = str(db.execute('EXPLAIN QUERY PLAN SELECT * FROM captures WHERE started>=? ORDER BY started', (200,)).fetchall())
                self.assertIn('captures_started', plan)
                db.execute('DELETE FROM captures WHERE started < 200')
            self.assertEqual(len(list(read_since(path, 0))), 2)


if __name__ == '__main__':
    unittest.main()
