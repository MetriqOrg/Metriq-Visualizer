# Mobile architecture and parity contract

## Non-negotiable contract

The mobile product is another Metriq Visualizer shell, not a reduced “viewer.”
The desktop Python application remains the behavior oracle while capabilities
move into a shared compiled core. Mobile performance controls may change update
frequency or the number of points submitted to a live preview, but they must not
remove source data, preset fields, project state, export resolution, or creator
controls.

## Runtime layers

1. **Shared C++ data/core boundary** — portable scene points, local table import,
   preset/project schema handling, bounded audio analysis, and mobile lifecycle.
2. **Retained GPU scene** — `GpuVisualizerItem` supplies static triangle geometry
   to the Qt Quick scene graph. `VisualizerMaterial` changes camera, timeline,
   visibility, and appearance through a uniform block. Qt chooses Metal on Apple,
   Vulkan/OpenGL ES on Android as available, and the appropriate host backend.
3. **Qt Quick shell** — touch orbit, pinch zoom, transport, file picker, live
   microphone, and a bottom-sheet inspector. The same target runs on a desktop
   host for deterministic validation before device packaging.
4. **Compatibility documents** — schema-4 `.mvpreset` and schema-2 `.mvproj`
   documents retain unknown sections. Supported fields are updated in place;
   newer unsupported schema versions fail closed instead of being truncated.

## GPU design

Lines and points are represented by triangle quads rather than API-specific
wide lines or point sprites. That produces consistent width and point behavior
across Metal, Vulkan, Direct3D, and OpenGL ES. The vertex shader performs the 3D
camera transform, screen-space line extrusion, point sizing, timeline reveal,
trail fading, and head sizing. The fragment shader handles circular points,
soft edges, alpha, and head/halo appearance.

Source replacement or point-budget changes rebuild the retained mesh. Camera,
zoom, playback, and ordinary visual-control changes update uniforms only. This
is the main performance property required for mobile: dragging the camera does
not regenerate every line and point on the CPU.

The renderer reports the graphics API selected by the actual Qt Quick window.
“GPU” is not inferred merely from the presence of an OpenGL library. The app can
run with a software fallback for compatibility, but the fallback is named.

## Data and memory

The imported source is retained in `SceneController::m_points`. A separate
endpoint-preserving GPU preview array is built from the configured point budget.
The current implementation bounds a single table import to one million valid
rows as a mobile safety ceiling and reports truncation. This is not a permanent
file-format limit; chunked/memory-mapped source storage is a later core milestone.

Live microphone capture uses the device's preferred format, converts interleaved
samples to mono outside the audio driver, and performs bounded 2,048-sample FFT
windows. The live scene is a rolling 12,000-point buffer. The microphone pauses
when the application leaves the foreground and resumes only if it was active.

## Parity milestones

### Implemented in this vertical slice

- GPU 3D path and points with camera, trail/reveal/static timeline modes, grid,
  axes, head/halo, opacity, sizing, and autorotation.
- Touch/desktop interaction and renderer diagnostics.
- Table import, schema-compatible preset/project read/write, demo scene.
- Live local microphone metrics and 3D mapping.
- Android/iOS application metadata and permissions.
- C++ regression tests and host/mobile build workflows.

### Next—do not mark complete until tested

- Port the full safe expression engine and every built-in mapping to the shared
  core with Python/C++ golden vectors.
- Audio/video file decode and the complete desktop feature extraction pipeline.
- GPU waveform, spectrogram, chromagram, MFCC, and mapped-trace panels.
- Full source-video layer, composition layouts, Export Studio, image sequences,
  and zero-copy VideoToolbox/MediaCodec export.
- Device thermal governor using platform thermal signals, with update-frequency
  adaptation before any live-preview density adaptation.
- Physical-device matrix: representative low/mid/high Android, current iPhone/
  iPad, background/resume, orientation, high-refresh, permission denial, large
  files, GPU fallback, and sustained thermal/battery runs.

## Security and privacy

There is no account, telemetry, cloud renderer, analytics SDK, advertising SDK,
or implicit upload path. Local microphone capture starts only after a user action
and platform permission. Saves use `QSaveFile` transactional replacement. Mobile
release signing credentials belong in protected release infrastructure, never in
the public repository.
