# ka9q-radio HF spectrograph

Python 3.10+ utilities for collecting HF spectrum data from a ka9q-radio RX888
receiver and plotting its history.

Must be run on the Linux machine hosting radiod. The default radio control multicast hostname is `hf.local`.

Yes, this is entirely vibe-coded... (Using Codex).



## Install

Copy the project scripts and `requirements.txt` into
`~/ka9qradio_spectrograph/` on the receiver machine. On Debian/Ubuntu, install
Python's virtual environment support and the Linux `flock` utility if needed:

```sh
sudo apt install python3 python3-venv util-linux
cd ~/ka9qradio_spectrograph
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
chmod +x capture_spectrum.sh plot_spectrum.sh
```

No virtual environment activation is required: the wrappers use `venv/bin/python`
explicitly. If you use another environment directory, edit both wrappers.
The local `deploy.sh` helper copies Python, shell, requirements and Markdown files
to its configured SSH destination; edit that destination before using it.

radiod must be running with the spectrum service supported and its `data =`
destination configured to permit dynamic channel creation. `hf.local` must resolve
to the radio's IPv4 multicast control address. The capture uses a dedicated random
channel ID with a limited lifetime and does not change receiver gain settings.

## Test and capture

First check live spectrum capture and a temporary database round trip:

```sh
cd ~/ka9qradio_spectrograph
./venv/bin/python test_capture.py --radio hf.local
```

This does not write production data. If multicast uses a specific network
interface, supply its local IPv4 address using `--interface`, and add that option
to `capture_spectrum.sh` too. Check the multicast address, interface and radiod
configuration if the diagnostic times out.

Run a production capture, then inspect the archive:

```sh
./capture_spectrum.sh
./venv/bin/python inspect_spectra.py spectra.sqlite3
```

The wrapper writes diagnostic output to `capture.log` and stores captures in
`spectra.sqlite3`, both beside the scripts. It works regardless of the calling
shell's current directory. Edit `capture_spectrum.sh` to configure:

- Radio hostname, frequency range and bin spacing: default 0.1–30 MHz at 3 kHz.
- Sampling duration: default 20 seconds, polling every 0.2 seconds.
- FFT window: default Kaiser with beta 7.0, matching ka9q-web. Other choices are
  `rectangular`, `hann`, `hamming`, and `blackman-harris`.

Each record stores per-bin median and peak powers in dBm, plus spectrum metadata.
Kaiser and other tapered windows have noise bandwidth wider than the bin spacing;
the reported noise bandwidth is stored separately. Absolute power accuracy depends
on receiver calibration. Peaks are maxima of polled spectra and can miss signals
between polls. Spectrum requests are limited to 14,000 bins; narrow the range or
increase spacing if this limit is exceeded.

Receiver readings are stored for every spectrum poll in metadata under
`receiver_samples`: elapsed seconds, A/D level in dBFS, attenuation in dB and VGA
gain in dB. Older records without receiver readings are still usable.

SQLite provides append operations and indexed time-range reads without loading
the whole archive. `inspect_spectra.py` opens it read-only and reports UTC coverage
and stored signal extrema. Capture locking skips overlapping captures. Persistent
`.capture.lock` and `plots.lock` files are normal: the operating system releases
the active locks when the corresponding processes exit; leave these files in place.

## Plot

```sh
./plot_spectrum.sh
```

Edit `plot_spectrum.sh` to change the title, time window, colour limits, statistic
or output paths. It defaults to the last 72 hours of median power, using `jet`
with limits of -130 to -30 dBm per bin. Outputs are `spectrograph.png` and
`spectrograph_full.png`; progress and errors go to `plot.log`.

Both plots have UTC time ticks every six hours, frequency labels on both sides,
and a small receiver panel showing VGA gain plus attenuation on the left scale
and A/D level on the right. The full-resolution image preserves at least one
vertical plot pixel per frequency bin and has dashed frequency/time grid lines.
Missing data is shown as gaps. See [PLOTTING.md](PLOTTING.md) for image dimensions,
time-cell aggregation, memory requirements and additional command-line options.

The plotting wrapper uses Linux `/usr/bin/flock` to skip overlapping plot jobs.
Plotting reads the database read-only and can run alongside capture. Database
locks may briefly delay a writer during the read stage; rendering starts after
the read connection closes.

Both wrappers accept extra Python options, for example:

```sh
./capture_spectrum.sh --duration 30
./plot_spectrum.sh --hours 24 --statistic peak
```

These options override preceding defaults. To change the plotting database,
edit its positional filename in `plot_spectrum.sh`.

## Schedule

Run `crontab -e` as the account owning the project, and add:

```cron
*/2 * * * * "$HOME/ka9qradio_spectrograph/capture_spectrum.sh"
*/30 * * * * "$HOME/ka9qradio_spectrograph/plot_spectrum.sh"
```

Replace previous long capture/plot entries with these to avoid duplicate jobs.
The wrappers handle working directory, environment interpreter, output options
and log redirection. Check operation with:

```sh
tail -n 20 ~/ka9qradio_spectrograph/capture.log
tail -n 20 ~/ka9qradio_spectrograph/plot.log
```

Logs grow by appending; rotate them periodically if retaining the installation
long term. Plot time labels are always UTC regardless of cron's timezone.

## Retention and offline checks

To delete everything older than seven days (168 hours):

```sh
./venv/bin/python cleanup_spectra.py spectra.sqlite3 --hours 168
```

Deleted pages are reused. Add `--vacuum` to shrink the file; run that maintenance
outside capture/plot jobs because it locks writers and needs extra free disk space.
Back up the archive before deleting data you may need later.

Run offline protocol, capture-metadata, archive and plotting checks with:

```sh
./venv/bin/python -m unittest -v test_offline test_plot_spectra
```
