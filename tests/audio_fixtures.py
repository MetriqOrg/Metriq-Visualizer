"""Deterministic eight-second, 44.1 kHz inputs for the baseline contract."""

import numpy as np
import soundfile as sf

FIXTURE_NAMES = ("tones_noise", "speech_like", "silence")


def write_fixture(path, name, sample_rate=44100):
    t = np.arange(8 * sample_rate, dtype=np.float64) / sample_rate
    rng = np.random.default_rng(1018)
    if name == "tones_noise":
        envelope = 0.45 + 0.35 * np.sin(2 * np.pi * 0.7 * t)
        signal = envelope * (0.3 * np.sin(2 * np.pi * 220 * t)
                             + 0.16 * np.sin(2 * np.pi * 659.25 * t))
        signal += 0.035 * rng.standard_normal(t.size)
        signal[(t > 3.2) & (t < 3.6)] = 0
    elif name == "speech_like":
        f0 = 135 + 30 * np.sin(2 * np.pi * 0.6 * t)
        phase = 2 * np.pi * np.cumsum(f0) / sample_rate
        signal = np.zeros(t.size)
        for harmonic in range(1, 25):
            frequency = harmonic * f0
            formants = sum(np.exp(-0.5 * ((frequency - center) / width) ** 2)
                           for center, width in ((600, 100), (1400, 180), (2500, 250)))
            signal += (0.025 + 0.06 * formants) / np.sqrt(harmonic) * np.sin(harmonic * phase)
        syllables = np.maximum(np.sin(2 * np.pi * 2.7 * t), 0) ** 1.5
        signal = syllables * signal + (1 - syllables) * 0.018 * rng.standard_normal(t.size)
        signal[(t > 5.4) & (t < 6)] = 0
    elif name == "silence":
        signal = np.zeros(t.size)
    else:
        raise ValueError(name)
    sf.write(path, signal, sample_rate, subtype="PCM_16")
