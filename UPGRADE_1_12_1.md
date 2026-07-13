# Upgrade to Metriq Visualizer 1.12.1

> **Superseded:** v1.12.2 restores the original true-3D workspace and should be used instead of v1.12.1.

Version 1.12.1 is a complete source-tree repair release. It supersedes the
broken 1.12.0 package, which did not connect source audio to a playback engine
and could continuously postpone visual rendering while Play was active.

## From the public 1.10.x repository

Use `tools/merge_into_repo.py` to preserve the existing Git repository and copy
all current source files into the working tree:

```bash
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer --dry-run
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer
```

Review the report, then run:

```bash
cd /path/to/Metriq-Visualizer
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen python -m pytest -q
python -m ruff check .
python -m compileall -q .
git diff --check
git status --short
```

The merge process does not delete target files, initialize Git, reset history,
or force-push. Existing changed files are copied into a timestamped local
`.metriq-merge-backup` directory before atomic replacement. Final-component and
parent-directory symlinks are rejected.

## From 1.11 or 1.12.0

The 1.11 overlay is no longer required. Do not build on the defective 1.12.0
package. Extract 1.12.1 into a new directory for a standalone checkout, or merge
it into the existing repository as described above.

Existing `.mvproj`, `.mvpreset`, and `.mvexport` files remain readable. Export
profile schema versions 1 and 2 are accepted. Saving a profile in 1.12.1 writes
schema 3, which also retains title, subtitle, watermark, axis, label, and color
scale presentation choices.

## Behavior changes

- Source audio is played through Qt Multimedia and synchronized to the visual
  timeline. A local visual clock keeps the interface moving when a host audio
  backend stalls or is unavailable.
- Playback rendering uses a dedicated continuous timer instead of the idle
  preview debounce timer.
- The default source and analysis panels are docked below the geometry field.
- Bundled export profiles use the corrected bottom-dock layouts.
- The analysis cache schema and engine version changed, so stale 1.12.0 cache
  entries are ignored automatically.
- Existing export files are not replaced until encoding succeeds. Failed or
  cancelled exports leave prior creator files intact.
- Image sequences never merge into a non-empty selected directory; a new child
  directory is created instead.

## Local state and cache

Recent-source and recoverable-session state is stored through `QSettings`.
Compressed analysis cache files use the operating system's standard user cache
directory. Neither is committed to the repository, and both can be cleared
without affecting source media or projects.

## Dependencies

Python 3.10 or newer is required. FFmpeg is required for broad media decoding
and encoded video/GIF output. PortAudio is required only for live microphone
capture. Qt Multimedia is supplied by PySide6 and handles source-audio playback.
