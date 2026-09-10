// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QAudioFormat>
#include <QElapsedTimer>
#include <QObject>
#include <QPointer>
#include <QThread>
#include <QVector>

class QAudioSource;
class QIODevice;
class QMediaDevices;
class VisualizerModel;

struct AudioFeatureFrame {
    float rms = 0.0F;
    float centroidHz = 0.0F;
    float spectralFlux = 0.0F;
    float fundamentalHz = 0.0F;
    double timestampSeconds = 0.0;
};
Q_DECLARE_METATYPE(AudioFeatureFrame)

class AudioAnalyzerWorker final : public QObject
{
    Q_OBJECT

public slots:
    void analyze(const QVector<float> &samples, int sampleRate, double timestampSeconds);
    void reset();

signals:
    void frameReady(const AudioFeatureFrame &frame);

private:
    QVector<float> m_previousMagnitude;
};

class AudioFeatureEngine final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(bool active READ active NOTIFY activeChanged)
    Q_PROPERTY(bool inputAvailable READ inputAvailable NOTIFY inputAvailabilityChanged)
    Q_PROPERTY(int sampleRate READ sampleRate NOTIFY formatChanged)
    Q_PROPERTY(qreal rms READ rms NOTIFY metricsChanged)
    Q_PROPERTY(qreal centroidHz READ centroidHz NOTIFY metricsChanged)
    Q_PROPERTY(qreal spectralFlux READ spectralFlux NOTIFY metricsChanged)
    Q_PROPERTY(qreal fundamentalHz READ fundamentalHz NOTIFY metricsChanged)
    Q_PROPERTY(QString errorMessage READ errorMessage NOTIFY errorChanged)

public:
    explicit AudioFeatureEngine(VisualizerModel *model, QObject *parent = nullptr);
    ~AudioFeatureEngine() override;

    [[nodiscard]] bool active() const;
    [[nodiscard]] bool inputAvailable() const;
    [[nodiscard]] int sampleRate() const;
    [[nodiscard]] qreal rms() const;
    [[nodiscard]] qreal centroidHz() const;
    [[nodiscard]] qreal spectralFlux() const;
    [[nodiscard]] qreal fundamentalHz() const;
    [[nodiscard]] QString errorMessage() const;

    Q_INVOKABLE void start();
    Q_INVOKABLE void stop();

signals:
    void activeChanged();
    void inputAvailabilityChanged();
    void formatChanged();
    void metricsChanged();
    void errorChanged();
    void analyzeBlock(const QVector<float> &samples, int sampleRate, double timestampSeconds);

private slots:
    void readAudio();
    void acceptFrame(const AudioFeatureFrame &frame);

private:
    void startDevice();
    void setError(const QString &message);
    QVector<float> decodePcm(const QByteArray &bytes) const;

    QPointer<VisualizerModel> m_model;
    QMediaDevices *m_mediaDevices = nullptr;
    QAudioSource *m_source = nullptr;
    QIODevice *m_device = nullptr;
    QAudioFormat m_format;
    QVector<float> m_pendingSamples;
    qsizetype m_pendingOffset = 0;
    int m_blocksInFlight = 0;
    QElapsedTimer m_elapsed;
    QThread m_analysisThread;
    AudioAnalyzerWorker *m_worker = nullptr;
    AudioFeatureFrame m_lastFrame;
    QString m_errorMessage;
    bool m_active = false;
};
