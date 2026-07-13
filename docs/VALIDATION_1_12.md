# Metriq Visualizer 1.12.5 validation

## Purpose

v1.12.5 is validated as a targeted correction to the restored original three-dimensional workspace. Acceptance includes independent autorotation, stable orbit, whole-coordinate-field zoom, visible spline/accent controls, functional extraction settings, embedded live microphone trajectories, backward-compatible presets, and preservation of exact export quality.

## Regression addressed

The earlier v1.12 shell substituted a flattened projection for the original Matplotlib 3D renderer. v1.12.5 restores:

- a `projection="3d"` axes in the main workspace;
- perspective camera changes that materially alter rendered pixels;
- depth-bearing XYZ points and segments;
- point, line, spline, tube, ghost-path, axis, label, colorbar, head, halo, flash, and comet artists;
- one shared scene implementation for Qt interaction and offscreen export;
- a bottom analysis dock that starts collapsed without reserving invisible space.

## Behavioral checks

The automated suite covers:

- true-3D axes creation and camera-dependent projection changes;
- elapsed-wall-clock autorotation while paused and with live-only trajectories;
- bounded logical-pixel drag math and renderer latching from mouse press through release;
- shared data/grid/axes zoom in the realtime renderer and coordinate-box zoom in the exact scene;
- realtime axis/axis-label visibility;
- preservation of XYZ depth and source-index alignment;
- filtered temporal gaps that are not bridged by path segments;
- Full static history with a playback-following head;
- correct Current point labels and Time/Hz/Index formats;
- configurable spline detail, comet, halo, head, and flash behavior in exact and normal live preview paths;
- tube geometry/radius behavior;
- clean exports with scene HUD removed;
- exact odd and even output dimensions;
- live playback advancing both time and multiple rendered frames;
- Qt Multimedia source wiring, seek, volume/mute capability, and silent-video preview;
- bottom-dock collapse/expand geometry;
- light and dark figure/axes palette changes on the actual 3D canvas;
- legacy mapping aliases and all built-in mapping formulas across table, audio, and silent-video sources;
- v1.10-era preset translation, user-directory precedence, and legacy `fill` layout conversion;
- Fast/Balanced/High/Full live-option transformations without mutating export options;
- adaptive live-density reduction, bounded floor, conservative recovery, and immediate disable/reset behavior;
- busy-frame coalescing, selected-profile paused behavior, and optional idle full-scene refinement;
- effective/configured density and completed-draw latency status reporting;
- monotonic-time binary-search selection for head, visible range, and comet windows;
- Export Studio profile recreation and presentation persistence;
- all bundled profile schema/version checks;
- transactional video/sequence output and cancellation;
- configurable sample rate, FFT, hop, band limits, Mel bands, MFCC count, frame ceiling, and analysis-cache identity;
- operating-system default microphone fallback after device-enumeration failure and embedded 3D trajectory emission;
- macOS bundle microphone-purpose metadata;
- cache, project, preset, data-export, live-buffer, theme, and merge-tool behavior, including preservation of an existing creator preset with the same bundled filename.

## Static and package checks

The release process runs:

```bash
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m pytest -q
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q .
```

It additionally verifies the source manifest, PEP 517 wheel build, ZIP/TAR integrity, Git clean-state behavior in a temporary repository, and merge dry-run/apply/idempotence while preserving `.git`, unrelated target files, and pre-existing creator presets.

## Interactive renderer validation

The realtime canvas is tested with synthetic XYZ trajectories and offscreen Qt painting. Tests verify that camera changes, spline tessellation, motion accents, axis visibility, and zoom alter the expected projected state without rebuilding audio analysis or mapped geometry. Timing figures are recorded only as host-specific diagnostics and are not presented as Mac/Retina guarantees.

## Export validation

When FFmpeg is available, the suite performs real H.264 streaming and command/profile tests. The final package validation report records the formats exercised in the build environment. No hardware-encoder result is claimed unless that physical encoder is exposed and a real probe succeeds.

## Environment limits

A headless Linux container cannot prove audible speaker output, physical microphone capture, Apple Silicon/Metal behavior, macOS Retina throughput, GPU hardware encoding, operating-system signing, or notarization. Software paths, Qt media wiring, timing behavior, capture failure/retry handling, DSP buffers, generated audio streams, and codec output are validated without representing those as physical-device tests. Synthetic render benchmarks in the machine-readable report describe only the validation host and are not presented as Mac measurements.

The final machine-readable report is distributed beside the source archives.
