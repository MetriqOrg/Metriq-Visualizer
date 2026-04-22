# Metriq Visualizer v1.10.16

Metriq Visualizer is an open-source multidimensional data and media visualizer.

It turns local audio, video, CSV, TSV, and XLSX data into interactive 3D geometry that you can scrub, inspect, and export as an MP4.

## About Metriq

Metriq builds technology intended to support real-world progress. The visual language behind this project reflects that broader design philosophy: clean, technical, and forward-looking.

## Features
- Local audio and video analysis
- Local CSV, TSV, and XLSX import for numeric datasets
- Formula-based mapping for X, Y, Z, color, and size
- Interactive geometry playback and timeline scrubbing
- Optional smooth spline path rendering for line and tube modes
- Save and reopen visualizer projects as `.mvproj`
- Save and reuse visualizer presets as `.mvpreset`
- Visualizer Behavior preset files are loaded from the local `presets/` folder
- Legacy `.bgl` project files can still be opened
- Feature reference panel and mapped trace panels
- Professional dark-mode interface
- MP4 export presets in:
  - 1280×720 landscape
  - 1920×1080 landscape
  - 1080×1920 vertical

## Input notes
Open a media file to extract a feature set and build geometry.

Open a table file with at least one numeric column to map imported values into geometry. Imported columns are available as formula-ready features, and the table importer also derives helper features such as `pc1`, `pc2`, `pc3`, `magnitude`, `column_mean`, and `delta_magnitude`.

## Formula examples
For media:
- `pc1`
- `0.7*mfcc_1 + 0.3*chroma_mean`
- `smooth(spectral_flux, 5)`

For tabular data:
- `input_1`
- `mean(input_1, input_2)`
- `pc1`
- `delta_magnitude`

Supported functions: `abs`, `sqrt`, `log`, `log1p`, `exp`, `clip`, `smooth`, `mean`, `avg`, `sum`, `max`, `min`

## Install (Linux)
```bash
sudo apt update
sudo apt install ffmpeg python3-pip libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libpulse0

cd metriq_visualizer_v1_10_16
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python metriq_visualizer_app.py
```

Or:
```bash
./run_linux.sh
```

## License
The source code in this package is licensed under Apache-2.0. See `LICENSE`.

## Branding and intellectual property notice
Use of this software, any fork, any modified version, or any derivative work does **not** grant permission to use the Metriq name, trademarks, service marks, logos, symbols, trade dress, copyrighted brand materials, or any other Metriq Foundation, Inc. intellectual property, and does not imply affiliation, sponsorship, or endorsement by Metriq Foundation, Inc.

See `TRADEMARKS.md` and `assets/ASSET_NOTICE.md` for the brand-asset reservation notice.

## Copyright
Copyright (c) 2026 Metriq Foundation, Inc.


Visualizer behavior presets are loaded from the local `presets/` directory next to the app. Add your own `.mvpreset` files there to populate the list.
