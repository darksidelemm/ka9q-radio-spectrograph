#!/bin/bash
# Edit the options below to configure cron plots. Extra arguments override them.
sleep 15
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec >> plot.log 2>&1

# Keep the lock open through rendering; skip an overlapping cron invocation.
exec 9>plots.lock
if ! /usr/bin/flock -n 9; then
    exit 0
fi
exec ./venv/bin/python plot_spectra.py spectra.sqlite3 \
    --hours 72 --title "VK5ARG HF" \
    --statistic median --cmap-min -140 --cmap-max -30 \
    --spectrograph spectrograph.png \
    --full-resolution spectrograph_full.png \
    "$@"

# Add in code here to transfer spectrographs to somewhere accesible
# e.g.
# scp *.png your-server:/your/server/path