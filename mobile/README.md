# Metriq Visualizer Mobile — GPU foundation

This directory contains the first production-oriented mobile vertical slice for
Metriq Visualizer. It is additive to the Python desktop application and does not
replace or reduce the existing desktop feature set.

## What runs now

- Qt Quick touch interface suitable for phones, tablets, and desktop validation.
- A graphics-API-independent Qt Quick scene-graph material compiled for Metal,
  Vulkan, Direct3D, and OpenGL/OpenGL ES by Qt Shader Tools.
- Static scene geometry retained by the scene graph; camera, playback progress,
  trail visibility, point sizing, and presentation controls update through a
  uniform buffer rather than rebuilding the data mesh.
- CSV/TSV/TXT import, demo data, `.mvpreset` schema 4 compatibility, `.mvproj`
  schema 2 compatibility, and transactional preset/project saves.
- Local live-microphone analysis with RMS, zero crossings, spectral centroid,
  spectral flux, and onset-driven 3D mapping.
- Android and iOS metadata, microphone permission declarations, and mobile
  lifecycle handling.
- Full source data retained separately from the GPU preview point budget.

## Build a host validation app

Qt 6.8 or newer with Qt Quick, Qt Multimedia, and Qt Shader Tools is required.

```sh
qt-cmake -S mobile -B build-mobile -GNinja -DBUILD_TESTING=ON
cmake --build build-mobile --parallel
ctest --test-dir build-mobile --output-on-failure
```

Run:

```sh
./build-mobile/metriq-visualizer-mobile
```

Use `--smoke-test` to load QML and exit automatically in CI.

## Android

Install matching Qt 6.8 desktop and Android arm64 packages plus Android NDK
26.1.10909125 or 27.2.12479018. Then use the Android Qt installation's
`qt-cmake` wrapper:

```sh
$QT_ANDROID/bin/qt-cmake -S mobile -B build-android -GNinja \
  -DANDROID_SDK_ROOT="$ANDROID_SDK_ROOT" \
  -DANDROID_NDK_ROOT="$ANDROID_NDK_ROOT" \
  -DBUILD_TESTING=OFF
cmake --build build-android --target apk --parallel
```

The unsigned package is produced under `build-android/android-build/outputs`.
Release signing is intentionally not stored in this public repository.

## iOS

Install matching Qt 6.8 desktop and iOS packages on macOS with Xcode, then:

```sh
$QT_IOS/bin/qt-cmake -S mobile -B build-ios -GXcode -DBUILD_TESTING=OFF
cmake --build build-ios --config Release
```

A signing team/profile must be supplied locally or in a protected release CI.
No Apple credentials are committed.

## Product boundary

This is a real GPU renderer and mobile application foundation, not a WebView or
Python desktop wrapper. The first vertical slice intentionally does not claim
that desktop audio/video-file analysis, every Matplotlib analysis panel, full
Export Studio composition, or zero-copy hardware video export has already been
ported. Those remain explicit parity milestones. Preset/project fields that the
slice does not yet execute are retained when the document is saved, rather than
deleted or silently rewritten.
