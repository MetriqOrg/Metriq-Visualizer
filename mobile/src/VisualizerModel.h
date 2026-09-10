// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QColor>
#include <QJsonObject>
#include <QObject>
#include <QReadWriteLock>
#include <QUrl>
#include <QVector>
#include <QVector3D>

class VisualizerModel final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(int pointCount READ pointCount NOTIFY geometryChanged)
    Q_PROPERTY(qreal duration READ duration NOTIFY geometryChanged)
    Q_PROPERTY(qreal currentTime READ currentTime WRITE setCurrentTime NOTIFY currentTimeChanged)
    Q_PROPERTY(bool playing READ playing WRITE setPlaying NOTIFY playingChanged)
    Q_PROPERTY(QString presetName READ presetName WRITE setPresetName NOTIFY presetNameChanged)
    Q_PROPERTY(QString sourceName READ sourceName NOTIFY sourceChanged)
    Q_PROPERTY(qreal elevation READ elevation WRITE setElevation NOTIFY visualChanged)
    Q_PROPERTY(qreal azimuth READ azimuth WRITE setAzimuth NOTIFY visualChanged)
    Q_PROPERTY(qreal zoom READ zoom WRITE setZoom NOTIFY visualChanged)
    Q_PROPERTY(qreal pointSizeScale READ pointSizeScale WRITE setPointSizeScale NOTIFY visualChanged)
    Q_PROPERTY(qreal lineWidth READ lineWidth WRITE setLineWidth NOTIFY visualChanged)
    Q_PROPERTY(qreal baseAlpha READ baseAlpha WRITE setBaseAlpha NOTIFY visualChanged)
    Q_PROPERTY(qreal pointLifespan READ pointLifespan WRITE setPointLifespan NOTIFY visualChanged)
    Q_PROPERTY(bool connectLines READ connectLines WRITE setConnectLines NOTIFY visualChanged)
    Q_PROPERTY(bool showAxes READ showAxes WRITE setShowAxes NOTIFY visualChanged)
    Q_PROPERTY(bool showHeadMarker READ showHeadMarker WRITE setShowHeadMarker NOTIFY visualChanged)
    Q_PROPERTY(bool autorotate READ autorotate WRITE setAutorotate NOTIFY visualChanged)
    Q_PROPERTY(QString renderMode READ renderMode WRITE setRenderMode NOTIFY visualChanged)
    Q_PROPERTY(QString historyMode READ historyMode WRITE setHistoryMode NOTIFY visualChanged)
    Q_PROPERTY(QString colormap READ colormap WRITE setColormap NOTIFY stateChanged)
    Q_PROPERTY(int livePointBudget READ livePointBudget WRITE setLivePointBudget NOTIFY stateChanged)
    Q_PROPERTY(qulonglong geometryRevision READ geometryRevision NOTIFY geometryChanged)

public:
    struct Snapshot {
        QVector<QVector3D> positions;
        QVector<QColor> colors;
        QVector<float> sizes;
        QVector<float> times;
        quint64 revision = 0;
        float duration = 0.0F;
        float currentTime = 0.0F;
        float elevation = 24.0F;
        float azimuth = 35.0F;
        float zoom = 1.0F;
        float pointSizeScale = 0.4F;
        float lineWidth = 1.55F;
        float baseAlpha = 1.0F;
        float pointLifespan = 1.0F;
        bool connectLines = true;
        bool showAxes = true;
        bool showHeadMarker = true;
        QString renderMode = QStringLiteral("Points + line");
        QString historyMode = QStringLiteral("Trail fade");
    };

    explicit VisualizerModel(QObject *parent = nullptr);

    [[nodiscard]] int pointCount() const;
    [[nodiscard]] qreal duration() const;
    [[nodiscard]] qreal currentTime() const;
    [[nodiscard]] bool playing() const;
    [[nodiscard]] QString presetName() const;
    [[nodiscard]] QString sourceName() const;
    [[nodiscard]] qreal elevation() const;
    [[nodiscard]] qreal azimuth() const;
    [[nodiscard]] qreal zoom() const;
    [[nodiscard]] qreal pointSizeScale() const;
    [[nodiscard]] qreal lineWidth() const;
    [[nodiscard]] qreal baseAlpha() const;
    [[nodiscard]] qreal pointLifespan() const;
    [[nodiscard]] bool connectLines() const;
    [[nodiscard]] bool showAxes() const;
    [[nodiscard]] bool showHeadMarker() const;
    [[nodiscard]] bool autorotate() const;
    [[nodiscard]] QString renderMode() const;
    [[nodiscard]] QString historyMode() const;
    [[nodiscard]] QString colormap() const;
    [[nodiscard]] int livePointBudget() const;
    [[nodiscard]] qulonglong geometryRevision() const;
    [[nodiscard]] Snapshot snapshot() const;
    [[nodiscard]] QJsonObject exportState() const;

    void applyState(const QJsonObject &state);
    void setSourceName(const QString &name);

    Q_INVOKABLE void generateDemo(int count = 3500);
    Q_INVOKABLE bool loadMappedData(const QUrl &url);
    Q_INVOKABLE void clear();
    Q_INVOKABLE void appendLiveFrame(qreal rms, qreal centroidHz, qreal flux,
                                     qreal fundamentalHz, qreal timestampSeconds);
    Q_INVOKABLE void orbit(qreal deltaX, qreal deltaY);
    Q_INVOKABLE void zoomBy(qreal factor);
    Q_INVOKABLE void resetCamera();
    Q_INVOKABLE void advance(qreal seconds);

public slots:
    void setCurrentTime(qreal value);
    void setPlaying(bool value);
    void setPresetName(const QString &value);
    void setElevation(qreal value);
    void setAzimuth(qreal value);
    void setZoom(qreal value);
    void setPointSizeScale(qreal value);
    void setLineWidth(qreal value);
    void setBaseAlpha(qreal value);
    void setPointLifespan(qreal value);
    void setConnectLines(bool value);
    void setShowAxes(bool value);
    void setShowHeadMarker(bool value);
    void setAutorotate(bool value);
    void setRenderMode(const QString &value);
    void setHistoryMode(const QString &value);
    void setColormap(const QString &value);
    void setLivePointBudget(int value);

signals:
    void geometryChanged();
    void currentTimeChanged();
    void playingChanged();
    void presetNameChanged();
    void sourceChanged();
    void visualChanged();
    void stateChanged();
    void errorOccurred(const QString &message);

private:
    struct Point {
        QVector3D position;
        QColor color;
        float size = 1.0F;
        float time = 0.0F;
    };

    void replacePoints(QVector<Point> points, const QString &sourceName);
    static QColor colorForProgress(float progress, float alpha = 1.0F);
    static QString pathFromUrl(const QUrl &url);

    mutable QReadWriteLock m_lock;
    QVector<Point> m_points;
    QJsonObject m_stateTemplate;
    quint64 m_geometryRevision = 0;
    qreal m_duration = 0.0;
    qreal m_currentTime = 0.0;
    bool m_playing = true;
    QString m_presetName = QStringLiteral("Data Disco");
    QString m_sourceName = QStringLiteral("Generated demo");
    qreal m_elevation = 24.0;
    qreal m_azimuth = 35.0;
    qreal m_zoom = 1.0;
    qreal m_pointSizeScale = 0.4;
    qreal m_lineWidth = 1.55;
    qreal m_baseAlpha = 1.0;
    qreal m_pointLifespan = 1.0;
    bool m_connectLines = true;
    bool m_showAxes = true;
    bool m_showHeadMarker = true;
    bool m_autorotate = true;
    QString m_renderMode = QStringLiteral("Points + line");
    QString m_historyMode = QStringLiteral("Trail fade");
    QString m_colormap = QStringLiteral("turbo");
    int m_livePointBudget = 1200;
};
