# Codex import and GitHub handoff

This archive contains the complete Metriq Visualizer 1.12.5 source tree. It can be opened independently or merged into an existing clone while preserving `.git`, branches, tags, remotes, and unrelated files.

## Safe merge

From the extracted v1.12.5 folder:

```bash
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer --dry-run --json
python tools/merge_into_repo.py --target /path/to/Metriq-Visualizer
```

The tool limits input to checksum-verified manifest files, rejects unsafe targets and symlinks, backs up changed files, copies through unique temporary files, verifies SHA-256 digests, and atomically replaces destinations. It never deletes target files or modifies Git history. Existing `presets/*.mvpreset` destinations are reported as `preserved` and are not overwritten by bundled styles.

Local merge artifacts are written to:

```text
.metriq-merge-backup/<UTC timestamp>/
.metriq-merge-report.json
```

They are added to `.git/info/exclude`, not the committed `.gitignore`.

## Review and validate

```bash
cd /path/to/Metriq-Visualizer
git switch -c feature/metriq-visualizer-1.12.5
git status --short
git diff --stat
git diff
git diff --check

python -m pip install -r requirements.txt
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg python -m pytest -q
python -m ruff check .
python -m compileall -q .
```

With a desktop environment, launch `python metriq_visualizer_app.py` and verify the real interactive 3D viewport, complete light scene, legacy mapping/visual presets, tiered playback profiles, adaptive live density, functional FFT/band extraction controls, stable orbit/autorotation/whole-scene zoom, audio-synchronized playback, bottom analysis dock, embedded live-input dock feeding the main 3D view, and Export Studio.

## Commit and push

```bash
git add -A
git commit -m "Release Metriq Visualizer 1.12.5"
git push -u origin feature/metriq-visualizer-1.12.5
```

Use a normal pull request. The source archive contains no replacement `.git` directory and performs no GitHub authentication.
