#!/usr/bin/env python3
"""Delete captures older than a UTC retention window."""
import argparse
import math
import sqlite3
import time
from contextlib import closing


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database')
    p.add_argument('--hours', type=float, required=True)
    p.add_argument('--vacuum', action='store_true', help='reclaim disk space; needs extra disk space and locks writers')
    a = p.parse_args()
    if not math.isfinite(a.hours) or a.hours <= 0:
        p.error('--hours must be positive and finite')
    with closing(sqlite3.connect(f'file:{a.database}?mode=rw', uri=True, timeout=30)) as db:
        with db:
            cursor = db.execute('DELETE FROM captures WHERE started < ?', (time.time()-a.hours*3600,))
        print(f'Deleted {cursor.rowcount} captures; remaining pages are reusable.')
        if a.vacuum:
            db.execute('VACUUM')


if __name__ == '__main__':
    main()
