# Lightweight runtime pass — 10 September 2026

## Scope and baseline

Second incremental pass on `MetriqOrg/Metriq-Visualizer`, extending PR #5 from
`83151d21325c76b18a3ebc4496a77164968ec6be` (the first optimization pass).
This is not a replacement application or a version bump. The interface,
creator presets, mappings, quality settings, project schemas, analysis data,
export formats and exact export renderer are retained. No dependency is added.
`main` is not changed by preparing or updating this pull request.

## Runtime changes

- **One shared current viewport frame.** The moving QWidget paints the same
  full-resolution raster that stage output requests. Identical-state captures
  no longer repeat projection, spline preparation or rasterization. The key
  covers source/live-buffer revision, time, camera, zoom, budget, theme, options,
  size, font and device-pixel ratio. Invalid live buffers do not replace the last
  valid frame. Returned pixmaps use Qt copy-on-write so consumers cannot corrupt
  the retained image. Initial snapshots polish widget styling before rendering.
- **Bounded memory, not a frame history.** Retain at most one viewport raster,
  up to 32 MiB of pixel payload. Above that limit, use the original full-resolution
  direct paint path; never reduce display resolution or point density to fit.
  This is a cache limit, not a total application-memory limit.
- **Cursor-only analysis redraws.** Cache the static plot and redraw the cursor
  and original foreground artists in their normal order. This preserves the
  legend, axes and spines rather than drawing the cursor over them. A deferred
  timestamp can be painted later even when the timestamp itself has not changed.
  Source/data, figure changes, resize, DPI changes and hiding invalidate caches.
  The additional cursor-background cache is capped at 8 MiB per materialized
  panel; oversized plots fall back to full rendering without losing detail.
- **Build hidden plots only when consumed.** Retain source references for unused
  spectrogram/chromagram/MFCC/trace tabs, not their plot artists and rasters.
  Opening a tab, explicit drawing, grabbing it, or including it in stage output
  materializes its full data with the latest cursor time. Replacing a pending
  source replaces the pending references. Collapsed analysis and video-hidden
  waveforms retain time without unnecessary cursor redraws.
- **Stage background lifecycle.** Solid-color and image backgrounds do not
  instantiate an unused audio/video pipeline. Video backgrounds still support
  playback and looping, with on-demand creation, pause while hidden/minimized,
  resume when restored and resource release on replacement/close. Closed stage
  windows are deleted rather than left behind. Stage refresh settings are retained.

## Measured comparison against the first pass

The same `tools/benchmark_lightweight.py` was run sequentially against the two
source snapshots in separate processes on the same Linux offscreen host.
100,000 source rows, 1,200 live points, 960 x 600 logical viewport, DPR 1,
spline detail 4, 20 measured frames after three warmups. Each unique viewport
state was painted once and then requested three more times for stage capture.
The spectrogram fixture is 256 x 2,048. No quality or frame-rate defaults changed.

| CPU operation | First pass | Second pass |
| --- | ---: | ---: |
| New viewport frame, median | 41.260 ms | 41.018 ms |
| Three additional captures of that same state, median | 122.482 ms | 0.152 ms |
| New frame plus three captures, median | 164.436 ms | 41.178 ms |
| Spectrogram cursor update, median | 45.784 ms | 1.444 ms |
| Mapped-trace cursor update, median | 39.585 ms | 10.639 ms |

The multi-consumer frame workload uses about **75% less CPU time**; spectrogram
cursor updates use about **97% less**, and trace cursor updates about **73% less**.
New-frame generation is essentially unchanged. This is removal of redundant
work, not an additional application-wide rendering multiplier or GPU speedup.
Qt's final screen compositor, real audio capture/output and physical display
frame pacing are not included. Host load and platform affect these timings.

For initial plot preparation with the Source tab selected, Python-tracked
retained allocations fell from **52,524,815 to 9,888,654 bytes** (about 81% less).
Tracked peak allocations fell from **84,315,366 to 13,545,672 bytes**. The fixtures
were allocated before tracing; this measures plot preparation only. Native Qt
allocations, including the shared viewport pixmap, are not fully represented;
these figures are **not total RAM/RSS reductions**. Preparation under allocation
tracing took 6.317 s versus 0.939 s; tracing adds overhead, so these are not
application-startup timings. Unopened plots incur their normal preparation cost
when first consumed. Executable/download size is unchanged by design.

## Validation

Local first-pass baseline: 113 tests, 111 passed and two macOS-only skips.
Second pass: 125 tests, 123 passed and the same two skips, including 12 new
focused tests. They cover exact cached-versus-direct viewport pixels, copy
ownership, same-state reuse, full-resolution memory fallback, camera/style/font/
source/live-buffer invalidation, lazy tab/stage materialization, current-time
handoff, collapsed panels and stage media/timer lifecycle.

Pixel tests compare cursor blitting against a normal full Matplotlib draw across
waveform, spectrogram, chromagram, MFCC and traces, including legend and spine
ordering. The focused suite also runs with Qt scale factors 1.5 and 2.0. Real
microphone/GPU certification is not implied; video lifecycle unit tests mock the
player, while the existing full suite retains its real software video/export
checks. Final four-platform results must be read from the current PR checks,
not inferred from the earlier revision's green status.

Local compile/import checks pass. Ruff 0.12.12 passes with the same UP038
exclusion used for the unmodified legacy preset loader in the first pass;
GitHub's existing quality workflow uses its current Ruff without this exclusion.
No existing regression test was removed or relaxed.

Run the same benchmark against each checkout:

```sh
python tools/benchmark_lightweight.py --source-tree /path/to/first-pass --output before.json
python tools/benchmark_lightweight.py --source-tree /path/to/second-pass --output after.json
python -m unittest discover -s tests -p 'test_*.py' -v
```

## Boundaries

The GPU viewport, live labels/colorbar/ghost-path parity gaps, moving-viewport
DPI-cap gap and other items in `RENDERING_OPTIMIZATION.md` remain separate work.
This patch does not claim to implement them or hide them by removing controls.
No shaders, renderer framework, dependency upgrades or new deployment services
are introduced. Full-scene CPU export is unchanged. The deliberate tradeoff is
small bounded caches in exchange for less repeated CPU work, while unused
analysis plots and stage decoders are not eagerly constructed.

Implementation references: Qt QWidget painting/grab behavior and QPixmap
implicit sharing; Matplotlib draw-event/background/artist blitting lifecycle.

- https://doc.qt.io/qt-6/qwidget.html
- https://doc.qt.io/qt-6/qpixmap.html
- https://matplotlib.org/stable/users/explain/animations/blitting.html
