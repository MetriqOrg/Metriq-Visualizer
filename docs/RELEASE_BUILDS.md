# Release builds

Compiled artifacts belong in GitHub Releases or workflow artifacts, not in the
source repository.

## Local PyInstaller build

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
python build/build_pyinstaller.py
```

The output is written under `build-out/dist`.

FFmpeg is not bundled by the build script. Bundling FFmpeg requires a separate
platform-specific licensing, codec, hardware, and update decision. The built
application detects a system FFmpeg and retains PNG/JPEG sequence output when
none is available.

## GitHub Actions

`.github/workflows/quality.yml` runs static checks, compilation, unit tests, and
an offscreen application smoke test.

`.github/workflows/build-release.yml` builds on Linux, Windows, and macOS when
manually dispatched or when a `v*` tag is pushed. It uploads platform artifacts
to the workflow run.

Suggested release process:

```bash
git switch main
git pull --ff-only
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p 'test_*.py' -v
git tag -s v1.12.5 -m "Metriq Visualizer 1.12.5"
git push origin v1.12.5
```

Review all generated artifacts before attaching them to a public release.

## Platform artifact names

- `Metriq-Visualizer-1.12.5-Linux-x86_64.tar.gz`
- `Metriq-Visualizer-1.12.5-Windows-x86_64.zip`
- `Metriq-Visualizer-1.12.5-macOS-arm64.zip`

Runner architecture determines actual compatibility. Apple Silicon and Intel
macOS should not be represented as interchangeable unless a universal build is
explicitly produced and tested.

## Signing

- macOS distribution should use Developer ID signing and notarization.
- Windows distribution should use Authenticode signing.
- Release checksum files should be generated after signing and packaging.
- Source ZIP checksums should be published separately from binary checksums.
