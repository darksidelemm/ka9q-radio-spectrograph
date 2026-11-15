#!/usr/bin/env python3
"""Capture one median/peak spectrum record from radiod."""
import argparse
import fcntl
import math
import sys
import datetime

from spectrum import WINDOW_TYPES, append, capture


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--radio', default='hf.local')
    p.add_argument('--port', type=int, default=5006)
    p.add_argument('--interface', default='0.0.0.0', help='local IPv4 address for multicast')
    p.add_argument('--min-mhz', type=float, default=.1)
    p.add_argument('--max-mhz', type=float, default=30)
    p.add_argument('--rbw', type=float, default=3000, help='bin spacing / rectangular-window RBW in Hz')
    p.add_argument('--window', choices=WINDOW_TYPES, default='kaiser',
                   help='FFT window applied by radiod (default: kaiser, beta=7.0, matching ka9q-web); tapered windows increase noise bandwidth')
    p.add_argument('--duration', type=float, default=20)
    p.add_argument('--interval', type=float, default=.2, help='seconds between spectrum polls')
    p.add_argument('--settle', type=float, default=1)
    p.add_argument('--timeout', type=float, default=3)
    p.add_argument('--offset-db', type=float, default=0, help='additional measured calibration correction')
    p.add_argument('--database', default='spectra.sqlite3')
    return p


def arguments():
    p = parser()
    a = p.parse_args()
    for key in ('min_mhz', 'max_mhz', 'rbw', 'duration', 'interval', 'settle', 'timeout', 'offset_db'):
        if not math.isfinite(getattr(a, key)):
            p.error(f'{key} must be finite')
    if not (0 <= a.min_mhz < a.max_mhz and a.rbw > 0 and a.duration > 0
            and a.interval > 0 and a.settle >= 0 and a.timeout > 0 and 1 <= a.port <= 65535):
        p.error('invalid frequency range, timing, RBW or port')
    return a


def report(result):
    _, duration, count, meta, median, peak = result
    print(f'{count} spectra in {duration:.2f}s; {meta["bins"]} bins, '
          f'{meta["first_hz"]/1e6:.6f}..'
          f'{(meta["first_hz"]+(meta["bins"]-1)*meta["rbw_hz"])/1e6:.6f} MHz; '
          f'{meta["window"]} window'
          f'{" (beta=" + str(meta["kaiser_beta"]) + ")" if "kaiser_beta" in meta else ""}, '
          f'{meta["rbw_hz"]:g} Hz spacing, '
          f'{meta["noise_bw_hz"]:g} Hz noise bandwidth; '
          f'median {median.min():.1f}..{median.max():.1f} dBm; '
          f'peak {peak.min():.1f}..{peak.max():.1f} dBm', flush=True)
    receiver = meta['receiver_samples']
    print(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} - Receiver ({len(receiver)} readings): '
          f'A/D {min(s["ad_level_dbfs"] for s in receiver):.1f}..'
          f'{max(s["ad_level_dbfs"] for s in receiver):.1f} dBFS; '
          f'last attenuation {receiver[-1]["attenuation_db"]:g} dB, '
          f'last VGA gain {receiver[-1]["vga_gain_db"]:g} dB', flush=True)


def main():
    args = arguments()
    # Lock spans capture and append; duplicate cron invocations skip cleanly.
    with open(args.database + '.capture.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another capture is running; skipped.')
            return
        result = capture(args)
        append(args.database, result)
        report(result)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Capture failed: {exc}', file=sys.stderr)
        sys.exit(1)
