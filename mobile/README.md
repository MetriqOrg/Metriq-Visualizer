# Metriq Visualizer Mobile — GPU foundation

This directory is an additive native mobile target for Metriq Visualizer. It is
not a Python desktop wrapper and does not replace the existing application.

## Implemented in this vertical slice

- Qt Quick touch interface for phones and tablets.
- Portable Qt scene-graph shader renderer. Qt RHI selects Metal, Vulkan,
  OpenGL/OpenGL ES, or Direct3D according to the platform and runtime.
- Explicit renderer diagnostics and a CPU-painted fallback when the Qt scene
  graph is running in software mode.
- Retained geometry buffers: camera/time changes update uniforms instead of
  rebuilding the trajectory.
- Deterministic demo source, bounded live trajectory, touch orbit/zoom,
  playback, and the existing core appearance controls.
- Local microphone capture with permission handling, bounded buffering, and
  analysis on a worker thread.
- `.mvpreset` schema 4 and `.mvproj` schema 2 load/save compatibility,
  preserving unknown state sections.
- Imported mapped CSV/TSV data.
- Android microphone manifest and iOS microphone/document metadata.
- Host-side C++ tests and a QML startup smoke path.

## Deliberate boundaries

This foundation does not claim full desktop feature parity yet. File-based
full scientific DSP, every Matplotlib analysis panel, source-video analysis,
GPU-native video export, app-store signing, and physical-device certification
remain later milestones. The source schemas and controls are retained so those
features can be added without inventing a second Visualizer format.

## Host preview build

Install Qt 6.8.3 or newer with Qt Quick, Qt Multimedia, and Qt Shader Tools.

```sh
cmake -S mobile -B mobile/build-host -DBUILD_TESTING=ON
cmake --build mobile/build-host --config Release
ctest --test-dir mobile/build-host -C Release --output-on-failure
```

Run the host preview:

```sh
./mobile/build-host/MetriqVisualizerMobile
```

The exact executable location varies with the generator and operating system.

## Renderer selection

Qt normally selects the best supported RHI backend. For diagnostics or testing:

```sh
QSG_RHI_BACKEND=vulkan ./MetriqVisualizerMobile
QSG_RHI_BACKEND=metal ./MetriqVisualizerMobile
QSG_RHI_BACKEND=opengl ./MetriqVisualizerMobile
QSG_RHI_BACKEND=software ./MetriqVisualizerMobile
```

The application reports the backend that actually initialized. Software mode
uses `CpuVisualizerItem`; GPU modes use the retained shader renderer.

## Android

Open `mobile/CMakeLists.txt` as a Qt Android project in Qt Creator, or configure
with the Qt Android toolchain and a configured Android SDK/NDK. The package
source under `platform/android` declares optional microphone hardware and asks
for `RECORD_AUDIO` only when live input is used.

## iOS

Configure with the Qt iOS kit and Xcode generator. `platform/ios/Info.plist.in`
contains the microphone purpose string and Visualizer document declarations.
Signing identifiers and store provisioning remain deployment configuration,
not source-controlled credentials.

## Compatibility contract

The mobile model consumes the same state section names used by desktop:
`mapping`, `geometry`, `visual`, `performance`, `extraction`, `layout`, and
`session`. Unknown sections are retained when a loaded preset or project is
saved again. Mobile-only state belongs under `mobile` and must remain optional
for older desktop builds.
