// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "AudioFeatureEngine.h"

#include "VisualizerModel.h"

#include <QAudioDevice>
#include <QAudioSource>
#include <QCoreApplication>
#include <QIODevice>
#include <QMediaDevices>
#include <QMicrophonePermission>
#include <QPermission>

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstring>
#include <numbers>

namespace {
constexpr int kAnalysisSize = 1024;
constexpr int kAnalysisHop = 512;
constexpr int kMaximumQueuedBlocks = 3;

void fftInPlace(QVector<std::complex<float>> &values)
{
    const int size = values.size();
    for (int index = 1, reversed = 0; index < size; ++index) {
        int bit = size >> 1;
        for (; reversed & bit; bit >>= 1)
            reversed ^= bit;
        reversed ^= bit;
        if (index < reversed)
            std::swap(values[index], values[reversed]);
    }

    for (int length = 2; length <= size; length <<= 1) {
        const float angle = -2.0F * std::numbers::pi_v<float> / static_cast<float>(length);
        const std::complex<float> step(std::cos(angle), std::sin(angle));
        for (int start = 0; start < size; start += length) {
            std::complex<float> weight(1.0F, 0.0F);
            for (int offset = 0; offset < length / 2; ++offset) {
                const std::complex<float> even = values[start + offset];
                const std::complex<float> odd = values[start + offset + length / 2] * weight;
                values[start + offset] = even + odd;
                values[start + offset + length / 2] = even - odd;
                weight *= step;
            }
        }
    }
}
} // namespace

void AudioAnalyzerWorker::analyze(const QVector<float> &samples, int sampleRate,
                                  double timestampSeconds)
{
    if (samples.size() < kAnalysisSize || sampleRate <= 0)
        return;

    QVector<std::complex<float>> spectrum(kAnalysisSize);
    double energy = 0.0;
    for (int index = 0; index < kAnalysisSize; ++index) {
        const float sample = std::isfinite(samples.at(index)) ? samples.at(index) : 0.0F;
        const float phase = static_cast<float>(index) / static_cast<float>(kAnalysisSize - 1);
        const float window = 0.5F - 0.5F * std::cos(2.0F * std::numbers::pi_v<float> * phase);
        spectrum[index] = std::complex<float>(sample * window, 0.0F);
        energy += static_cast<double>(sample) * static_cast<double>(sample);
    }
    fftInPlace(spectrum);

    QVector<float> magnitude(kAnalysisSize / 2);
    double weightedFrequency = 0.0;
    double magnitudeSum = 0.0;
    float strongest = 0.0F;
    int strongestBin = 0;
    const int minimumBin = std::max(1, static_cast<int>(40.0 * kAnalysisSize / sampleRate));
    const int maximumBin = std::min(kAnalysisSize / 2 - 1,
                                    static_cast<int>(4000.0 * kAnalysisSize / sampleRate));
    for (int bin = 0; bin < magnitude.size(); ++bin) {
        const float value = std::abs(spectrum.at(bin));
        magnitude[bin] = value;
        const double frequency = static_cast<double>(bin) * sampleRate / kAnalysisSize;
        weightedFrequency += frequency * value;
        magnitudeSum += value;
        if (bin >= minimumBin && bin <= maximumBin && value > strongest) {
            strongest = value;
            strongestBin = bin;
        }
    }

    double positiveDifference = 0.0;
    if (m_previousMagnitude.size() == magnitude.size()) {
        for (int index = 0; index < magnitude.size(); ++index)
            positiveDifference += std::max(0.0F, magnitude.at(index) - m_previousMagnitude.at(index));
    }
    m_previousMagnitude = magnitude;

    AudioFeatureFrame frame;
    frame.rms = static_cast<float>(std::sqrt(energy / kAnalysisSize));
    frame.centroidHz = magnitudeSum > 1.0e-9
        ? static_cast<float>(weightedFrequency / magnitudeSum)
        : 0.0F;
    frame.spectralFlux = magnitudeSum > 1.0e-9
        ? static_cast<float>(positiveDifference / magnitudeSum)
        : 0.0F;
    frame.fundamentalHz = strongest > 0.0F
        ? static_cast<float>(strongestBin) * sampleRate / kAnalysisSize
        : 0.0F;
    frame.timestampSeconds = timestampSeconds;
    emit frameReady(frame);
}

void AudioAnalyzerWorker::reset()
{
    m_previousMagnitude.clear();
}

AudioFeatureEngine::AudioFeatureEngine(VisualizerModel *model, QObject *parent)
    : QObject(parent)
    , m_model(model)
    , m_mediaDevices(new QMediaDevices(this))
    , m_worker(new AudioAnalyzerWorker)
{
    qRegisterMetaType<AudioFeatureFrame>();
    m_worker->moveToThread(&m_analysisThread);
    connect(&m_analysisThread, &QThread::finished, m_worker, &QObject::deleteLater);
    connect(this, &AudioFeatureEngine::analyzeBlock,
            m_worker, &AudioAnalyzerWorker::analyze, Qt::QueuedConnection);
    connect(m_worker, &AudioAnalyzerWorker::frameReady,
            this, &AudioFeatureEngine::acceptFrame, Qt::QueuedConnection);
    connect(m_mediaDevices, &QMediaDevices::audioInputsChanged,
            this, &AudioFeatureEngine::inputAvailabilityChanged);
    m_analysisThread.setObjectName(QStringLiteral("MetriqMobileAudioAnalysis"));
    m_analysisThread.start(QThread::LowPriority);
}

AudioFeatureEngine::~AudioFeatureEngine()
{
    stop();
    m_analysisThread.quit();
    m_analysisThread.wait(3000);
}

bool AudioFeatureEngine::active() const
{
    return m_active;
}

bool AudioFeatureEngine::inputAvailable() const
{
    return !QMediaDevices::defaultAudioInput().isNull();
}

int AudioFeatureEngine::sampleRate() const
{
    return m_format.sampleRate();
}

qreal AudioFeatureEngine::rms() const
{
    return m_lastFrame.rms;
}

qreal AudioFeatureEngine::centroidHz() const
{
    return m_lastFrame.centroidHz;
}

qreal AudioFeatureEngine::spectralFlux() const
{
    return m_lastFrame.spectralFlux;
}

qreal AudioFeatureEngine::fundamentalHz() const
{
    return m_lastFrame.fundamentalHz;
}

QString AudioFeatureEngine::errorMessage() const
{
    return m_errorMessage;
}

void AudioFeatureEngine::start()
{
    if (m_active)
        return;

    QMicrophonePermission permission;
    QCoreApplication *application = QCoreApplication::instance();
    if (!application) {
        setError(tr("Application permission service is unavailable."));
        return;
    }

    const Qt::PermissionStatus status = application->checkPermission(permission);
    if (status == Qt::PermissionStatus::Undetermined) {
        application->requestPermission(permission, this, [this](const QPermission &result) {
            if (result.status() == Qt::PermissionStatus::Granted)
                startDevice();
            else
                setError(tr("Microphone permission was not granted."));
        });
        return;
    }
    if (status != Qt::PermissionStatus::Granted) {
        setError(tr("Microphone permission is denied in system settings."));
        return;
    }
    startDevice();
}

void AudioFeatureEngine::startDevice()
{
    const QAudioDevice input = QMediaDevices::defaultAudioInput();
    if (input.isNull()) {
        setError(tr("No microphone input is available."));
        return;
    }

    QAudioFormat requested;
    requested.setSampleRate(48000);
    requested.setChannelCount(1);
    requested.setSampleFormat(QAudioFormat::Float);
    m_format = input.isFormatSupported(requested) ? requested : input.preferredFormat();
    if (!m_format.isValid()) {
        setError(tr("The default microphone did not provide a usable format."));
        return;
    }

    m_pendingSamples.clear();
    m_pendingOffset = 0;
    m_blocksInFlight = 0;
    m_lastFrame = {};
    QMetaObject::invokeMethod(m_worker, &AudioAnalyzerWorker::reset, Qt::QueuedConnection);

    m_source = new QAudioSource(input, m_format, this);
    m_source->setBufferSize(static_cast<int>(std::max<qint64>(4096, m_format.bytesForDuration(120000))));
    m_device = m_source->start();
    if (!m_device) {
        setError(tr("The microphone stream could not be started."));
        m_source->deleteLater();
        m_source = nullptr;
        return;
    }

    connect(m_device, &QIODevice::readyRead, this, &AudioFeatureEngine::readAudio);
    m_elapsed.restart();
    m_active = true;
    setError({});
    if (m_model) {
        m_model->clear();
        m_model->setSourceName(QStringLiteral("Live microphone"));
    }
    emit activeChanged();
    emit formatChanged();
    emit metricsChanged();
}

void AudioFeatureEngine::stop()
{
    if (m_device)
        disconnect(m_device, nullptr, this, nullptr);
    m_device = nullptr;
    if (m_source) {
        m_source->stop();
        m_source->deleteLater();
        m_source = nullptr;
    }
    m_pendingSamples.clear();
    m_pendingOffset = 0;
    m_blocksInFlight = 0;
    if (m_active) {
        m_active = false;
        emit activeChanged();
    }
}

QVector<float> AudioFeatureEngine::decodePcm(const QByteArray &bytes) const
{
    QVector<float> result;
    const int channels = std::max(1, m_format.channelCount());
    const int bytesPerSample = m_format.bytesPerSample();
    const int bytesPerFrame = m_format.bytesPerFrame();
    if (bytesPerSample <= 0 || bytesPerFrame <= 0)
        return result;
    const int frameCount = bytes.size() / bytesPerFrame;
    result.reserve(frameCount);
    const char *data = bytes.constData();

    auto readSample = [this, bytesPerSample](const char *sampleData) -> float {
        switch (m_format.sampleFormat()) {
        case QAudioFormat::UInt8:
            return (static_cast<int>(*reinterpret_cast<const quint8 *>(sampleData)) - 128) / 128.0F;
        case QAudioFormat::Int16: {
            qint16 value = 0;
            std::memcpy(&value, sampleData, std::min<int>(bytesPerSample, sizeof(value)));
            return value / 32768.0F;
        }
        case QAudioFormat::Int32: {
            qint32 value = 0;
            std::memcpy(&value, sampleData, std::min<int>(bytesPerSample, sizeof(value)));
            return static_cast<float>(value / 2147483648.0);
        }
        case QAudioFormat::Float: {
            float value = 0.0F;
            std::memcpy(&value, sampleData, std::min<int>(bytesPerSample, sizeof(value)));
            return std::isfinite(value) ? value : 0.0F;
        }
        case QAudioFormat::Unknown:
        default:
            return 0.0F;
        }
    };

    for (int frame = 0; frame < frameCount; ++frame) {
        float mixed = 0.0F;
        const char *frameData = data + frame * bytesPerFrame;
        for (int channel = 0; channel < channels; ++channel)
            mixed += readSample(frameData + channel * bytesPerSample);
        result.push_back(std::clamp(mixed / channels, -1.0F, 1.0F));
    }
    return result;
}

void AudioFeatureEngine::readAudio()
{
    if (!m_device || !m_active)
        return;
    const QByteArray bytes = m_device->readAll();
    const QVector<float> decoded = decodePcm(bytes);
    if (decoded.isEmpty())
        return;
    m_pendingSamples += decoded;

    while (m_pendingSamples.size() - m_pendingOffset >= kAnalysisSize) {
        if (m_blocksInFlight >= kMaximumQueuedBlocks) {
            m_pendingOffset += kAnalysisHop;
            continue;
        }
        const QVector<float> block = m_pendingSamples.mid(m_pendingOffset, kAnalysisSize);
        m_pendingOffset += kAnalysisHop;
        ++m_blocksInFlight;
        const double timestamp = m_elapsed.isValid() ? m_elapsed.elapsed() / 1000.0 : 0.0;
        emit analyzeBlock(block, m_format.sampleRate(), timestamp);
    }

    if (m_pendingOffset >= kAnalysisSize * 4) {
        m_pendingSamples.remove(0, m_pendingOffset);
        m_pendingOffset = 0;
    }
}

void AudioFeatureEngine::acceptFrame(const AudioFeatureFrame &frame)
{
    m_blocksInFlight = std::max(0, m_blocksInFlight - 1);
    m_lastFrame = frame;
    emit metricsChanged();
    if (m_model) {
        m_model->appendLiveFrame(
            frame.rms,
            frame.centroidHz,
            frame.spectralFlux,
            frame.fundamentalHz,
            frame.timestampSeconds);
    }
}

void AudioFeatureEngine::setError(const QString &message)
{
    if (m_errorMessage == message)
        return;
    m_errorMessage = message;
    emit errorChanged();
}
