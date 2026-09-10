# Metriq Visualizer mobile GPU foundation

**Branch:** `codex/visualizer-mobile-gpu-foundation-v2`  
**Base:** optimized desktop candidate `bac4446aa6ffd33cedcbaedf17211f30ade63fec`  
**Foundation version:** 0.1.0

## Product boundary

This is an additive native mobile target, not the desktop Python interface
compressed into a phone window. The existing desktop application remains the
behavioral reference and exact export implementation. Mobile uses the same
preset/project state vocabulary while moving interactive rendering and bounded
live analysis into a native Qt 6.8 C++ runtime.

The mobile source lives under `mobile/`. It does not add Python dependencies,
change desktop runtime defaults, replace presets, or change the 1.12.8 desktop
release number.

## Implemented vertical slice

### Portable retained GPU renderer

`GpuVisualizerItem` is a custom Qt Quick scene-graph item. Position, color,
size, and timestamp attributes are uploaded to retained scene-graph geometry.
Camera movement and playback update uniform data; they do not regenerate the
trajectory. Qt Shader Tools compiles one shader source set into portable QSB
packages. Qt RHI then selects Metal, Vulkan, OpenGL/OpenGL ES, or Direct3D for
the host platform.

The application reports the API that actually initialized. It does not infer
hardware acceleration merely from an installed graphics library. When Qt uses
its software scene graph, QML switches to `CpuVisualizerItem`, which reads the
same model and projection contract.

Current GPU primitives are points, a connected trajectory, playback head, and
coordinate field. The first implementation uses portable point primitives and
a line strip. Production tube/ribbon geometry, robust joins, and full preview
parity remain later renderer work; the existing controls and state are not
removed to conceal that boundary.

### Shared native model

`VisualizerModel` owns aligned position/color/size/time records behind a
read-write lock. The render thread receives immutable snapshots. Geometry and
visual revisions are independent so camera/time changes avoid vertex-buffer
rebuilds.

The model currently supports:

- deterministic generated demonstration data;
- mapped CSV/TSV/TXT import with quoted fields, optional RGBA/size/time columns,
  invalid-row rejection, and bounded input size;
- a bounded live trajectory driven by native microphone feature frames;
- touch orbit, zoom, reset, playback, autorotation, points/path modes, history
  modes, opacity, line width, point scale, axes, and playback head;
- additive state import/export retaining unknown sections.

### Native microphone path

`AudioFeatureEngine` requests microphone permission only when live input starts.
Qt Multimedia supplies PCM. The input callback path converts and buffers samples
but does not perform rendering or FFT work. A low-priority worker thread computes
bounded 1,024-sample Hann-window FFT frames and returns RMS, spectral centroid,
positive spectral flux, and a bounded dominant/fundamental estimate. At most
three analysis blocks are queued; stale excess blocks are skipped rather than
letting latency grow without bound.

No network permission or implicit upload path is introduced.

### State compatibility

The C++ codec accepts:

- `metriq.visualizer-preset`, schema versions through 4;
- `metriq.visualizer-project`, schema versions through 2;
- legacy root-state preset payloads and `.bgl` project routing;
- mapped `.csv`, `.tsv`, and `.txt` sources.

Known visual/performance values are applied to the native model. Mapping,
extraction, layout, session, vendor, and future sections are retained when the
document is saved. Mobile additions live under the optional `mobile` section.

Android document-provider writes are intentionally not claimed complete in this
slice. Local/shared-files locations use transactional `QSaveFile`; provider
write-back needs a platform document abstraction rather than pretending a
`content:` URI is a normal path.

### Touch UI

The QML shell provides a dominant viewport, source/preset/backend status,
playback controls, live microphone control, document actions, and the existing
scene/style controls. Phones use a dismissible bottom inspector; tablets expose
it as a persistent side inspector. It is a touch reorganization, not a
capability tier or reduced-quality mode.

## Runtime flow

```text
microphone / imported mapped data / demo
                    |
           bounded native model
                    |
       retained vertex attributes
                    |
     Qt Quick scene graph + QSB shaders
                    |
  Metal | Vulkan | OpenGL ES | Direct3D
                    |
                display

software Qt scene graph -> CpuVisualizerItem fallback
```

## Build and validation

`mobile/CMakeLists.txt` builds the native runtime independently with Qt 6.8.3+
Core, Gui, QML, Quick, Quick Controls, Multimedia, and Shader Tools. Host tests
cover deterministic geometry, bounded live history, unknown-state retention,
preset schema 3 to 4 round trip, project schema 2 round trip, mapped-data
normalization, projection/temporal behavior, and a resolvable microphone FFT
fixture.

The mobile CI workflow compiles QSB shaders and C++ on Linux, Windows, and
macOS, runs the native test suite, and starts the QML application offscreen long
enough to detect immediate startup failure. This is host validation, not a
claim that an APK or IPA has passed store signing or physical-device testing.

## Platform packaging included

Android source metadata provides a Qt activity, hardware-accelerated
application, optional microphone/GLES/Vulkan features, runtime microphone
permission, and file-provider paths. Devices are not excluded merely for
lacking Vulkan.

iOS metadata provides the microphone purpose text, phone/tablet orientations,
files-in-place support, and exported Visualizer preset/project document types.
Signing and provisioning remain deployment configuration and no credentials are
committed.

## Feature-parity status

| Capability | Foundation status |
|---|---|
| GPU points/path/camera/time | Implemented |
| CPU renderer fallback | Implemented |
| Touch phone/tablet shell | Implemented |
| Live microphone RMS/centroid/flux/pitch | Implemented |
| Preset/project schema compatibility | Implemented |
| Mapped CSV/TSV data | Implemented |
| Full desktop audio-file DSP parity | Not yet |
| Spectrogram/chromagram/MFCC mobile panels | Not yet |
| Source video analysis/camera input | Not yet |
| GPU ribbon/tube and all preview accents | Not yet |
| GPU-native export to VideoToolbox/MediaCodec | Not yet |
| Android document-provider write-back | Not yet |
| Physical device thermal/frame-pacing certification | Not yet |
| App Store/Play signing and submission | Not yet |

These are implementation stages, not features selected for removal.

## Recommended next sequence

1. Validate this slice on at least one Apple Silicon Mac and one Android-capable
   workstation, then produce unsigned/debug iOS Simulator and Android APK
   artifacts.
2. Establish golden fixtures comparing C++ mapped geometry with the Python
   desktop core for every built-in mapping and legacy alias.
3. Port bounded file-audio DSP and scientific panel data into the shared native
   core; keep the Python implementation as the oracle until parity passes.
4. Replace point/line primitives with GPU ribbons, circular quad sprites,
   temporal accents, labels, colorbar, and ghost path while preserving fallback.
5. Add native file-provider/security-scoped document access and reopen-in-place.
6. Add thermal/frame pacing, background/interrupt audio behavior, and physical
   device profiling.
7. Implement GPU render-target export directly into VideoToolbox and MediaCodec,
   avoiding per-frame CPU readback.
8. Bring the same renderer back to desktop behind an opt-in backend, then make it
   default only after visual and compatibility evidence is complete.

## Non-claims

This source does not claim a production-complete App Store or Play Store app,
full desktop parity, a physical hardware benchmark, zero-copy video export, or
that software OpenGL is a hardware GPU. It is a buildable architectural and
functional vertical slice intended to make those remaining stages additive
instead of forcing a later rewrite.
