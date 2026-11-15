#!/bin/bash
# Edit the options below to configure cron captures. Extra arguments override them.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

exec ./venv/bin/python capture_spectrum.py \
    --radio hf.local \
    --database spectra.sqlite3 \
    --min-mhz 0.1 --max-mhz 30 --rbw 3000 \
    --window kaiser --duration 20 \
    "$@" >> capture.log 2>&1
