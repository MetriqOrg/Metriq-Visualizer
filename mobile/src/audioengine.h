#pragma once

#include "audioanalysis.h"

#include <QAudioFormat>
#include <QObject>
#include <QPointer>
#include <QVector>

class QAudioSource;
class QIODevice;

namespace metriq::mobile {

class AudioEngine final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool active READ active NOTIFY activeChanged)
    Q_PROPERTY(QString status READ status NOTIFY statusChanged)
    Q_PROPERTY(float rms READ rms NOTIFY metricsChanged)
    Q_PROPERTY(float spectralCentroidHz READ spectralCentroidHz NOTIFY metricsChanged)
    Q_PROPERTY(float onsetStrength READ onsetStrength NOTIFY metricsChanged)

public:
    explicit AudioEngine(QObject *parent = nullptr);
    ~AudioEngine() override;

    [[nodiscard]] bool active() const noexcept { return m_active; }
    [[nodiscard]] QString status() const { return m_status; }
    [[nodiscard]] float rms() const noexcept { return m_metrics.rms; }
    [[nodiscard]] float spectralCentroidHz() const noexcept { return m_metrics.spectralCentroidHz; }
    [[nodiscard]] float onsetStrength() const noexcept { return m_metrics.onsetStrength; }

    Q_INVOKABLE void start();
    Q_INVOKABLE void stop();
    void setApplicationActive(bool active);

signals:
    void activeChanged();
    void statusChanged();
    void metricsChanged();
    void metricsReady(const metriq::mobile::AudioMetrics &metrics);

private:
    void startGranted();
    void readAvailableAudio();
    void consumeBytes(const QByteArray &bytes);
    void processWindows();
    void setStatus(QString status);
    void setActive(bool active);

    QAudioSource *m_source = nullptr;
    QPointer<QIODevice> m_device;
    QAudioFormat m_format;
    QVector<float> m_pendingSamples;
    QVector<float> m_previousSpectrum;
    AudioMetrics m_metrics;
    QString m_status = QStringLiteral("Microphone idle");
    bool m_active = false;
    bool m_resumeAfterForeground = false;
};

} // namespace metriq::mobile
