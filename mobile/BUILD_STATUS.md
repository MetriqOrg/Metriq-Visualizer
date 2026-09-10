# Build status contract

This branch must not be described as a production mobile release merely because
its host build passes. Acceptance for the foundation pull request requires:

1. Qt 6.8 C++ compilation on Linux, Windows, and macOS.
2. QML cache compilation and portable QSB shader generation.
3. Native model/state/audio/projection regression tests.
4. Offscreen startup with the CPU fallback selected.
5. No changes to the desktop Python runtime outside the stacked optimization
   base.

Android APK/AAB and iOS app/simulator artifacts are the next packaging gate.
Physical GPU, microphone, thermal, battery, interruption, and store-signing
validation remain separate evidence requirements.
