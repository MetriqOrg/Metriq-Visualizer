# Changelog

## 1.12.6 — audience output and verified updates

### Added

- A configurable **Stage Output** window for a projector, TV, or second monitor. It mirrors the already-rendered live scene so studio controls remain available on the operator display, while composition can independently enable, move, resize, order, and fit geometry, source, spectrogram, chromagram, MFCC, and mapped traces.
- Stage backgrounds can be a solid color, static image, or muted looping video. Stage configuration—including composition—persists in projects and local settings.
- A background GitHub release checker and a **Check updates** command. It only offers non-prerelease macOS application archives carrying GitHub's SHA-256 digest; before replacement it validates the downloaded digest, bundle identity/version, and code signature, then restarts only after the operator explicitly approves the update.

### Fixed

- The Mapping tab once again presents the full **Feature Formula Presets** menu, including all original Pitch/Timbre/Motion, Audio PCA, Rhythm/Brightness/Texture, and table mappings. A GUI regression test verifies every built-in entry is visible.
- Fast Preview retains enough Catmull–Rom samples for Smooth spline to read as visibly curved rather than point-to-point segments.
- The realtime media and microphone paths now always tessellate the displayed
  Smooth spline trail instead of reusing a straight precomputed segment buffer.
- Frozen macOS bundles no longer write Python bytecode into their signed
  resources after launch, so update verification remains valid.

## 1.12.5 — Camera control, restored extraction settings, and live 3D microphone input

### Added

- A low-latency Qt/QPainter motion renderer that shares mapped XYZ data, camera state, trail semantics, spline interpolation, motion accents, axes, and zoom with the exact Matplotlib scene.
- Functional audio-analysis controls for sample rate, FFT size, hop length, minimum/maximum frequency, Mel bands, MFCC count, and analysis-frame ceiling.
- Balanced, Legacy v1.10, Fast analysis, Birdsong detail, and Custom extraction profiles.
- An embedded Live Input dock that maps microphone features into the main three-dimensional viewport while retaining waveform/spectrum diagnostics and optional WAV capture.
- macOS application-bundle microphone-purpose metadata.
- Regression tests for independent autorotation, stable logical-pixel orbit, whole-scene zoom, spline/accent state, configurable extraction/cache identity, device fallback, embedded live trajectories, and fixed telemetry geometry.

### Changed

- Interactive autorotation is driven by elapsed wall-clock time instead of source playback time, so it works while paused and during live input. Export autorotation remains deterministic from source time.
- Mouse orbit bypasses Matplotlib's unstable default 3D drag handler and uses bounded elevation plus normalized azimuth. A drag remains on one renderer until release, avoiding mid-drag canvas swaps.
- Zoom scales the projected data, grid, and axes together in the realtime renderer and uses Matplotlib's coordinate-box zoom in the exact scene.
- Fast/Balanced/High preview tiers retain spline smoothing and temporal accents; only expensive tube surfaces use a responsive centerline proxy while moving.
- Fine-adjustment steps were reduced for trail lifespan, fade, opacity, line/point scale, camera, rotation speed, tube, comet, flash, and accent sizes.
- Color-map selection moved to Appearance.
- Analysis settings are persisted in projects/presets, translated from legacy keys, and included in cache identity.
- Live capture now tries the operating-system default device even when device enumeration fails, then retries compatible native/48 kHz/44.1 kHz stream configurations.
- Realtime path segments and point sprites are issued to Qt in bounded gradient batches, reducing Python-to-Qt draw-call overhead without changing exact paused or exported geometry.

### Fixed

- Autorotate appearing inert when media was paused or no file was loaded.
- High-DPI mouse movement producing disproportionate camera jumps.
- A delayed settings refresh swapping from the realtime canvas to the exact canvas while the mouse button was still held.
- Zoom affecting data artists while leaving the coordinate grid visually unchanged.
- Smooth spline and motion-accent controls appearing ineffective in ordinary preview modes.
- Realtime playback ignoring axis and axis-label visibility settings.
- Live render mode and point/head scaling not respecting Appearance options.
- Frequency controls accepting a nonzero upper bound at or below the lower bound.
- Stale analysis cache entries being reused after FFT, band, Mel, MFCC, or sample-rate changes.
- Live microphone startup being blocked solely by a failed device-list query.
- The realtime camera using the opposite elevation convention from the exact 3D scene; 90° is now top-down over XY and 0° is level with the plane in both renderers.
- Grid planes being inseparable from axes, or visually covering geometry as the camera crossed them. Grid visibility is now independent and exact-scene panes are transparent with grid lines drawn behind the visualizer.
- A paused exact scene being forced through Matplotlib redraws by queued idle callbacks, which could keep CPU usage high after playback. Autorotation now also follows the selected live target rate.
- Analysis-dock cursor paints competing with the 3D renderer on every playback-clock tick. The active scientific panel now repaints at a bounded 8 Hz while its cursor state remains current.
- Manual camera interaction silently disabling the selected autorotation control. Rotation now pauses only while dragging and resumes on release. During playback, it advances exactly once per displayed preview frame instead of competing with the render loop.
- The realtime XZ and YZ coordinate planes becoming foreground geometry after orbiting around the scene. All three planes and their axes now relocate to the camera-facing back edges of the coordinate field.
- Smoke-test results depending on a developer's saved macOS application preferences. Tests now use an explicit isolated settings file on every platform.

## 1.12.4 — Low-latency motion rendering and embedded live source

- Separated moving playback/live rendering from exact paused inspection and export.
- Added a cached QPainter motion path so normal playback no longer rebuilds an export-grade Matplotlib 3D axes graph on every tick.
- Stabilized time/status telemetry with fixed-width, fixed-pitch layouts.
- Replaced the recording-only popup workflow with an embedded microphone dock feeding the main viewport.
- Added bounded live feature extraction, adaptive frame timing, and callback-safe capture/recording boundaries.

## 1.12.3 — Theme parity, legacy presets, and scalable live preview

### Added

- A complete light palette for the Matplotlib figure, genuine 3D axes, panes, grid, ticks, labels, HUD, colorbar, ghost path, and empty-state canvas.
- Restored legacy mapping choices and formula aliases, including `Pitch/Timbre/Motion`, `Audio PCA`, `Rhythm / Brightness / Texture`, table mappings, and a compatible local `Birdsong` fallback.
- Separate visual-style selection for Data Disco, Glowstick, Neon Lights, and creator-authored `.mvpreset` files without replacing the current mapping or performance profile.
- Four explicit live workload profiles: Fast preview, Balanced, High quality, and Full live simulation.
- Live-only high-DPI canvas ceilings for Retina/high-DPI displays.
- Adaptive live density based on an exponentially weighted measurement of completed Matplotlib draw time.
- Effective/configured point density and completed-draw latency in the viewport status line.
- User preset discovery through `~/.metriq_visualizer/presets` and `METRIQ_PRESET_PATH`.

### Changed

- Balanced is the default live profile and remains active while paused unless optional idle refinement is enabled.
- Playback frames are coalesced while a prior Matplotlib draw is pending instead of building a stale render queue.
- Geometry visibility, playback-head selection, and comet-window selection use monotonic-time binary searches instead of repeated full-array scans.
- The interactive scene caches full XYZ coordinates and avoids reapplying unchanged camera, axes, labels, colorbar, and zoom state on every playback frame.
- Existing v1.10-era preset sections are translated into the current state model when loaded.
- Bundled visual presets carry modern performance metadata but do not change the selected performance profile when used from the Visual style control.
- Additive repository merges preserve any existing `presets/*.mvpreset` file rather than overwriting creator work.

### Fixed

- Light mode no longer leaves the central visualizer canvas dark.
- Legacy feature names such as `f0_hz`, `dominant_freq_hz`, `spectral_centroid_hz`, `zcr`, `chroma_entropy`, and `row_index` no longer fail in compatible old presets.
- Mapping presets and visual presets no longer overwrite one another.
- The first live scene now receives the selected point ceiling before its initial draw.
- The viewport status line no longer repeats its scene prefix.

### Performance boundary

All profile substitutions, adaptive density, busy-frame coalescing, and high-DPI limits are confined to the desktop viewport. Saved creator settings and Export Studio output remain full fidelity.

## 1.12.2 — Restore the original-style true 3D workspace

- Restored a genuine Matplotlib `projection="3d"` viewport with orbit, zoom, depth, labels, axes, colorbar, point/line/spline/tube rendering, ghost path, head, halo, flash, and comet trail.
- Shared the same 3D scene implementation between the main viewport and Export Studio.
- Returned source video and scientific panels to a collapsible bottom dock.
- Fixed playback-head behavior, label selection, source-index alignment, filtered temporal gaps, tube controls, and stale export previews.

## 1.12.1 — Playback and rendering repair

- Connected source playback through Qt Multimedia.
- Separated the playback clock, continuous playback renderer, and idle preview debounce timer.
- Moved analysis panels from the right column to the bottom workspace.
- Hardened export cancellation, destination preservation, sequence output, cache invalidation, microphone retry, and project portability.

## 1.12.0 — Export Studio and expanded desktop workflow

- Added direct-manipulation export composition, multi-format FFmpeg output, live microphone tools, local cache, data export, project/preset persistence, and the Dynamics-inspired application presentation.
- This release also introduced application-shell regressions that were corrected in 1.12.1 and 1.12.2.
