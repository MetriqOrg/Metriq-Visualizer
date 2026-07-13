#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Metriq Visualizer requires Python 3.10 or newer." >&2
  exit 1
fi
if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[Metriq] Install FFmpeg with: brew install ffmpeg" >&2
fi
exec python metriq_visualizer_app.py "$@"
