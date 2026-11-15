#!/usr/bin/env python3
"""Print UTC coverage and stored signal extrema without modifying the archive."""
import argparse
import math
import sqlite3
import struct
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def summarize(path):
    """Scan a consistent snapshot, keeping just one capture in memory."""
    uri = Path(path).expanduser().resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        db.execute('PRAGMA query_only = ON')
        db.execute('BEGIN')
        summary = {'captures': 0, 'start': math.inf, 'end': -math.inf,
                   'median': [math.inf, -math.inf], 'peak': [math.inf, -math.inf]}
        for capture_id, start, duration, median, peak in db.execute(
                'SELECT id, started, duration, median, peak FROM captures'):
            if not math.isfinite(start) or not math.isfinite(duration) or duration < 0:
                raise ValueError(f'capture {capture_id}: invalid timestamp or duration')
            summary['captures'] += 1
            summary['start'] = min(summary['start'], start)
            summary['end'] = max(summary['end'], start + duration)
            for name, blob in (('median', median), ('peak', peak)):
                if not blob or len(blob) % 4:
                    raise ValueError(f'capture {capture_id}: invalid {name} float32 array')
                for (value,) in struct.iter_unpack('<f', blob):
                    if not math.isfinite(value):
                        raise ValueError(f'capture {capture_id}: non-finite {name} signal level')
                    summary[name][0] = min(summary[name][0], value)
                    summary[name][1] = max(summary[name][1], value)
        return summary


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', ' UTC')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('database', nargs='?', default='spectra.sqlite3')
    args = p.parse_args()
    try:
        result = summarize(args.database)
        print(f'Database: {args.database}')
        print(f'Captures: {result["captures"]:,}')
        if not result['captures']:
            print('No captures stored.')
            return
        print(f'First capture start: {utc(result["start"])}')
        print(f'Last capture end:    {utc(result["end"])}')
        print(f'Time span: {(result["end"]-result["start"])/3600:.3f} hours (includes gaps)')
        for name in ('median', 'peak'):
            low, high = result[name]
            print(f'{name.capitalize()} levels: {low:.2f} to {high:.2f} dBm')
        print(f'Overall levels: {min(result["median"][0], result["peak"][0]):.2f} to '
              f'{max(result["median"][1], result["peak"][1]):.2f} dBm')
    except (sqlite3.Error, OSError, ValueError, OverflowError) as exc:
        print(f'Inspection failed: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
