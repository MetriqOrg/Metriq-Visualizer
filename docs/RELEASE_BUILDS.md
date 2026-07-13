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

`.github/workflows/build-release.yml` builds Linux x64, Windows x64, macOS
Apple Silicon, and macOS Intel artifacts when manually dispatched or when a
`v*` tag is pushed. Tagged builds publish a GitHub Release with generated notes
and a `SHA256SUMS.txt` manifest.

Suggested release process:

```bash
git switch main
git fetch origin --prune
git pull --ff-only origin main
python tools/verify_source.py --imports
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -p 'test_*.py' -v
git tag -a v1.12.8 -m "Metriq Visualizer 1.12.8"
git push origin main v1.12.8
```

Review the completed workflow, each platform artifact, and `SHA256SUMS.txt`
before announcing a public release. Use a signed tag instead of an annotated
tag when a release-signing key is available.

## Platform artifact names

- `Metriq-Visualizer-v1.12.8-Linux-x86_64.tar.gz`
- `Metriq-Visualizer-v1.12.8-Windows-x86_64.zip`
- `Metriq-Visualizer-v1.12.8-macOS-arm64.zip`
- `Metriq-Visualizer-v1.12.8-macOS-x86_64.zip`
- `SHA256SUMS.txt`

Runner architecture determines actual compatibility. Apple Silicon and Intel
macOS should not be represented as interchangeable unless a universal build is
explicitly produced and tested.

## Signing

- macOS public distribution requires a Developer ID Application certificate,
  hardened-runtime signing, and Apple notarization. The repository's ad-hoc
  signature keeps the self-updater verifiable during local development, but it
  is not a substitute for Developer ID distribution.
- Windows public distribution requires an Authenticode certificate and
  timestamping service.
- Add the corresponding protected GitHub Actions secrets before treating a
  cross-platform release as publicly signed; never commit certificates,
  private keys, passwords, or API credentials.
- Checksums are generated after packaging. Source-archive checksums, if
  published, stay separate from binary checksums.
