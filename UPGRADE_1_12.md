# Upgrade to Metriq Visualizer 1.12.2

v1.12.2 is the corrective release for the v1.12 application architecture. It preserves the playback, export, cache, live-input, and reliability work from v1.12.1 while restoring the original Visualizer's real interactive 3D workspace as the product core.

## Why this release exists

The v1.12.0/v1.12.1 shell rendered the main visualization through a flattened projection. That removed material behavior present in the public v1.10.x application: genuine 3D axes, perspective, orbit, depth-aware trails, tube meshes, and a temporal head/comet/flash treatment. v1.12.2 removes that replacement and routes both the main viewport and Export Studio through one shared true-3D scene.

## Safe merge into an existing checkout

From the extracted v1.12.2 package:

```bash
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer --dry-run --json
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer
```

Then validate inside the checkout:

```bash
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m pytest -q
python -m ruff check .
python -m compileall -q .
git diff --check
git status --short
```

The merge tool preserves `.git`, branches, tags, remotes, and unrelated files. It does not initialize Git, delete target files, reset history, or force-push.

## Compatibility

- Existing `.mvproj`, `.mvpreset`, and `.mvexport` files remain readable.
- Export profiles saved by v1.12.2 use schema 4, adding explicit in-scene HUD and timecode choices.
- Visual projects now persist comet/flash duration and head/halo/flash scale controls.
- Stale v1.12.0 analysis caches remain invalidated by the current cache engine revision.

## Workspace changes

- The real 3D viewport is dominant and interactive.
- Analysis/source panels live below it and start collapsed.
- Dragging the 3D canvas orbits the scene; the wheel zooms it.
- Manual camera interaction disables autorotation so playback does not immediately overwrite the chosen view.
- Export Studio uses the same 3D renderer and therefore matches the main workspace materially.

## Rollback

Before committing, normal Git commands can restore the working tree. The merge tool also stores replaced files under `.metriq-merge-backup/<timestamp>/`; do not commit that directory or `.metriq-merge-report.json`.
