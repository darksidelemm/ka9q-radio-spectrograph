"""Offline plot checks: python -m unittest -v test_plot_spectra."""
import argparse
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from spectrum import append
from plot_spectra import load_window, render


class PlotTests(unittest.TestCase):
    def test_window_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp)/'capture ?#.sqlite3'
            bins = 2000
            metadata = {'first_hz': 100000, 'rbw_hz': 3000, 'bins': bins, 'units': 'dBm'}
            values = np.linspace(-130, -40, bins, dtype=np.float32)
            for stamp in (100, 200, 210, 500, 600):
                meta = dict(metadata)
                if stamp in (200, 210):
                    meta['receiver_samples'] = [
                        {'vga_gain_db': 20, 'attenuation_db': 10, 'ad_level_dbfs': -30},
                        {'vga_gain_db': 24, 'attenuation_db': 10, 'ad_level_dbfs': -20}]
                append(str(database), (stamp, 20, 40, meta, values, values+10))
            before = database.read_bytes()
            data, grid, start, end, count, occupied, step, receiver = load_window(database, 400/3600, 600, 'median', 100)
            np.testing.assert_array_equal(receiver[:, 0], [32, -25])
            self.assertTrue(np.all(np.isnan(receiver[:, 1:])))
            self.assertEqual((start, end, count, occupied, step), (200, 600, 3, 2, 100))
            np.testing.assert_array_equal(data[:, 0], values)
            self.assertTrue(np.all(np.isnan(data[:, 1:3])))
            peak = load_window(database, 400/3600, 600, 'peak', 100)[0]
            np.testing.assert_array_equal(peak[:, 0], values+10)
            args = argparse.Namespace(cmap_min=-130, cmap_max=-30, title='Test', hours=400/3600, statistic='median')
            for full, height in ((False, 2200), (True, 2640)):
                output = Path(tmp)/f'plot{full}.png'
                render(output, data, grid, start, end, args, full, receiver=receiver)
                with Image.open(output) as image:
                    self.assertEqual(image.size, (3600, height))
            self.assertEqual(database.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'No captures'):
                load_window(database, .01, 1000, 'median', 120)
            changed = dict(metadata, rbw_hz=6000)
            append(str(database), (550, 20, 40, changed, values, values+10))
            with self.assertRaisesRegex(ValueError, 'changed'):
                load_window(database, 400/3600, 600, 'median', 100)


if __name__ == '__main__':
    unittest.main()
