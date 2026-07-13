#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Metriq Visualizer requires Python 3. Set PYTHON_BIN to a valid interpreter." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  cat >&2 <<'MSG'
[Metriq] FFmpeg was not found. The app will still run and can export PNG/JPEG
sequences, but MP4, MOV, WebM, GIF, and source-audio muxing require FFmpeg.
MSG
fi

if ! python -c 'import sounddevice' >/dev/null 2>&1; then
  cat >&2 <<'MSG'
[Metriq] Live microphone input is unavailable because PortAudio could not be
loaded. On Debian/Ubuntu install `libportaudio2`, then run this launcher again.
File analysis and export remain available.
MSG
fi

exec python metriq_visualizer_app.py "$@"
