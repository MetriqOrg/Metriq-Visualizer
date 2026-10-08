# Export renderers

`ExportOptions.renderer` accepts `auto`, `cpu`, or `gpu`; the default is `auto`.
The MP4 export flow adds one renderer selector after the existing save dialog.
The live preview, presets, other controls, and layouts are unchanged. The choice
is saved in session/profile state and read by queued/batch export options. Old
state without the field uses `auto`.

| Choice | Behavior |
| --- | --- |
| Auto (identical CPU output) | Optimized serial Agg, the fastest verified identical-output path in the completed benchmark medians. GPU is never selected automatically. |
| CPU (reference) | Serial matplotlib Agg, with identical-output composition optimizations. |
| Fast (GPU) | Explicit opt-in OpenGL geometry rasterization. Uses the existing Agg projection, axes, labels, HUD, panels and PIL card composition. Falls back to serial CPU if context creation or a frame fails. |

The renderer choice is independent of the existing **Engine** encoder choice.
For example, CPU-rendered frames can still be H.264 encoded by VideoToolbox.
`auto` never selects the GPU renderer: its look and speed have not been verified
on a real display session. The GPU path can differ in antialiasing, line widths,
transparent overlap, tube faces and annotation layering. It is not advertised as
pixel-identical. Even a future SSIM ≥ 0.97 result would not establish exact equality.

## Profile first

The first profile used `scratch-01/common.wav`, `analyze_media`, `build_geometry`,
and `render_export_video`, with default balanced layout, panels/preview enabled,
30 fps and `end_time=3.0`: **91 frames**. The file itself is about 8 seconds;
this export is its first 3 seconds, including the endpoint frame.

`bench2.py`'s audio size formula `energy` is not available in this repo's audio
features. All comparisons instead use `rms`, sorted audio features' first three
entries (`chroma_A`, `chroma_As`, `chroma_B`) for XYZ, `time` for color, and
`max_points=2000`. Analysis/geometry preparation is outside export timing.

| Initial original-path measurement | 720p | 1080p |
| --- | ---: | ---: |
| End-to-end seconds / fps | 25.40 / 3.58 | 33.18 / 2.74 |
| Agg 3D scene draw, ms/frame (includes axes and scene HUD) | 95.54 | 99.38 |
| Geometry state/update + conversion outside that draw, ms/frame | 22.95 | 27.21 |
| Panel playhead updates, ms/frame (all four panels) | 1.40 | 2.48 |
| Canvas-to-array, ms/frame equivalent* | 1.40 | 1.04 |
| PIL fit/resize/cards/project HUD composition, ms/frame | 126.96 | 189.44 |
| FFmpeg pipe writes, ms/frame | 7.30 | 9.31 |
| Encoder drain after last frame, total ms | 18.50 | 39.08 |
| Independent encode of 91 pre-rendered frames, total seconds | 0.82 | 1.05 |
| Session initialization, total seconds | 0.65 | 0.66 |

*Conversion includes 91 geometry canvases and four panel canvases rendered once
at initialization. The conversion row is nested inside geometry/init timing;
do not add it again. Panels already cache their plots. Scene HUD is inside the
Agg draw; project title/watermark are inside composition. Encode runs concurrently
with drawing/pipe transfer, so encode-only time is a diagnostic throughput check,
not an additional serial stage. Remaining wall time includes startup, encoder
probes, RGB packing, callbacks and orchestration.

The sandbox could not use VideoToolbox; exports automatically used `libx264`.
These measurements do **not** reproduce the supplied desktop result of 14.0 s /
6.5 fps with VideoToolbox. They establish that drawing and composition dominate
this run, rather than the encoder.

## Implementation and measured speed

Static card backgrounds/titles and the audio-only preview overlay are reused
within a session. Cache entries are invalidated on output-size or layout changes;
alpha compositing uses new images, so cached layers are never mutated. Redundant
RGBA input copies are removed. The resize filter, integer rounding, draw calls,
artist ordering, panel cursors and final compositing order are preserved.

Two Agg workers each own a renderer and video reader. Frames are consumed in
order, with at most two futures outstanding. Worker/submission failure resumes
serially from the first unfinished frame, preserving order and count. Worker
frame waits are bounded to 120 s; failure/early-close cleanup terminates workers
with bounded joins. Auto and CPU use one renderer. The explicit `iter_export_frames(..., parallel=True)`
path is available for further benchmarking, but is not selected automatically:
the completed whole-export medians favored serial CPU at both resolutions.
No longer-clip crossover is assumed.

Full-export benchmark results are recorded in `GPU_EXPORT_MEASUREMENTS.json`.
The table below is generated from three repetitions with alternating case order;
all use 91 frames, the same options and FFmpeg encoder. Timings varied with host
load, so raw runs and ranges are retained rather than treating a single run as
a reliable speedup.

| Resolution | Original fps (median; time range) | CPU fps (median; time range) | Auto fps (median; time range) | Auto fps gain |
| --- | ---: | ---: | ---: | ---: |
| 720p | 6.91 (13.18 s; 12.02–19.01 s) | 7.79 (11.68 s; 9.11–15.32 s) | 8.35 (10.89 s; 8.62–14.63 s) | 21.0% |
| 1080p | 5.33 (17.08 s; 14.55–25.14 s) | 6.41 (14.19 s; 12.97–19.53 s) | 6.39 (14.24 s; 8.96–16.23 s) | 20.0% |

CPU and Auto use the same serial renderer; differences between their measured
times are run-to-run variation. These are sandbox results, not a claim of
VideoToolbox or GPU acceleration.

An additional paired check alternated **60 original/optimized frames at identical
times** within one process, asserting exact equality after every pair. This
reduces bias from host load changing between whole exports:

| Rendering only; no encoder or startup | Original fps | Optimized serial CPU fps | Gain |
| --- | ---: | ---: | ---: |
| 720p | 7.40 | 8.49 | 14.7% |
| 1080p | 6.14 | 6.92 | 12.8% |

These rendering-only numbers are not end-to-end export fps. The three-repeat
export table above is the evidence for export throughput. GPU fps is **unverified**;
a GPU-requested export that fell back to CPU is not a GPU benchmark.

## Look parity

The comparator loads the original render module directly from
`release/1.13-rollback` using `git show`. It compares full RGBA frames, including
panels/HUD, against optimized serial CPU and spawned CPU workers.

| Frame | History / render mode | CPU SSIM, 720p / 1080p | Worker SSIM, 720p / 1080p | GPU SSIM |
| --- | --- | ---: | ---: | --- |
| 0.25 s | Trail fade / Points + line | 1.000 / 1.000 | 1.000 / 1.000 | Unverified: context unavailable |
| 1.50 s | Cumulative reveal / Points + line | 1.000 / 1.000 | 1.000 / 1.000 | Unverified: context unavailable |
| 2.75 s | Full static / Tube + points | 1.000 / 1.000 | 1.000 / 1.000 | Unverified: context unavailable |

All twelve CPU/worker comparisons have **zero differing RGBA pixels**, mean
absolute channel difference 0, maximum difference 0, and 99th percentile
difference 0. Equality is asserted with `np.array_equal`, not an SSIM tolerance.
SSIM uses RGB, an 11×11 Gaussian window (sigma 1.5), population covariance,
255 range and a five-pixel excluded border.

Side-by-side PNGs and raw JSON are in `~/Developer/MV/scratch-07/final-parity/`.
`*-cpu.png` and `*-auto-worker.png` show original on the left, optimized on the
right. `*-gpu-fallback.png` explicitly shows a CPU fallback on the right; these
are not evidence of GPU look parity. Actual GPU rasterization could not be
verified because the child process could not connect to the macOS display session.

## Context failure and atomic output

The GPU worker creates its own `QGuiApplication`, offscreen surface, compatibility
OpenGL context and multisampled export-size framebuffer on its main thread.
The parent never shares GUI contexts or invokes Qt GUI objects from its export
thread. Driver/context failures and crashes are confined to the worker.

Startup waits are bounded to 20 s and frame waits to 15 s. Large RGBA data travels
through a mapped temporary file; only small readiness messages use the pipe.
This avoids a large `recv()` blocking after a partial frame message. Failure
terminates the worker, releases the mapping and logs a CPU fallback. The failed
frame is rendered on CPU, and subsequent frames stay CPU for that session.

Qt framebuffer images use premultiplied alpha; conversion to RGBA8888 supplies
straight alpha for PIL ([Qt framebuffer documentation](https://doc.qt.io/qt-6/qopenglframebufferobject.html#toImage)).
Depth-sorted scatter offsets are kept with the matching colors and sizes, verified
without a GL context by a projection regression test.

FFmpeg and legacy OpenCV exports both stage the encoded/muxed `.mp4` before
atomic replacement through a same-directory `.partial` file. A rendering or
encoding failure preserves the previous destination and removes staging files.
The multiprocessing entry point includes early `freeze_support()` for packaged
workers. A rebuilt, signed desktop bundle was not tested in this task.

## Verification and desktop commands

Targeted tests cover exact CPU worker equality, video preview seek/order, tube
geometry with labels/colorbar/watermark, cache equality under clipping/overlap and
all fit modes, renderer choice/state, GPU exception and real-process startup/frame-timeout fallback,
CPU worker failure/timeouts, and atomic render-failure cleanup.

Run the following in a **desktop Terminal outside the sandbox**, with a logged-in
macOS display session. The comparator must keep `active_renderer=gpu`; a fallback
is a failed GL verification, even if its pixels match perfectly.

```sh
cd ~/Developer/MV/Metriq-Visualizer
MPLCONFIGDIR=~/Developer/MV/scratch-07/mpl \
  ~/Developer/MV/.venv/bin/python tools/benchmark_gpu_export.py \
  --fixture ~/Developer/MV/scratch-01/common.wav \
  --output ~/Developer/MV/scratch-07/desktop \
  --benchmark --repeats 3 --parity --gpu --require-gpu
```

Exit 0 means actual GPU frames were obtained and all three frames at both sizes
had SSIM ≥ 0.97. Exit 2 means GL was unavailable or fell back; exit 3 means actual
GPU SSIM missed 0.97. In every case inspect `desktop/results.json` and the PNGs:
GPU must remain an opt-in unless exact-output acceptance is established. The
benchmark includes before/CPU/Auto/GPU exports and independent encoder throughput.

For the non-GL checks:

```sh
MPLCONFIGDIR=~/Developer/MV/scratch-07/mpl \
  ~/Developer/MV/.venv/bin/python -m pytest \
  tests/test_gpu_export.py tests/test_export_atomic.py -q -n 2
```

Then launch the app on the desktop with
`~/Developer/MV/.venv/bin/python metriq_visualizer_app.py`, load the WAV, and use
Export MP4. Check the three choices, Auto and CPU appearance, Fast (GPU) output,
and normal preview during export. Repeat in a newly built bundle to verify the
packaged worker entry point; that GUI/bundle exercise was not possible here.
