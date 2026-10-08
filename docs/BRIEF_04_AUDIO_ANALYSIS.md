# Brief 04: compatible audio analysis

Implemented on `port/core`, based on `release/1.13-rollback` at `1ae7026`.
The reference checkout was read only. UI, theme, layout, and renderer files are
unchanged. No new feature names were added.

## What made 1.12.8 faster

The baseline already computes one STFT and passes its magnitude to the spectral
feature functions. Its main cold-call cost is loading librosa's analysis stack,
including Numba/JIT machinery for pitch tracking, interpolation, and crossing
detection. It also retains a full complex STFT and creates full-size temporary
matrices for YIN autocorrelation/differences, tuning, normalization, and flux.

1.12.8 bypasses that stack: SoundFile decodes directly, SciPy resamples, FFT work
is chunked, most arrays stay float32, and harmonic summation replaces YIN.
Chroma, MFCC, onset, rolloff, PCA, window, and frame alignment also change.
Its adaptive hop/frame ceiling reduces memory on long inputs by changing the
number of frames. Those semantic changes cannot be copied into legacy names.

The port uses bounded NumPy/SciPy FFT, YIN, tuning, ZCR, spectral reductions,
contrast, and flux, avoiding the normal librosa/Numba import path. It discards
complex FFT batches immediately and calculates the dB spectrum once for MFCC
and onset. Small chroma filterbanks are cached. Spectral working arrays remain
float32; public legacy feature arrays and PCA remain float64. It retains every
frame and the old PCA input order/sign conventions. Output matrices still grow
with duration; temporary FFT/YIN working memory is bounded to 64-frame batches.

Legacy definitions retained include centered zero padding and periodic Hann;
edge padding for ZCR; magnitude-based, auto-tuned, infinity-normalized chroma;
13 DCT coefficients of the **linear FFT** power-dB spectrum (the old supplied
`S` bypasses Mel conversion); magnitude-based rolloff; and the fixed three-frame
onset delay from the old call's omitted `n_fft`/`hop_length` arguments.
Spectral functions also retain the baseline's inferred even FFT frequency grid
for odd FFT sizes. Reduction order is preserved for numeric reproducibility.

FFmpeg's mono PCM16 conversion/resampling remains for inputs requiring it;
replacing it with `resample_poly` changes legacy values. An already matching
mono PCM16 WAV can be decoded directly with identical samples. Librosa remains
a fallback for sources that need its decoder when FFmpeg/SoundFile cannot
provide the requested mono sample rate.

## RMS finding

The baseline calls `librosa.feature.rms(S=magnitude, frame_length=n_fft)`.
Parseval's identity makes that the RMS of the **Hann-windowed** frame:

```
RMS² = 2 * sum(adjusted_magnitude²) / n_fft²
```

The DC bin is half weighted, as is Nyquist for even FFT lengths. 1.12.8 instead
computes `sqrt(mean(samples²) + 1e-20)` on unwindowed, uncentered frames.
For stationary tones/noise, the Hann RMS is approximately `sqrt(3/8) = 0.61237`
times the unwindowed RMS. This is not a universal correction factor: transients,
padding, framing, and decoder/resampler differences also matter. The port
reproduces the old calculation, rather than multiplying the new RMS by a
constant. Legacy `rms_db` and the silence floor of -120 dB are also preserved.

On the tones/noise fixture, baseline/port mean RMS is **0.06648611687579313**;
1.12.8 is **0.10932610183954239**. Thus the inspected sources and this comparison
produce the opposite direction from the brief's old/new **0.195 / 0.119** pair.
That pair's ratio is consistent with Hann attenuation with the labels reversed,
but its exact cause cannot be established without the original WAV, settings,
and benchmark invocation. No legacy RMS values were changed to fit those figures.

## Goldens and checks

`tests/generate_audio_goldens.py` loads the core directly from Git tag
`baseline-v1.10.18`, commit `c3dfc9086a8383e2f5adcd0d00192d07b926721e`.
The checked-in manifest records the core SHA256, fixture SHA256 values and
NumPy/SciPy/librosa versions. Expected outputs never come from the port.
The synthetic WAVs are reproducible eight-second, mono PCM16, 44.1 kHz inputs:
tones plus seeded noise, speech-like harmonic/formant/syllable audio, and silence.

All **54 legacy features match bit for bit** on this machine for all three
fixtures at each of these settings, with **zero maximum absolute error**:

| Sample rate | FFT | Hop | Frames per fixture |
|---:|---:|---:|---:|
| 22050 | 2048 | 256 | 690 |
| 44100 | 4096 | 512 | 690 |
| 22050 | 2049 | 257 | 687 |

The default spectrogram, frequencies, chromagram and MFCC panels also match.
Tests allow `rtol=1e-7, atol=1e-8` for FFT/BLAS platform rounding; RMS is checked
for exact equality. There are **no feature exceptions**. Geometry checks cover
legacy audio presets and explicit note-named chroma/MFCC13/contrast7/PC4–6
formulas, raw/minmax/zscore modes, point sizes, volume masks, and sampled indices.

The complete suite passes: **31 passed** with
`QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg ../.venv/bin/python -m pytest -n 2 -q`.
`git diff --check` and Python compilation checks pass.

## Cache integration

Existing UI callers of `analyze_media` automatically use the brief 03 disk
cache. `use_cache=False` provides the uncached path; `cache_root` and
`METRIQ_CACHE_DIR` select storage. The key includes the source fingerprint,
sample rate, FFT length, hop length, and a new analysis engine revision.
`temp_dir` controls scratch placement only and does not alter values.

Cache hits skip decoding and DSP. Tests verify every configurable setting
misses independently, engine/source changes invalidate, corrupt entries recover,
and disabling the cache bypasses lookup. Extracted PCM audio is retained beside
the NPZ so cached results can still be exported with audio after scratch cleanup.
Pruning counts that WAV against the budget; pruning/clearing removes both files.
An unavailable/unwritable cache falls back to uncached analysis.

## Timings and memory

Median of three fresh-process runs on this machine using the tones/noise fixture,
22050 Hz / FFT2048 / hop256. Each process makes two calls. First-call timing
includes lazy analysis imports; module import before that timer is approximately
0.03–0.05 s. RSS is the whole process peak in MiB. Reproduce with
`../.venv/bin/python tests/benchmark_audio_analysis.py --runs 3`.

| Engine/path | First call | Second call | Peak RSS | Frames | Features |
|---|---:|---:|---:|---:|---:|
| baseline-v1.10.18 | 0.9672 s | 0.0969 s | 272.0 MiB | 690 | 54 |
| reference v1.12.8, same settings | 0.4589 s | 0.0177 s | 126.5 MiB | 682 | 64 |
| port, cache disabled | 0.1861 s | 0.0871 s | 85.4 MiB | 690 | 54 |
| port, cache miss then disk hit | 0.2955 s | 0.0092 s | 83.1 MiB | 690 | 54 |

The uncached cold call is approximately **5.2× faster**, with approximately
**69% less peak RSS**. The cached repeat is approximately **10.5× faster** than
the old repeat. A cache miss adds compression/storage work. Uncached warm calls
remain slower than 1.12.8 because exact FFmpeg conversion, YIN, and legacy
spectral definitions are retained. Fresh-process results differ from the
brief's original benchmark; both the fixture and import state affect timing.

No old feature or default panel failed to match. The supplied RMS pair could
not be reproduced from the available fixture/code comparison, as explained above.
