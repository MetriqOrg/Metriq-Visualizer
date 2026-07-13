# Metriq Visualizer 1.12.5 architecture

## Product boundary

Metriq Visualizer is a local creator, classroom, field-observation, and light-science application. It favors explicit local files, bounded memory, portable JSON state, and recoverable failure. It does not contain cloud accounts, telemetry, remote rendering, monetization gates, or industrial acquisition/control features.

The original interactive 3D visualizer is the product core. Analysis panels, source playback, Export Studio, microphone tools, and persistence are supporting systems around that core.

## Runtime boundaries

1. **Qt orchestration** — window state, user actions, playback, background analysis, settings, projects, and dialogs.
2. **Analysis core** — toolkit-neutral decoding, feature extraction, table import, safe formulas, and geometry mapping.
3. **Exact true-3D scene** — one Matplotlib 3D artist graph used for paused inspection and offscreen export.
4. **Realtime motion scene** — a Qt/QPainter projection using the same mapped XYZ geometry, camera, trail, spline, and accent state during playback, autorotation, drag, and microphone input.
5. **Composition renderer** — combines the exact 3D geometry raster with source/scientific layers and presentation overlays.
6. **Streaming exporter** — profiles, FFmpeg process management, image sequences, progress, cancellation, and atomic destination replacement.
7. **Live capture** — bounded audio callback with DSP, 3D mapping, and visualization outside the callback.

## Main modules

### `metriq_visualizer_app.py`

Owns the original-style main workspace: a left mapping/appearance/data inspector, dominant interactive 3D viewport, collapsible bottom analysis dock, and playback controls. It coordinates background source analysis, recent/recoverable sessions, media synchronization, geometry rebuilding, projects, presets, data export, Export Studio, and live input.

Analysis runs on a `QThread`. Playback clock updates, 3D rendering, and idle preview/debounce work use separate timers so continuous playback cannot starve rendering.

### `metriq_visualizer_3d.py`

This is the authoritative geometry renderer.

- `Matplotlib3DScene` owns genuine `projection="3d"` axes and reusable artists for points, line segments, tube meshes, ghost path, comet, head, halo, flash, labels, axes, colorbar, HUD, perspective, and camera.
- `Interactive3DViewport` coordinates the exact QtAgg canvas with `Realtime3DCanvas`, owns the independent autorotation clock, and keeps camera state synchronized between moving and paused modes.
- `Matplotlib3DFrameRenderer` uses the same scene with `FigureCanvasAgg` for exact-size RGBA export frames.
- `compute_trail_state` materializes one aligned playback state for Trail fade, Cumulative reveal, or Full static mode while preserving original source indices through filtering/downsampling.

The exact and realtime paths share temporal and spatial semantics. The realtime path is a low-latency projection of the same mapped 3D data, not a replacement formula or export renderer.

### `metriq_visualizer_realtime.py`

Provides the QPainter motion canvas used during playback, autorotation, camera drag, and microphone input. Data, grid, and axes pass through one bounded camera transform; spline interpolation and motion-accent state are reused from the authoritative 3D module.

### `metriq_visualizer_panels.py`

Provides the subordinate bottom dock: source video or waveform, spectrogram, chromagram, MFCC, and mapped traces. All panels share the playback cursor. The dock starts collapsed and explicitly updates splitter sizing so hidden panels do not reserve space.

### `metriq_visualizer_core.py`

Contains no Qt code. `AnalysisResult` stores aligned features and scientific panels. `GeometryResult` stores full and sampled mapped geometry plus source-index alignment. `AnalysisSettings` makes sample rate, FFT, hop, frequency band, Mel bands, MFCC count, and frame ceiling explicit. The module provides delimited/XLSX import, SoundFile/FFmpeg audio decoding, OpenCV visual analysis for silent video, bounded DSP, principal components, restricted AST formulas, filtering, normalization, sampling, and color mapping.

### `metriq_visualizer_render.py`

`ExportPreviewSession` composes RGBA frames with Pillow/OpenCV/Matplotlib. The geometry layer delegates to `Matplotlib3DFrameRenderer`; source video capture and scientific panel rasters are cached/reused. Title, subtitle, watermark, HUD, timecode, and six normalized layout layers are composed here.

### `metriq_visualizer_layout.py`

Defines versioned, normalized coordinates for geometry, source preview, spectrogram, chromagram, MFCC, and traces. Layer order is explicit. Values are cloned, clamped, serialized, and resolution-independent.

### `metriq_visualizer_export_studio.py`

Owns direct manipulation of the six-layer layout, undo/redo, safe areas, snapping, profile load/save, preview recreation, and background export. Profile changes that affect 3D presentation invalidate/recreate the preview session.

### `metriq_visualizer_export_pipeline.py`

Validates schema-4 `.mvexport` profiles, resolves output paths, probes/selects encoders, streams RGBA frames through FFmpeg stdin, drains stderr concurrently, maps optional source audio, writes PNG/JPEG sequences, reports progress, and handles cancellation. Encoded video is written to a unique same-directory temporary path and atomically replaces the requested destination only after success.

### `metriq_visualizer_live.py`

The audio callback performs channel selection, bounded ring-buffer writes, status capture, and optional chunk retention. FFT, spectrum, rolling spectrogram, metrics, painting, WAV writing, and analysis handoff occur outside the callback. Captures can be sent into the standard analysis/3D workflow.

### Persistence and support modules

- `metriq_visualizer_cache.py` — content-validated compressed local analysis cache.
- `metriq_visualizer_atomic.py` — unique same-directory transactional writes.
- `metriq_visualizer_projects.py` — portable `.mvproj` and compatible legacy `.bgl` state.
- `metriq_visualizer_preset_files.py` — source-free `.mvpreset` state, v1.10 schema translation, user-directory discovery, and display-name precedence.
- `metriq_visualizer_performance.py` — live-only workload profiles, completed-draw adaptation, and proxy simplification without changing saved/export settings.
- `metriq_visualizer_data_export.py` — aligned CSV/NPZ feature and geometry output.
- `metriq_visualizer_theme.py` — dark/light Dynamics-inspired presentation and boot overlay.

## Performance strategy

- Analyze sources off the UI thread with explicit, cache-keyed extraction settings.
- Keep full mapped geometry for exact paused rendering and export.
- Use the QPainter motion renderer while playback, autorotation, camera drag, or microphone input is active.
- Project data, grid, and axes through one camera/zoom transform.
- Keep spline interpolation and motion accents active in normal live profiles; substitute only expensive polygonal tube surfaces with a smoothed centerline while moving.
- Bound live point density, coalesce stale paint requests, and adapt density from measured paint cost.
- Cap live device-pixel ratio on high-DPI displays without limiting export resolution.
- Use binary search over monotonic timestamps for playback head, visible range, and comet windows.
- Reuse source capture, fonts, panel rasters, and the offscreen exact 3D scene during export.
- Stream frames directly to FFmpeg and use transactional destinations.
- Keep DSP, mapping, painting, and file I/O outside the real-time audio callback.

## Failure behavior

- Missing FFmpeg disables encoded video and broad fallback decoding, not table work or PNG/JPEG sequences.
- Missing PortAudio/input hardware disables live capture, not file analysis.
- An unusable hardware encoder falls back to software in automatic mode.
- Cache corruption removes the affected entry and triggers fresh analysis.
- Failed/cancelled export preserves any pre-existing destination.
- Projects, presets, profiles, cache records, data exports, WAV captures, merge reports, and source merging use transactional local writes.
- The repository merge tool rejects unsafe paths and symlinks, copies only checksum-listed release files, and never deletes unrelated target files.

## Privacy boundary

All supported workflows operate on explicit local files and local input devices. A future sound/species-recognition plug-in should consume `AnalysisResult` or bounded live snapshots outside the capture callback; it should not introduce an implicit remote-upload path.
