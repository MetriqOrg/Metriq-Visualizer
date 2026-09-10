# Mobile architecture map

```text
QML phone/tablet shell
        |
VisualizerModel (aligned immutable snapshots)
   |                 |
GPU item             CPU fallback
QSGGeometry          QQuickPaintedItem
QSB shaders          shared projection math
   |
Qt RHI: Metal / Vulkan / OpenGL ES / Direct3D

Microphone -> QAudioSource -> bounded PCM queue -> worker FFT -> feature frame
                                                            -> VisualizerModel

Preset/project JSON -> StateCodec -> supported state + retained unknown sections
Mapped CSV/TSV      -> VisualizerModel -> normalized retained geometry
```

The desktop Python application remains the exact-render/export reference during
this foundation stage. Mobile does not embed Python, Matplotlib, SciPy, OpenCV,
or the desktop widget hierarchy.
