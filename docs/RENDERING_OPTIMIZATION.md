# Rendering optimization pass — 10 September 2026

## Scope

Target: MetriqOrg/Metriq-Visualizer, upstream main
`96fe31c7f112083f6aea652689b01411979f49c0`, version **1.12.8**.
This is a maintenance patch, not a replacement application. No version bump,
new visual style, UI redesign, preset migration, or project-schema change.
The original presets, mapping expressions, quality profiles and export renderer
remain in place. Main is not changed until the pull request is reviewed/merged.

## Changes

1. **Vectorized Catmull–Rom interpolation.** Evaluate the same polynomial,
   sample positions, color/width interpolation and endpoint in NumPy rather than
   nested Python loops. The existing output limit is preserved. A numeric
   regression oracle compares against the original scalar implementation.
2. **Remove duplicate preview geometry work.** Cache sanitized XYZ coordinates
   once per scene. The shared temporal-state function can omit its exact path
   and tube mesh when the realtime renderer will build its own bounded path.
   Point colors, fade, head, comet and time semantics remain shared. Full-axis
   span scans now run only when building tube geometry that needs them.
3. **Bound cumulative sampling before allocation.** Construct the requested
   visible indices directly instead of allocating the entire historical index
   array and immediately throwing most of it away. Preserve head inclusion.
4. **Continuous bounded preview paths.** Reduce vertices and then connect them;
   do not remove individual edges from an already-interpolated continuous line.
   Pass original source indices through the existing temporal-gap convention
   and budget independent runs without joining across those gaps.
5. **Honor existing preview controls.** Points-only mode no longer draws a comet
   line. Head, halo and flash scales now affect media preview, including zero
   to hide each accent. The existing scene-HUD toggle is respected.
6. **Pause/refine at the current time.** Handoff to the exact canvas uses the
   realtime playback clock rather than its own potentially stale last-drawn
   time. Clear/replacement scenes reset the realtime clock and cached points.
7. **Harden live buffers.** Validate point/color/size alignment before replacing
   the last good frame; support uniform attributes; keep finite-filtering and
   point-budget indices aligned; sanitize attributes and own copied buffers.
   Invalid lengths raise ValueError at the input boundary, not during painting.
8. **Avoid unused stage captures.** Do not call QWidget.grab on disabled output
   layers. Capturing an enabled widget can still trigger a paint; this is not
   described as zero-cost reuse of a framebuffer.
9. **Packaging and compatibility checks.** Add the already-imported stage-output
   and updater modules to the wheel module list. Add regression tests and a
   reproducible preview benchmark. Expand PR quality checks from Linux-only to
   Linux, Windows, Apple Silicon macOS and Intel macOS; a configured matrix is
   not itself evidence that every runner or physical device has passed.

## Local validation

Linux x86_64 container, Python 3.13.5, PySide6 6.9.3, Qt offscreen platform.
The untouched source ran 102 tests: 100 passed, two macOS-only tests skipped.
The patch ran 113 tests: 111 passed, the same two skipped. Source import and
compile checks passed. Ruff 0.12.12 passes with UP038 excluded because that
older Ruff flags an existing, unmodified preset-loader expression; GitHub CI
uses the repository's current Ruff installation and checks without this local
exclusion. No unrelated preset code was changed to satisfy an older linter.

The source application's MainWindow opened, analyzed a synthetic table and
rendered an offscreen workspace. A wheel was built and extracted into an
isolated directory; the application imported from that directory, initialized
and closed successfully with the formerly missing modules included. This is
not a signed/notarized desktop-installer validation. Brand assets/presets in
standalone desktop bundles remain governed by the existing PyInstaller build.

No physical microphone, speaker, Apple/Windows desktop session, hardware GPU or
hardware video encoder was available locally. Automated audio/export checks do
not establish audible playback or physical capture. Physical device validation
is still required; CI results should be read from the final PR's actual checks.

## Reproducible performance evidence

Synthetic full-static trajectory: 100,000 source rows, 1,200 live points,
smooth spline detail 4, 960 × 600 logical pixels, DPR 1, same visible HUD and
standard accents. Twenty measured frames after three warmups. Both source
snapshots ran in separate processes in the same container with the same
libraries. Measurements include actual CPU/QPainter rasterization via
QWidget.grab, not just arithmetic, and exclude audio IO and display composition.

| Measurement | Original median | Patched median |
| --- | ---: | ---: |
| Spline interpolation | 58.610 ms | 0.409 ms |
| Realtime raster frame | 156.601 ms | 29.388 ms |

The raster-frame result is about **5.33× faster / 81.2% less frame time** for
this workload. It is not an application-wide speed multiplier, GPU benchmark,
or display-FPS promise. Path continuity and previously ignored controls are
intentionally corrected, so this is not a pixel-identical screenshot workload.
Saved density/profile defaults are unchanged. Performance will differ with
hardware, source size, presets, DPI, output layers and active audio/video work.

Run the same benchmark against each source checkout:

```sh
python tools/benchmark_realtime.py --source-tree /path/to/original --output before.json
python tools/benchmark_realtime.py --source-tree /path/to/patched --output after.json
```

## GPU assessment

**This patch does not add GPU rendering.** The moving canvas is still QPainter
on QWidget; exact inspection/export remain Matplotlib. Selecting a hardware
video encoder accelerates encoding, not creation of the 3D scene frames.
There is no shader/VBO-based realtime renderer in this inspected baseline.

The appropriate next step is an opt-in backend inside the existing Qt viewport,
not a new application or preset system. Qt documents QPainter support on
QOpenGLWidget, which could reuse the current drawing semantics as an initial
acceleration experiment. It would not automatically move feature extraction,
projection or path generation onto the GPU. Those remaining CPU tasks are why
removing the duplicate work first is useful to either backend.

Do not label an imported OpenGL library or a created context as proof of a
hardware GPU: software OpenGL implementations can also create valid contexts.
Before enabling a backend by default, require:

- Actual renderer/vendor/version diagnostics, an explicit backend selection,
  and a tested software fallback without blank viewports or startup failure.
- Side-by-side preset, alpha/width, camera, grid and trail comparisons; high-DPI
  resize, pause/refine, minimize/restore and context lifecycle checks.
- Correct stage-output framebuffer capture; the GPU path cannot assume a
  generic QWidget grab has identical behavior or negligible cost.
- Measured physical Windows integrated/discrete, Apple Silicon/Intel and Linux
  desktop results, plus remote/headless fallback. Preserve CPU exact export
  until a separate GPU export path has visual and deterministic-output tests.

Qt sources: [QOpenGLWidget](https://doc.qt.io/qt-6/qopenglwidget.html),
[QOpenGLPaintDevice](https://doc.qt.io/qt-6/qopenglpaintdevice.html),
[QOpenGLContext](https://doc.qt.io/qt-6/qopenglcontext.html).
Qt explicitly documents context/profile initialization, framebuffer capture,
resource lifecycle and remote-display limitations; these are compatibility
requirements, not optional cleanup after turning acceleration on.

## Remaining gaps found during this pass

**Preview feature parity:** the High quality/Full live profiles permit labels,
colorbar and/or ghost path, but the QPainter preview has no corresponding
artist implementations. Those features should be implemented using the same
options or accurately described as exact/paused-only. Real tube surfaces are
already documented as an exact/paused/export feature. This patch does not
claim to finish those preview features.

**High-DPI control:** the existing pixel-ratio-cap plumbing targets the
Matplotlib canvas, not the moving QWidget's backing pixels. The published
live-preview DPI claim therefore needs a proper implementation or narrower
wording. Do not lower global Qt DPI scaling to hide this; it can degrade the
whole interface. This remains separate from the geometry optimizations here.

**Stage output:** skipping disabled captures removes unnecessary work. Enabled
QWidget captures still repaint, and stage refresh can exceed the preview's
cadence. A shared frame cache with explicit invalidation is the next targeted
step, especially before a GPU backend is enabled.

These gaps remain recorded rather than silently called complete or replaced by
novelty visual styles. Physical driver/peripheral compatibility is not certified
by offscreen tests.
