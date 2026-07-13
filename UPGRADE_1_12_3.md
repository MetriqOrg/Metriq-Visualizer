# Upgrade to Metriq Visualizer 1.12.3

v1.12.3 is an additive refinement of the restored v1.12.2 application. It keeps the true-3D workspace, audio playback, bottom analysis dock, live input, and Export Studio intact.

## Changes that affect existing users

### Light mode now includes the visualizer

Switching themes now updates the Matplotlib figure, genuine 3D axes, panes, grid, ticks, formula labels, scene HUD, point labels, colorbar, ghost path, and empty-state canvas. Light mode no longer leaves a dark rendering surface inside a light application shell.

### Legacy mappings and visual styles

The original mapping names and compatible feature aliases are restored. A built-in `Birdsong` mapping is included as a general local fallback. Existing creator-authored Birdsong or other `.mvpreset` files remain authoritative:

- user presets in `~/.metriq_visualizer/presets` take precedence over bundled display names;
- `METRIQ_PRESET_PATH` can add one or more preset directories;
- presets already present in an existing repository are never overwritten by the additive merge tool;
- v1.10-era state sections and the legacy `fill` layout mode are translated when loaded;
- unknown preset sections are retained rather than discarded.

Mapping and visual style are separate controls. Selecting Data Disco, Glowstick, or Neon Lights changes appearance, camera, and geometry sampling without replacing X/Y/Z/color/size formulas or the selected live-performance profile. Loading a complete preset file still applies its complete compatible state.

The bundled `Birdsong` entry is a compatibility fallback, not a claim that an unavailable private creator preset was reconstructed exactly. When the target repository or user preset directory contains the original file, that file is preserved and takes precedence.

### Scalable live performance

The selected profile affects only the interactive viewport:

| Profile | Default budget | DPI ceiling | Intended use |
|---|---:|---:|---|
| Fast preview | 650 points, 15 fps | 1.0× | Lowest-latency editing on ordinary or high-DPI systems |
| Balanced | 1,200 points, 12 fps | 1.25× | Default straight-line playback and editing proxy |
| High quality | 1,800 points, 8 fps | 1.5× | Higher-density proxy with optional labels and colorbar |
| Full live simulation | 6,000 points, 5 fps | Native | No proxy substitutions; throughput is hardware- and scene-dependent |

Fast preview removes tubes, smoothing, labels, colorbar, ghost path, comet, and flash. Balanced uses straight points and line while retaining temporal accents. High quality raises density and permits labels/colorbar while continuing to suppress tubes, smoothing, and ghost path. Full live simulation makes no proxy substitutions.

Pending Matplotlib draws are always coalesced. With **Adapt live density under load** enabled, completed draw time is measured, only the live point ceiling is reduced when the target is missed, and density is restored gradually when sustained headroom returns. The status line exposes effective/configured points and measured draw latency.

The selected profile remains active while paused by default. **Refine to full scene when playback stops** is available as an explicit opt-in. Neither live profiles nor adaptation change saved creator settings or Export Studio output.

### Efficiency corrections

- The selected live point budget is applied before the first scene draw.
- Playback-head, visible-range, and comet-window calculations use binary search on monotonic timestamps.
- Full XYZ geometry is cached inside the scene.
- Unchanged camera, axes, labels, colorbar, and zoom state are not reapplied every playback frame.
- High-DPI playback can render below the host's native pixel ratio while idle refinement and export remain native/full resolution.

## Repository-safe application

From the extracted package:

```bash
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer --dry-run --json
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer
```

Review actions marked `preserved`; these are existing creator preset files deliberately left unchanged.

Then validate:

```bash
cd /path/to/Metriq-Visualizer
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m pytest -q
git diff --check
git status --short
```

The merge tool does not replace `.git`, reset history, delete unrelated files, or overwrite existing creator presets.
