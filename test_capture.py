#!/usr/bin/env python3
"""On-server diagnostic: live capture and temporary archive round trip."""
import sys
import tempfile
from pathlib import Path

import numpy as np

from capture_spectrum import arguments, report
from spectrum import append, capture, read_since


def main():
    args = arguments()
    result = capture(args)
    report(result)
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / 'test.sqlite3')
        append(path, result)
        rows = list(read_since(path, result[0]))
        if len(rows) != 1 or rows[0][3] != result[3]:
            raise ValueError('archive metadata round trip failed')
        np.testing.assert_array_equal(rows[0][4], result[4])
        np.testing.assert_array_equal(rows[0][5], result[5])
        if not np.all(rows[0][5] >= rows[0][4]):
            raise ValueError('peak is below median')
        if list(read_since(path, result[0]+1)):
            raise ValueError('archive time selection failed')
    print('PASS: live spectrum and binary archive round trip; no production data written.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, AssertionError) as exc:
        print(f'Test failed: {exc}', file=sys.stderr)
        sys.exit(1)
