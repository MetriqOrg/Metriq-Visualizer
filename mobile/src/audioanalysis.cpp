#include "audioanalysis.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <numbers>

namespace metriq::mobile {
namespace {

int floorPowerOfTwo(int value)
{
    if (value < 2)
        return 0;
    int result = 1;
    while (result <= value / 2)
        result *= 2;
    return result;
}

void fftInPlace(QVector<std::complex<float>> &values)
{
    const int size = values.size();
    for (int i = 1, j = 0; i < size; ++i) {
        int bit = size >> 1;
        for (; j & bit; bit >>= 1)
            j ^= bit;
        j ^= bit;
        if (i < j)
            std::swap(values[i], values[j]);
    }

    for (int length = 2; length <= size; length <<= 1) {
        const float angle = -2.0f * std::numbers::pi_v<float> / static_cast<float>(length);
        const std::complex<float> step(std::cos(angle), std::sin(angle));
        for (int start = 0; start < size; start += length) {
            std::complex<float> rotation(1.0f, 0.0f);
            for (int offset = 0; offset < length / 2; ++offset) {
                const auto even = values[start + offset];
                const auto odd = values[start + offset + length / 2] * rotation;
                values[start + offset] = even + odd;
                values[start + offset + length / 2] = even - odd;
                rotation *= step;
            }
        }
    }
}

} // namespace

AudioMetrics AudioAnalysis::analyze(const QVector<float> &samples,
                                    int sampleRate,
                                    QVector<float> *previousSpectrum)
{
    AudioMetrics metrics;
    const int fftSize = floorPowerOfTwo(samples.size());
    if (fftSize < 32 || sampleRate <= 0)
        return metrics;

    double energy = 0.0;
    int crossings = 0;
    float prior = samples.front();
    for (int index = 0; index < fftSize; ++index) {
        const float sample = std::isfinite(samples[index]) ? samples[index] : 0.0f;
        energy += static_cast<double>(sample) * sample;
        if (index > 0 && ((sample >= 0.0f) != (prior >= 0.0f)))
            ++crossings;
        prior = sample;
    }
    metrics.rms = static_cast<float>(std::sqrt(energy / fftSize));
    metrics.zeroCrossingRate = static_cast<float>(crossings) / static_cast<float>(fftSize - 1);

    QVector<std::complex<float>> spectrumInput(fftSize);
    for (int index = 0; index < fftSize; ++index) {
        const float window = 0.5f - 0.5f * std::cos(
            2.0f * std::numbers::pi_v<float> * static_cast<float>(index)
            / static_cast<float>(fftSize - 1));
        const float sample = std::isfinite(samples[index]) ? samples[index] : 0.0f;
        spectrumInput[index] = std::complex<float>(sample * window, 0.0f);
    }
    fftInPlace(spectrumInput);

    const int bins = fftSize / 2 + 1;
    QVector<float> magnitudes(bins);
    double weightedFrequency = 0.0;
    double magnitudeSum = 0.0;
    double positiveFlux = 0.0;
    for (int bin = 0; bin < bins; ++bin) {
        const float magnitude = std::abs(spectrumInput[bin]);
        magnitudes[bin] = std::isfinite(magnitude) ? magnitude : 0.0f;
        const double frequency = static_cast<double>(bin) * sampleRate / fftSize;
        weightedFrequency += frequency * magnitudes[bin];
        magnitudeSum += magnitudes[bin];
        if (previousSpectrum && previousSpectrum->size() == bins)
            positiveFlux += std::max(0.0f, magnitudes[bin] - previousSpectrum->at(bin));
    }

    if (magnitudeSum > 1.0e-12)
        metrics.spectralCentroidHz = static_cast<float>(weightedFrequency / magnitudeSum);
    metrics.spectralFlux = magnitudeSum > 1.0e-12
        ? static_cast<float>(positiveFlux / magnitudeSum)
        : 0.0f;
    metrics.onsetStrength = std::clamp(metrics.spectralFlux * 4.0f, 0.0f, 1.0f);

    if (previousSpectrum)
        *previousSpectrum = std::move(magnitudes);
    return metrics;
}

} // namespace metriq::mobile
