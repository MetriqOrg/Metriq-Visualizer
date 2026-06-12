# macOS install and compatibility

Metriq Visualizer supports macOS, but user experience depends on how it is launched.
For non-technical users, use the packaged `.app` from GitHub Releases when available.
For developers, use the source install below.

## Recommended: packaged app

1. Open the latest GitHub Release.
2. Download the file named similar to:
   - `Metriq-Visualizer-macOS-arm64.zip` for Apple Silicon Macs.
   - `Metriq-Visualizer-macOS-x86_64.zip` for Intel Macs.
3. Unzip it.
4. Move `Metriq Visualizer.app` to `/Applications`.
5. Launch it from Finder.

If macOS blocks the app because it is unsigned, right-click the app, choose **Open**, then choose **Open** again. A fully notarized build requires an Apple Developer ID certificate and notarization credentials in GitHub Actions.

## Developer/source install

Install Homebrew first if needed. Then:

```bash
brew install python@3.11 ffmpeg

cd Metriq-Visualizer-main
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python metriq_visualizer_app.py
```

If the folder from GitHub is named something else, use that exact folder name. For example, GitHub's default source ZIP usually extracts to `Metriq-Visualizer-main`.

## Common macOS issues

### App opens but export fails

Install FFmpeg if running from source:

```bash
brew install ffmpeg
```

Packaged release builds include the Python app dependencies, but FFmpeg availability may still depend on the packaging method and platform. If export fails, install FFmpeg with Homebrew or use the legacy OpenCV export engine.

### PyOpenGL / PySide launch errors

Use Python 3.11 or 3.12. Avoid mixing system Python, Homebrew Python, and Conda in the same virtual environment.

### Apple Silicon vs Intel

Apple Silicon and Intel Macs should be packaged separately. Do not build one macOS binary and assume it works everywhere.
