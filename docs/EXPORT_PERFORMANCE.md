# Export performance

The export dialog uses the v1.10.18 layout and has no renderer selector. Frame
rasterization runs through the optimized CPU Agg path. The cached static layers,
reduced array copies, and compositing improvements remain enabled. Atomic output
replacement remains enabled, so incomplete exports do not replace the prior file.

Older profiles and saved states may still contain `renderer: "gpu"`, `"cpu"`,
or `"auto"`. The field is ignored and all exports use the normal CPU renderer.

## Desktop result

Measured by the user on a real display with VideoToolbox encoding, exporting a
91-frame 1080p clip:

| Path | Throughput |
| --- | ---: |
| Original renderer | 16.96 fps |
| Optimized CPU | 18.07 fps |
| Auto | 19.84 fps |
| GPU renderer | 10.73 fps |

At 720p, GPU rendering reached 13.4 fps versus 26 fps on CPU. GPU output had
SSIM 0.9989–0.9999, but that visual similarity did not offset its lower speed.
The GPU renderer was tried and rejected: it was slower at both tested sizes and
added a worker process plus shared-memory frame transfer. It does not ship.

## Next optimization candidates

These are investigation ideas only; none is implemented here. User profiling
puts `composite_HUD` (PIL composition) at about 24 ms/frame at 1080p, followed
by `scene_draw` at about 14 ms/frame.

| Stage | Candidate idea | Expected gain |
| --- | --- | --- |
| `composite_HUD` | Composite at final output size with fewer full-frame PIL conversions and intermediate buffers. | Highest remaining opportunity; potentially several ms/frame, depending on how much time is conversion and memory traffic. |
| `composite_HUD` | Cache fitted/resized static card content and invalidate it only when layout, size, or branding changes. | Avoid repeated resize work; gain depends on the static share of composition. |
| `scene_draw` | Reduce Matplotlib artist updates and redraw only changing artists; reuse projected geometry where camera state is fixed. | Potentially a few ms/frame; camera motion limits reuse. |
| `scene_draw` | Profile scatter, tube, and annotation artists separately, then batch or simplify the dominant artist work. | Unknown until measured by artist; likely smaller than the composition opportunity. |

These estimates are directional, not benchmark promises. Preserve bit-identical
output while evaluating any follow-up change.
