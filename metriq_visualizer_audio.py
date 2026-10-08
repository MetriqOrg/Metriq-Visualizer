# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Bounded DSP with the v1.10.18/librosa 0.11 numerical conventions.

The chroma and YIN equations follow librosa (ISC; see docs/licenses/librosa.txt).
Do not substitute the 1.12 approximations: these arrays drive saved formulas.
"""

from functools import lru_cache

import numpy as np
from scipy.fft import dct, irfft, next_fast_len, rfft

CHUNK_FRAMES = 64


def frames(y, n_fft, hop_length, *, edge=False):
    padded = np.pad(y, n_fft // 2, mode="edge" if edge else "constant")
    return np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop_length].T


def magnitude_stft(y, n_fft, hop_length):
    framed = frames(y, n_fft, hop_length)
    # Periodic Hann, as in librosa/scipy.get_window, rather than np.hanning.
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)
    output = np.empty((n_fft // 2 + 1, framed.shape[1]), dtype=np.float32, order="F")
    for start in range(0, framed.shape[1], CHUNK_FRAMES):
        stop = start + CHUNK_FRAMES
        # librosa casts the double FFT to complex64 before taking magnitude.
        spectrum = rfft(window[:, None] * framed[:, start:stop], axis=0).astype(np.complex64)
        output[:, start:stop] = np.abs(spectrum)
    return output


def power_to_db(power, *, amin=1e-10, ref=1.0):
    db = 10.0 * np.log10(np.maximum(amin, power))
    db -= 10.0 * np.log10(np.maximum(amin, ref))
    return np.maximum(db, db.max() - 80.0)


def amplitude_to_db(magnitude):
    # Preserve float32 rounding and the legacy +1e-12/ref=np.max call.
    amplitude = magnitude + 1e-12
    return power_to_db(np.square(amplitude), amin=1e-10, ref=np.max(amplitude) ** 2)


def spectral_features(magnitude, sample_rate, n_fft):
    count = magnitude.shape[1]
    # Old spectral calls infer an EVEN FFT length from S, including for odd FFTs.
    frequencies = np.fft.rfftfreq(2 * (magnitude.shape[0] - 1), 1.0 / sample_rate)
    rms = np.empty(count, np.float32)
    centroid = np.empty(count, np.float64)
    bandwidth = np.empty(count, np.float64)
    rolloff = np.empty(count, np.float64)
    flatness = np.empty(count, np.float32)
    for start in range(0, count, CHUNK_FRAMES):
        stop = start + CHUNK_FRAMES
        batch = magnitude[:, start:stop]
        power = np.square(batch)
        power[0] *= 0.5
        if n_fft % 2 == 0:
            power[-1] *= 0.5
        rms[start:stop] = np.sqrt(2 * np.sum(power, axis=0) / n_fft ** 2)
        length = np.sum(batch.astype(np.float64), axis=0, keepdims=True)
        length[length < np.finfo(np.float32).tiny] = 1.0
        normalized = (batch / length).astype(np.float32)
        center = np.sum(frequencies[:, None] * normalized, axis=0)
        centroid[start:stop] = center
        # Match the legacy column-major reduction order as well as its formula.
        deviation = np.abs(np.subtract.outer(center, frequencies).T)
        bandwidth[start:stop] = np.sum(normalized * deviation ** 2, axis=0) ** 0.5
        cumulative = np.cumsum(batch, axis=0)
        indices = np.argmax(cumulative >= 0.85 * cumulative[-1], axis=0)
        rolloff[start:stop] = frequencies[indices]
        power = np.maximum(1e-10, np.square(batch))
        flatness[start:stop] = np.exp(np.mean(np.log(power), axis=0)) / np.mean(power, axis=0)
    return rms, centroid, bandwidth, rolloff, flatness


def zero_crossing_rate(y, n_fft, hop_length):
    framed = frames(y, n_fft, hop_length, edge=True)
    output = np.empty(framed.shape[1], np.float64)
    for start in range(0, output.size, CHUNK_FRAMES):
        batch = framed[:, start:start + CHUNK_FRAMES]
        # librosa treats near-zero samples as positive and divides by n_fft,
        # including a False crossing at the start of each frame.
        signs = (batch < 0) & (np.abs(batch) > 1e-10)
        output[start:start + CHUNK_FRAMES] = np.sum(signs[1:] != signs[:-1], axis=0) / n_fft
    return output


def spectral_contrast(magnitude, sample_rate):
    frequencies = np.fft.rfftfreq(2 * (magnitude.shape[0] - 1), 1.0 / sample_rate)
    edges = np.r_[0.0, 200.0 * 2.0 ** np.arange(7)]
    if np.any(edges[:-1] >= sample_rate / 2):
        raise ValueError("Frequency band exceeds Nyquist. Reduce either fmin or n_bands.")
    valleys = np.empty((7, magnitude.shape[1]), np.float64)
    peaks = np.empty_like(valleys)
    for band, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        mask = (frequencies >= low) & (frequencies <= high)
        bins = np.flatnonzero(mask)
        if band > 0:
            mask[bins[0] - 1] = True
        if band == 6:
            mask[bins[-1] + 1:] = True
        quantile = max(1, int(np.rint(0.02 * np.sum(mask))))
        selected = np.flatnonzero(mask)
        if band < 6:
            selected = selected[:-1]
        for start in range(0, magnitude.shape[1], CHUNK_FRAMES):
            stop = start + CHUNK_FRAMES
            ordered = np.sort(magnitude[selected, start:stop], axis=0)
            valleys[band, start:stop] = np.mean(ordered[:quantile], axis=0)
            peaks[band, start:stop] = np.mean(ordered[-quantile:], axis=0)
    return power_to_db(peaks) - power_to_db(valleys)


def parabolic_shifts(values):
    shifts = np.zeros_like(values)
    # Numba's stencil promotes multiplication by 2 to double precision,
    # but adds/subtracts the neighboring float32 samples BEFORE promotion.
    a = (values[2:] + values[:-2]).astype(np.float64) - 2 * values[1:-1].astype(np.float64)
    b = (values[2:] - values[:-2]).astype(np.float64) / 2
    np.divide(-b, a, out=shifts[1:-1], where=np.abs(b) < np.abs(a))
    return shifts


def estimate_tuning(magnitude, sample_rate):
    n_fft = 2 * (magnitude.shape[0] - 1)
    frequencies = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    allowed = (frequencies >= 150) & (frequencies < min(4000, sample_rate / 2))
    pitches, weights = [], []
    for start in range(0, magnitude.shape[1], CHUNK_FRAMES):
        batch = magnitude[:, start:start + CHUNK_FRAMES]
        shifts = parabolic_shifts(batch)
        skew = 0.5 * np.gradient(batch, axis=0) * shifts
        thresholded = batch * (batch > 0.1 * np.max(batch, axis=0))
        maxima = np.zeros(batch.shape, bool)
        maxima[1:-1] = ((thresholded[1:-1] > thresholded[:-2])
                         & (thresholded[1:-1] >= thresholded[2:]))
        maxima[-1] = thresholded[-1] > thresholded[-2]
        bins, columns = np.nonzero(maxima & allowed[:, None])
        pitch = ((bins + shifts[bins, columns]) * float(sample_rate) / n_fft).astype(np.float32)
        weight = batch[bins, columns] + skew[bins, columns]
        pitches.append(pitch)
        weights.append(weight)
    pitch, weight = np.concatenate(pitches), np.concatenate(weights)
    if not pitch.size:
        return 0.0
    pitch = pitch[weight >= np.median(weight)]
    residual = np.mod(12 * np.log2(pitch / (440.0 / 16)), 1.0)
    residual[residual >= 0.5] -= 1.0
    counts, edges = np.histogram(residual, np.linspace(-0.5, 0.5, 101))
    return float(edges[np.argmax(counts)])


@lru_cache(maxsize=32)
def chroma_filterbank(sample_rate, n_fft, tuning):
    frequencies = np.linspace(0, sample_rate, n_fft, endpoint=False)[1:]
    bins = 12 * np.log2(frequencies / (440.0 * 2.0 ** (tuning / 12) / 16))
    bins = np.r_[bins[0] - 18, bins]
    widths = np.r_[np.maximum(np.diff(bins), 1.0), 1]
    distance = np.subtract.outer(bins, np.arange(12, dtype=float)).T
    distance = np.remainder(distance + 6 + 120, 12) - 6
    weights = np.exp(-0.5 * (2 * distance / widths) ** 2)
    weights /= np.sqrt(np.sum(weights ** 2, axis=0, keepdims=True))
    weights *= np.exp(-0.5 * ((bins / 12 - 5) / 2) ** 2)
    weights = np.roll(weights, -3, axis=0)
    return np.ascontiguousarray(weights[:, :1 + n_fft // 2], dtype=np.float32)


def chroma_stft(magnitude, sample_rate):
    n_fft = 2 * (magnitude.shape[0] - 1)
    bank = chroma_filterbank(sample_rate, n_fft, estimate_tuning(magnitude, sample_rate))
    # Same float32 BLAS contraction and magnitude (NOT power) as the baseline.
    chroma = np.einsum("cf,ft->ct", bank, magnitude, optimize=True)
    length = np.max(np.abs(chroma).astype(float), axis=0, keepdims=True)
    length[length < np.finfo(chroma.dtype).tiny] = 1.0
    return (chroma / length).astype(np.float32)


def spectral_flux(magnitude):
    output = np.empty(magnitude.shape[1], np.float32)
    for start in range(0, output.size, CHUNK_FRAMES):
        stop = min(output.size, start + CHUNK_FRAMES)
        batch = magnitude[:, max(0, start - 1):stop]
        normalized = batch / np.maximum(np.linalg.norm(batch, axis=0, keepdims=True), 1e-12)
        if start == 0:
            delta = np.diff(normalized, axis=1, prepend=normalized[:, :1])
        else:
            delta = np.diff(normalized, axis=1)
        output[start:stop] = np.sqrt(np.sum(delta ** 2, axis=0))
    return output


def yin(y, sample_rate, n_fft, hop_length):
    fmax = max(200.0, float(min(sample_rate / 2 - 50, 12000)))
    if fmax > sample_rate / 2 or n_fft - 1 < sample_rate / fmax:
        return np.zeros(frames(y, n_fft, hop_length).shape[1], np.float64)
    minimum = int(np.floor(sample_rate / fmax))
    maximum = min(int(np.ceil(sample_rate / 50)), n_fft - 1)
    framed = frames(y, n_fft, hop_length)
    output = np.empty(framed.shape[1], np.float64)
    pad_length = next_fast_len(2 * n_fft - 1, real=True)
    periods = np.arange(1, maximum + 1)[:, None]
    for start in range(0, output.size, CHUNK_FRAMES):
        batch = framed[:, start:start + CHUNK_FRAMES]
        spectrum = rfft(batch, n=pad_length, axis=0)
        power = spectrum.real ** 2 + spectrum.imag ** 2
        acf = irfft(power, n=pad_length, axis=0)[:maximum + 1]
        energy = np.cumsum(np.square(batch), axis=0)
        energy[0] = 0
        energy[1:maximum + 1] = 2 * (acf[:1] - acf[1:]) - energy[:maximum]
        cumulative = np.cumsum(energy[1:maximum + 1], axis=0) / periods
        denominator = cumulative[minimum - 1:maximum]
        difference = energy[minimum:maximum + 1] / (denominator + np.finfo(denominator.dtype).tiny)
        shifts = parabolic_shifts(difference)
        troughs = np.zeros(difference.shape, bool)
        troughs[0] = difference[0] < difference[1]
        troughs[1:-1] = ((difference[1:-1] < difference[:-2])
                         & (difference[1:-1] <= difference[2:]))
        troughs[-1] = difference[-1] < difference[-2]
        below = troughs & (difference < 0.1)
        selected = np.argmax(below, axis=0)
        missing = ~np.any(below, axis=0)
        selected[missing] = np.argmin(difference, axis=0)[missing]
        output[start:start + CHUNK_FRAMES] = sample_rate / (
            minimum + selected + shifts[selected, np.arange(selected.size)])
    return output


def cepstrum_and_onset(magnitude):
    db = power_to_db(np.square(magnitude) + 1e-12)
    # Legacy mfcc(S=...) applies DCT directly to the linear FFT dB spectrum;
    # converting it to a Mel spectrum would change existing presets.
    mfcc = dct(db, axis=0, type=2, norm="ortho")[:13]
    onset = np.zeros(magnitude.shape[1], np.float32)
    for start in range(3, onset.size, CHUNK_FRAMES):
        stop = min(onset.size, start + CHUNK_FRAMES)
        change = np.maximum(0.0, db[:, start - 2:stop - 2] - db[:, start - 3:stop - 3])
        onset[start:stop] = np.mean(change, axis=0)
    # The old call omitted n_fft/hop_length: librosa uses 2048/512 here,
    # so the delay is ALWAYS 1 + 2048//(2*512) = 3 frames.
    return mfcc, onset
