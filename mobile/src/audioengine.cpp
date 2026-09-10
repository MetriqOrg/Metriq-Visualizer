#include "audioengine.h"

#include <QAudioDevice>
#include <QAudioSource>
#include <QCoreApplication>
#include <QGuiApplication>
#include <QIODevice>
#include <QMediaDevices>
#include <QPermissions>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

namespace metriq::mobile {
namespace {

float sanitize(float value)
{
    return std::isfinite(value) ? std::clamp(value, -1.0f, 1.0f) : 0.0f;
}

template<typename Sample>
Sample unalignedSample(const char *data)
{
    Sample value{};
    std::memcpy(&value, data, sizeof(Sample));
    return value;
}

} // namespace

AudioEngine::AudioEngine(QObject *parent)
    : QObject(parent)
{
    qRegisterMetaType<AudioMetrics>();
}

AudioEngine::~AudioEngine()
{
    stop();
}

void AudioEngine::start()
{
    if (m_active)
        return;

    const QMicrophonePermission permission;
    const auto permissionStatus = qApp->checkPermission(permission);
    if (permissionStatus == Qt::PermissionStatus::Granted) {
        startGranted();
        return;
    }
    if (permissionStatus == Qt::PermissionStatus::Denied) {
        setStatus(QStringLiteral("Microphone permission denied"));
        return;
    }

    setStatus(QStringLiteral("Requesting microphone permission…"));
    qApp->requestPermission(permission, this, [this](const QPermission &result) {
        if (result.status() == Qt::PermissionStatus::Granted)
            startGranted();
        else
            setStatus(QStringLiteral("Microphone permission denied"));
    });
}

void AudioEngine::startGranted()
{
    const QAudioDevice input = QMediaDevices::defaultAudioInput();
    if (input.isNull()) {
        setStatus(QStringLiteral("No microphone input is available"));
        return;
    }

    stop();
    m_format = input.preferredFormat();
    if (m_format.sampleRate() <= 0 || m_format.channelCount() <= 0
        || m_format.sampleFormat() == QAudioFormat::Unknown) {
        setStatus(QStringLiteral("The default microphone format is unsupported"));
        return;
    }

    m_source = new QAudioSource(input, m_format, this);
    connect(m_source, &QAudioSource::stateChanged, this, [this](QtAudio::State state) {
        if (!m_source)
            return;
        if (state == QtAudio::StoppedState && m_source->error() != QtAudio::NoError) {
            setStatus(QStringLiteral("Microphone stream stopped with an audio error"));
            setActive(false);
        }
    });

    m_device = m_source->start();
    if (!m_device) {
        setStatus(QStringLiteral("Could not start microphone capture"));
        delete m_source;
        m_source = nullptr;
        return;
    }

    connect(m_device, &QIODevice::readyRead, this, &AudioEngine::readAvailableAudio);
    m_pendingSamples.clear();
    m_previousSpectrum.clear();
    setActive(true);
    setStatus(QStringLiteral("Live microphone"));
}

void AudioEngine::stop()
{
    m_resumeAfterForeground = false;
    if (m_source)
        m_source->stop();
    if (m_device)
        disconnect(m_device, nullptr, this, nullptr);
    m_device.clear();
    delete m_source;
    m_source = nullptr;
    m_pendingSamples.clear();
    m_previousSpectrum.clear();
    setActive(false);
    if (!m_status.contains(QStringLiteral("permission"), Qt::CaseInsensitive))
        setStatus(QStringLiteral("Microphone idle"));
}

void AudioEngine::setApplicationActive(bool active)
{
    if (!active) {
        m_resumeAfterForeground = m_active;
        if (m_source && m_active) {
            m_source->suspend();
            setStatus(QStringLiteral("Microphone paused in background"));
        }
        return;
    }
    if (m_resumeAfterForeground && m_source) {
        m_source->resume();
        m_resumeAfterForeground = false;
        setStatus(QStringLiteral("Live microphone"));
    }
}

void AudioEngine::readAvailableAudio()
{
    if (!m_device)
        return;
    const QByteArray bytes = m_device->readAll();
    if (!bytes.isEmpty()) {
        consumeBytes(bytes);
        processWindows();
    }
}

void AudioEngine::consumeBytes(const QByteArray &bytes)
{
    const int channels = std::max(1, m_format.channelCount());
    const int bytesPerSample = m_format.bytesPerSample();
    const int frameBytes = bytesPerSample * channels;
    if (bytesPerSample <= 0 || frameBytes <= 0)
        return;

    const int frameCount = bytes.size() / frameBytes;
    m_pendingSamples.reserve(m_pendingSamples.size() + frameCount);
    const char *data = bytes.constData();
    for (int frame = 0; frame < frameCount; ++frame) {
        double sum = 0.0;
        for (int channel = 0; channel < channels; ++channel) {
            const char *sample = data + frame * frameBytes + channel * bytesPerSample;
            float value = 0.0f;
            switch (m_format.sampleFormat()) {
            case QAudioFormat::UInt8:
                value = (static_cast<unsigned char>(*sample) - 128.0f) / 128.0f;
                break;
            case QAudioFormat::Int16:
                value = static_cast<float>(unalignedSample<qint16>(sample))
                    / static_cast<float>(std::numeric_limits<qint16>::max());
                break;
            case QAudioFormat::Int32:
                value = static_cast<float>(unalignedSample<qint32>(sample))
                    / static_cast<float>(std::numeric_limits<qint32>::max());
                break;
            case QAudioFormat::Float:
                value = unalignedSample<float>(sample);
                break;
            case QAudioFormat::Unknown:
                break;
            }
            sum += sanitize(value);
        }
        m_pendingSamples.append(static_cast<float>(sum / channels));
    }
}

void AudioEngine::processWindows()
{
    constexpr int windowSize = 2048;
    constexpr int hopSize = 512;
    while (m_pendingSamples.size() >= windowSize) {
        const QVector<float> window = m_pendingSamples.mid(0, windowSize);
        m_metrics = AudioAnalysis::analyze(window, m_format.sampleRate(), &m_previousSpectrum);
        emit metricsChanged();
        emit metricsReady(m_metrics);
        m_pendingSamples.remove(0, hopSize);
    }
}

void AudioEngine::setStatus(QString status)
{
    if (m_status == status)
        return;
    m_status = std::move(status);
    emit statusChanged();
}

void AudioEngine::setActive(bool active)
{
    if (m_active == active)
        return;
    m_active = active;
    emit activeChanged();
}

} // namespace metriq::mobile
