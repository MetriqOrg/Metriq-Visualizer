# Interactive performance architecture

Metriq Visualizer separates moving interaction from exact inspection and export.

## Why the earlier build was slow

A genuine Matplotlib `mplot3d` scene is appropriate for accurate paused inspection and frame export, but it is CPU-rasterized. Repainting axes, panes, ticks, labels, colorbar, depth-sorted artists, and a Retina-scale backing image for every audio position update is substantially more work than moving a playback head through already-mapped geometry.

Version 1.12.5 therefore uses two coordinated renderers:

| State | Renderer | Purpose |
|---|---|---|
| Playback, autorotation, camera drag, live microphone | Qt/QPainter realtime renderer | Low-latency motion using cached XYZ geometry and camera state |
| Paused/refined scene | Matplotlib 3D renderer | Exact axes, labels, tubes, colorbar, and inspection |
| Export Studio | Matplotlib 3D frame renderer | Deterministic full-quality output |

The realtime renderer is not a separate mapping model. It consumes the same geometry, timestamps, colors, sizes, camera, trail state, spline interpolation, and accent parameters as the exact scene. Grid, axes, and data use one projection, so orbit and zoom apply to the entire coordinate field.

Path segments and point sprites are sent to Qt in bounded color/width batches rather than one Python draw call per primitive. This preserves a stepped live gradient while substantially reducing interpreter-to-Qt overhead; the exact paused/export renderer remains unchanged.

## Workload controls

- **Live point ceiling** bounds the trajectory submitted to the moving renderer.
- **Target redraw rate** limits UI paint frequency; stale frames are coalesced rather than queued.
- **Adaptive density** reduces only the current live point ceiling when measured frame cost exceeds the target and restores it conservatively.
- **High-DPI ceiling** reduces live backing-pixel cost on Retina/high-DPI displays; exact paused rendering and export remain independent.
- **Tube proxy** uses the selected smoothed centerline while moving instead of rebuilding polygonal tube surfaces on every tick.
- **Cached bounds/projection state** avoids repeated feature analysis and geometry mapping during playback.

## Profiles

| Profile | Default live budget | Target rate | Moving-scene behavior |
|---|---:|---:|---|
| Fast preview | 650 points | 15 fps | Lowest density; spline and selected temporal accents remain visible |
| Balanced | 1,200 points | 12 fps | Recommended normal-computer profile |
| High quality | 1,800 points | 8 fps | Denser trajectory and selected presentation detail |
| Full live simulation | 6,000 points | 5 fps | Highest live density; exact tube surfaces still require paused/refined rendering |

A higher profile spends more work per frame; it does not promise a higher frame rate. Export Studio always uses its own requested resolution and quality.

## Practical recommendations

- Use **Balanced** for playback and editing.
- Use **Fast preview** on high-DPI laptops or complex sources.
- Keep **Adapt live density under load** enabled.
- Keep **Refine to full scene when playback stops** off while making rapid changes; enable it for exact paused inspection.
- Judge final tube, label, colorbar, and high-resolution output in Export Studio rather than from a moving preview.
