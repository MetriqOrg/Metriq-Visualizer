# Live microphone input

Live Input is an embedded source for the main Metriq three-dimensional viewport. It is not merely a recording popup.

## Workflow

1. Select **Live input** in the command bar.
2. Choose **System default input** first unless a specific device is required.
3. Choose a live mapping and quality tier.
4. Select **Start input**.
5. Incoming features produce a rolling XYZ trajectory in the main visualizer while the dock shows supporting waveform and spectrum information.

The dock supports Birdsong-compatible, Spectral orbit, and Waveform trace mappings; Efficient, Balanced, and Full live quality; freeze/resume; clear trail; optional recording; WAV save; and analysis handoff.

## Capture architecture

The PortAudio callback only copies mono sample blocks into a bounded queue and optionally retains recording chunks. FFT, feature extraction, coordinate mapping, Qt painting, and WAV writing occur outside the callback. This keeps device capture independent of UI/render latency.

The live feature window includes RMS, peak, dominant frequency, spectral centroid, spectral bandwidth, flatness, zero-crossing rate, and onset/flux-like motion values. These are descriptive values, not calibrated sound-pressure measurements.

## Device startup and retry

The panel always provides a **System default input** route. That route calls PortAudio with `device=None` even when device enumeration fails. The engine then tries:

- the requested or native sample rate;
- 48 kHz;
- 44.1 kHz;
- low-latency and host-default latency modes.

A failed start leaves the controls available for another attempt rather than retaining a stale stream object.

## macOS permission

For a packaged `.app`, the bundle declares `NSMicrophoneUsageDescription`. The first start should trigger the operating-system permission request. For a source launch, macOS grants permission to the terminal/Python host rather than to the source directory.

When capture is denied, open **System Settings → Privacy & Security → Microphone**, enable the relevant application or terminal, quit/reopen that host if required, and select **Start input** again.

## Recording

Recording can be enabled before or during capture. **Save WAV** performs a transactional 16-bit PCM write. **Analyze capture** stops input, writes a temporary WAV, and sends it through the normal configurable analysis/cache/mapping workflow.

## Recognition boundary

Version 1.12.5 does not identify birds, instruments, speakers, alarms, or other sound classes. A future local recognizer should consume copied live windows or completed `AnalysisResult` objects outside the audio callback.
