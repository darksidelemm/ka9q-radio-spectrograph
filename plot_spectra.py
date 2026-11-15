#!/usr/bin/env python3
"""Render compact and full-frequency-resolution PNGs from a spectrum archive."""
import argparse
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def load_window(database, hours, end, statistic, time_bin):
    """Read only the selected indexed time range, one BLOB at a time."""
    start = end - hours*3600
    columns = math.ceil(hours*3600/time_bin)
    # Exactly span the requested window with uniform time cells.
    step = (end-start)/columns
    uri = Path(database).expanduser().resolve().as_uri() + '?mode=ro'
    image = None
    grid = None
    count = 0
    occupied = set()
    receiver = np.full((2, columns), np.nan, dtype=np.float32)
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        db.execute('PRAGMA query_only=ON')
        for timestamp, metadata, blob in db.execute(
                f'SELECT started, metadata, {statistic} FROM captures '
                'WHERE started>=? AND started<? ORDER BY started, id', (start, end)):
            meta = json.loads(metadata)
            current = (meta['first_hz'], meta['rbw_hz'], meta['bins'])
            first, rbw, bins = current
            if (not isinstance(bins, int) or bins < 2 or not math.isfinite(first)
                    or not math.isfinite(rbw) or rbw <= 0 or meta.get('units') != 'dBm'):
                raise ValueError('Invalid frequency grid or units in capture metadata')
            if grid is None:
                grid = current
                image = np.full((bins, columns), np.nan, dtype=np.float32)
            elif current != grid:
                raise ValueError('Frequency range/RBW changed within the selected window; choose a shorter --hours window')
            values = np.frombuffer(blob, dtype='<f4')
            if values.size != bins or not np.all(np.isfinite(values)):
                raise ValueError('Invalid spectrum array in database')
            column = min(columns-1, int((timestamp-start)/step))
            image[:, column] = values
            # Use the same capture and time cell as the spectrogram. Older
            # records have no telemetry; leave gaps instead of inventing values.
            receiver[:, column] = np.nan
            readings = meta.get('receiver_samples', [])
            if readings:
                gains, levels = [], []
                for reading in readings:
                    gain = reading.get('vga_gain_db')
                    attenuation = reading.get('attenuation_db')
                    level = reading.get('ad_level_dbfs')
                    if gain is not None and attenuation is not None and math.isfinite(gain) and math.isfinite(attenuation):
                        gains.append(gain + attenuation)
                    if level is not None and math.isfinite(level):
                        levels.append(level)
                if gains:
                    receiver[0, column] = np.median(gains)
                if levels:
                    receiver[1, column] = np.median(levels)
            occupied.add(column)
            count += 1
    if image is None:
        raise ValueError('No captures in the requested time window; existing PNGs were left in place')
    return image, grid, start, end, count, len(occupied), step, receiver


def render(path, image, grid, start, end, args, high_resolution=False, receiver=None):
    bins = grid[2]
    width = 3600
    base_height = max(1800, bins+240) if high_resolution else 1800
    height = base_height + 400
    spectrum_height = base_height - 240
    dpi = 150
    fig = plt.figure(figsize=(width/dpi, height/dpi), dpi=dpi)
    try:
        # Fixed pixel margins preserve >= one vertical plot pixel per bin in the large PNG.
        ax = fig.add_axes([300/width, 140/height, 2850/width, spectrum_height/height])
        color_ax = fig.add_axes([3250/width, 140/height, 65/width, spectrum_height/height])
        cmap = plt.get_cmap('jet').copy()
        cmap.set_bad('#eeeeee')
        first, rbw, _ = grid
        low = (first-rbw/2)/1e6
        high = (first+(bins-.5)*rbw)/1e6
        dates = mdates.date2num([datetime.fromtimestamp(t, timezone.utc) for t in (start, end)])
        spec = ax.imshow(image, origin='lower', aspect='auto', interpolation='nearest',
                         cmap=cmap, vmin=args.cmap_min, vmax=args.cmap_max,
                         extent=[dates[0], dates[1], low, high])
        ax.set_xlim(dates)
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 6), tz=timezone.utc))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d\n%H:%M', tz=timezone.utc))
        ax.set_xlabel('Time (UTC)')
        ax.set_ylabel('Frequency (MHz)')
        ax.tick_params(axis='y', right=True, labelright=True)
        if high_resolution:
            ax.set_yticks(np.arange(math.ceil(low), math.floor(high) + 1, dtype=float))
            ax.set_ylim(low, high)
            ax.set_axisbelow(False)
            ax.grid(axis='both', linestyle='--', linewidth=0.7, color='black', alpha=0.5)
        telemetry_ax = fig.add_axes([300/width, (base_height-40)/height,
                                     2850/width, 260/height], sharex=ax)
        ad_ax = telemetry_ax.twinx()
        centres = np.linspace(dates[0], dates[1], image.shape[1], endpoint=False)
        centres += (dates[1]-dates[0]) / (2*image.shape[1])
        if receiver is None:
            receiver = np.full((2, image.shape[1]), np.nan)
        gain_line, = telemetry_ax.plot(centres, receiver[0], color='tab:blue',
                                       label='Receiver gain', drawstyle='steps-mid', marker='.', markersize=2)
        ad_line, = ad_ax.plot(centres, receiver[1], color='tab:red',
                              label='A/D level', drawstyle='steps-mid', marker='.', markersize=2)
        telemetry_ax.set_ylabel('Receiver gain (dB)', color='tab:blue')
        ad_ax.set_ylabel('A/D level (dBFS)', color='tab:red')
        telemetry_ax.tick_params(axis='y', labelcolor='tab:blue')
        ad_ax.tick_params(axis='y', labelcolor='tab:red')
        telemetry_ax.tick_params(axis='x', labelbottom=False)
        telemetry_ax.grid(axis='y', linestyle='--', alpha=0.3)
        telemetry_ax.legend(handles=[gain_line, ad_line], loc='upper left', ncol=2)
        if not np.any(np.isfinite(receiver)):
            telemetry_ax.text(.5, .5, 'No receiver telemetry in this window',
                               transform=telemetry_ax.transAxes, ha='center')
        title_ax = telemetry_ax
        title_ax.set_title(f'{args.title} — {args.statistic} spectrum\n'
                     f'{args.hours:g} hours ending {datetime.fromtimestamp(end, timezone.utc):%Y-%m-%d %H:%M UTC}; '
                     f'{rbw:g} Hz bin spacing')
        fig.colorbar(spec, cax=color_ax, label='Power (dBm / bin)')
        # Save alongside the destination and replace atomically for web/cron readers.
        destination = Path(path).expanduser()
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.'+destination.name+'.', suffix='.png', delete=False) as tmp:
            temporary = tmp.name
        try:
            fig.savefig(temporary, format='png', dpi=dpi)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(f'Saved {destination}: {width} x {height} pixels', flush=True)
    finally:
        plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('database', nargs='?', default='spectra.sqlite3')
    p.add_argument('--hours', type=float, default=72)
    p.add_argument('--statistic', choices=('median', 'peak'), default='median')
    p.add_argument('--cmap-min', '--cmap_min', type=float, default=-130)
    p.add_argument('--cmap-max', '--cmap_max', type=float, default=-30)
    p.add_argument('--title', default='ka9q-radio HF')
    p.add_argument('--spectrograph', default='spectrograph.png', help='compact PNG')
    p.add_argument('--full-resolution', default='spectrograph_full.png', help='PNG with at least one vertical pixel per frequency bin')
    p.add_argument('--time-bin', type=float, default=120, help='seconds per time column; latest capture wins if several occupy a column')
    args = p.parse_args()
    if (not all(math.isfinite(v) for v in (args.hours, args.time_bin, args.cmap_min, args.cmap_max))
            or args.hours <= 0 or args.time_bin <= 0 or args.cmap_min >= args.cmap_max):
        p.error('hours/time-bin must be positive and colour limits finite and increasing')
    if Path(args.spectrograph).expanduser().resolve() == Path(args.full_resolution).expanduser().resolve():
        p.error('the two output paths must differ')
    if any(Path(path).expanduser().resolve() == Path(args.database).expanduser().resolve()
           for path in (args.spectrograph, args.full_resolution)):
        p.error('an output path must not overwrite the database')
    try:
        image, grid, start, end, count, occupied, step, receiver = load_window(
            args.database, args.hours, time.time(), args.statistic, args.time_bin)
        print(f'Read {count} captures; {occupied}/{image.shape[1]} time columns populated '
              f'({step:g}s/column). Grey indicates missing data.', flush=True)
        render(args.spectrograph, image, grid, start, end, args, receiver=receiver)
        render(args.full_resolution, image, grid, start, end, args, high_resolution=True, receiver=receiver)
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Plot failed: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
