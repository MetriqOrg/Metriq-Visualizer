# macOS installation

## Source launch

```bash
brew install python ffmpeg portaudio
cd Metriq-Visualizer-v1.12.7
./run_macos.command
```

Python 3.11 or 3.12 is recommended. Keep Homebrew/system/Conda/pyenv interpreters separate rather than mixing them in one virtual environment.

## Microphone permission

Packaged application builds include an `NSMicrophoneUsageDescription` entry and the Metriq bundle identifier. The first input attempt should request microphone access.

For source launches, permission belongs to the terminal application or Python host. If capture does not start:

1. Open **System Settings → Privacy & Security → Microphone**.
2. Enable Metriq Visualizer, Terminal, iTerm, or the Python host used to launch it.
3. Quit and reopen the affected host if macOS does not apply the change immediately.
4. In Live Input, select **System default input**, choose **Host native**, and retry.
5. Close other applications using exclusive input and verify `portaudio`/`sounddevice` are installed in the active interpreter.

## Retina performance

The application uses a low-latency Qt renderer while playback, autorotation, camera drag, or live input is moving. The exact Matplotlib scene is retained for paused inspection and export. Balanced or Fast Preview plus adaptive density is recommended on high-DPI displays.

## Gatekeeper and signing

Unsigned packaged builds may be blocked. A public binary should use a Developer ID certificate and notarization. Source launches normally avoid the same bundle warning, though downloaded scripts can still carry quarantine metadata.

## Apple Silicon and Intel

Use a build created for the correct architecture unless the release explicitly states that it is universal. Python wheels and PyInstaller outputs are architecture-specific.
