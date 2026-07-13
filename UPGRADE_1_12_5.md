# Upgrade to Metriq Visualizer 1.12.5

Version 1.12.5 is a corrective release built on the restored original-style 3D workspace. It does not replace the application shell.

## Camera and viewport

- Autorotation now has its own elapsed-time clock and works while paused or while live input is active. Manual camera drags pause it temporarily and resume it on release without changing the selected control. During playback, it advances once per displayed 3D frame so it cannot be starved by a competing timer.
- Export autorotation remains tied to source time for reproducible frames.
- Mouse orbit uses logical-pixel deltas, bounded elevation, and normalized azimuth rather than Matplotlib's default 3D drag handler.
- The realtime canvas stays active from mouse press through release, preventing a delayed option refresh from swapping renderers mid-drag.
- Zoom transforms data, grid, axes, and labels as one coordinate field.
- Elevation is consistent across the realtime and exact views: **90°** looks straight down at the XY plane, while **0°** is level with it.
- Appearance now has a separate **Show grid planes** control. The XZ and YZ planes relocate to the camera-facing back edges while orbiting, and the exact view uses transparent panes, so a plane never hides the data.
- A static refined scene is no longer redrawn by idle callbacks. The playback clock updates analysis cursor state without painting its Matplotlib panel; the selected panel repaints at 8 Hz while 3D playback is active, avoiding unnecessary CPU work.

## Analysis controls

The Mapping tab restores functional extraction settings:

- sample rate;
- FFT size;
- hop length;
- minimum and maximum frequency;
- Mel-band count;
- MFCC coefficient count;
- analysis-frame ceiling.

Changing these values marks the loaded media for reanalysis. The values are stored in projects and presets and are part of the analysis-cache key. `Legacy v1.10` applies 22,050 Hz, FFT 2,048, and hop 256.

## Appearance controls

- Color map is under Appearance → Visual style.
- Trail lifespan and other continuous controls use fine increments.
- Smooth spline and motion accents remain visible in normal preview tiers.
- Tube surfaces may use a centerline proxy while the scene is moving; the exact paused scene and exports retain the selected tube geometry.

## Live input

Live Input is a dock in the main window, not a separate recorder. Incoming microphone features generate a rolling XYZ trajectory in the primary viewport. The dock also exposes waveform/spectrum diagnostics, mapping/quality choices, freeze, clear, optional recording, WAV export, and analysis handoff.

The default input route is attempted even if device enumeration fails. On macOS, packaged builds include `NSMicrophoneUsageDescription`; source launches require permission for the terminal or Python host.

## Safe repository import

From the extracted complete-source package:

```bash
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer --dry-run --json
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer
```

The merge tool preserves `.git`, unrelated files, and existing creator presets. Review `git diff` before committing.
