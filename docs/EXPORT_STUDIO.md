# Export Studio

Export Studio combines direct layout composition, output settings, true-3D preview, reusable profiles, and background rendering.

![Export Studio](images/export-studio-3d.png)

## Renderer parity

The geometry layer is not a screenshot or independent approximation. It uses the same `Matplotlib3DScene` as the main interactive viewport, including perspective, camera, point/line/tube modes, trail history, comet, head/halo/flash, axes, labels, and color scale. A project should therefore retain its material 3D appearance when moved from the main workspace into export.

## Composition workflow

1. Analyze a source and build the desired mapping/appearance in the main window.
2. Open **Export Studio**.
3. Choose a layout preset or arrange layers directly.
4. Select output format, resolution, frame rate, range, and encoder mode.
5. Scrub the Export Studio timeline and inspect the rendered frame.
6. Choose presentation elements such as title, watermark, axes, scene HUD, and timecode.
7. Save a `.mvexport` profile when the output/composition should be reused.
8. Start export; monitor progress or cancel safely.

## Layers

The frame can contain geometry, source preview, spectrogram, chromagram, MFCC, and mapped traces. Every layer has:

- enabled state;
- normalized X/Y position and width/height;
- explicit front-to-back order;
- content scale;
- contain, cover, or stretch fitting;
- content opacity and panel-background opacity;
- optional panel title.

Dragging changes position. The lower-right handle changes size. Arrow keys nudge by 1%; Shift+arrow nudges by 0.1%. Numeric fields provide exact placement. Undo/redo covers layout changes. Snapping aligns edges and centers; safe-area percentage controls the guide.

## Layout presets

- **Balanced** — dominant geometry above a bottom analysis band.
- **Geometry focus** — near-full-frame 3D geometry.
- **Analysis focus** — larger source/scientific comparisons.
- **Overlay** — full-frame geometry with floating source/spectrogram layers.
- **Vertical social** — 9:16 geometry-first arrangement.

Presets are starting points and do not lock controls.

## Presentation controls

Profiles can preserve project title/subtitle, watermark, axes, axis labels, colorbar, in-scene technical HUD, and global timecode. Creator profiles default to a clean scene HUD/timecode state; the 4K analysis profile retains technical context.

## Output profiles

`.mvexport` is JSON-based and versioned. Schema 4 stores dimensions, frame rate, format, quality, audio choice, range, title/subtitle/watermark, encoder mode, six-layer layout, axes/labels/colorbar, scene HUD, and timecode. Earlier profiles remain readable; unknown future versions are rejected rather than guessed.

Visual mapping and 3D appearance are inherited from the open project/preset so Export Studio cannot silently diverge from the main viewport.

## Encoders and formats

Automatic H.264/HEVC mode probes platform-relevant hardware encoders and falls back to software. Explicit NVIDIA NVENC, Intel Quick Sync, Apple VideoToolbox, AMD AMF, and software modes are available. Hardware advertisement alone is insufficient; candidates must pass a bounded encode probe.

Supported formats are MP4 H.264, MP4 H.265/HEVC, WebM VP9, MOV ProRes 422 HQ, MOV ProRes 4444, animated GIF, PNG sequence, and JPEG sequence.

## Range and audio

Start/end values use source seconds. Frame count derives from duration and selected fps. Audio is offered only when the source exposes a usable audio path and the format supports it. Profiles requesting audio are automatically disabled for silent/table sources.

## Image sequences

PNG/JPEG exports use `frame_000001` naming plus `sequence.json`. A non-empty destination is never mixed with a new sequence; the exporter creates a collision-free child directory. Exact dimensions and expected frame counts are enforced.

## Cancellation and destination safety

Video is encoded to a unique same-directory temporary file and atomically replaces the selected destination only after success. Failure or cancellation preserves any pre-existing creator file. A failed sequence attempt removes only its own files and leaves prior directories and unrelated content untouched.
