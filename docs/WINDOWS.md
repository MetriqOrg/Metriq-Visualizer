# Windows installation

## Source launch

1. Install 64-bit Python 3.10 or newer and enable **Add Python to PATH**.
2. Install FFmpeg and ensure `ffmpeg.exe` is on PATH.
3. Extract the source ZIP to a normal writable directory.
4. Run `run_windows.bat`.

The launcher creates `.venv`, installs the pinned minimum dependencies, and
starts the application.

## FFmpeg

Open Command Prompt and verify:

```bat
ffmpeg -version
```

When FFmpeg is not found, encoded video/audio export and broad media decoding
are unavailable. Table work and PNG/JPEG sequences remain available.

## Microphone permission

Open **Settings → Privacy & security → Microphone** and allow desktop apps to
use the microphone. Some devices may be held exclusively by communication or
recording software; close those applications or choose a different input.

## Hardware encoding

Automatic mode may select NVENC, Quick Sync, or AMF when the corresponding
FFmpeg encoder and driver are usable. A failed probe falls back to software.
Explicit hardware mode is useful for diagnosis but still retains software as a
compatibility fallback.
