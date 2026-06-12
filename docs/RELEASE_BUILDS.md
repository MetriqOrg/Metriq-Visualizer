# Release builds

Do not commit compiled binaries into the source tree. Keep source code in Git, and attach compiled `.zip`, `.dmg`, `.AppImage`, or `.exe` files to GitHub Releases.

Recommended release assets:

| Platform | Asset name | Notes |
|---|---|---|
| macOS Apple Silicon | `Metriq-Visualizer-macOS-arm64.zip` | Build on `macos-14` or newer. |
| macOS Intel | `Metriq-Visualizer-macOS-x86_64.zip` | Build on Intel macOS runner if available, or maintain manually. |
| Linux x86_64 | `Metriq-Visualizer-Linux-x86_64.tar.gz` | Best for users who do not want Python setup. |
| Windows x86_64 | `Metriq-Visualizer-Windows-x86_64.zip` | Optional if Windows users request it. |

## Manual local build

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python build/build_pyinstaller.py
```

The output will be in `dist/`.

## GitHub Actions build

This repo includes `.github/workflows/build-release.yml`. It builds packaged artifacts when a version tag is pushed:

```bash
git tag v1.10.18
git push origin v1.10.18
```

Then create or edit the GitHub Release for that tag and attach the build artifacts, or allow the workflow to upload them as workflow artifacts first.

## Notarization

Unsigned macOS builds will trigger Gatekeeper warnings. For a polished public release, add Apple Developer ID signing and notarization secrets to GitHub Actions.
