#pragma once

#include "audioengine.h"
#include "scenepoint.h"

#include <QJsonObject>
#include <QObject>
#include <QUrl>
#include <QVector>

namespace metriq::mobile {

class SceneController final : public QObject
{
    Q_OBJECT

    Q_PROPERTY(QString sourceName READ sourceName NOTIFY sourceChanged)
    Q_PROPERTY(QString sourceKind READ sourceKind NOTIFY sourceChanged)
    Q_PROPERTY(QString sourcePath READ sourcePath NOTIFY sourceChanged)
    Q_PROPERTY(QString presetName READ presetName NOTIFY presetChanged)
    Q_PROPERTY(QString status READ status NOTIFY statusChanged)
    Q_PROPERTY(int pointCount READ pointCount NOTIFY sceneChanged)
    Q_PROPERTY(int renderedPointCount READ renderedPointCount NOTIFY sceneChanged)
    Q_PROPERTY(double duration READ duration NOTIFY sourceChanged)
    Q_PROPERTY(double progress READ progress WRITE setProgress NOTIFY progressChanged)
    Q_PROPERTY(bool playing READ playing WRITE setPlaying NOTIFY playingChanged)

    Q_PROPERTY(float azimuth READ azimuth WRITE setAzimuth NOTIFY cameraChanged)
    Q_PROPERTY(float elevation READ elevation WRITE setElevation NOTIFY cameraChanged)
    Q_PROPERTY(float zoom READ zoom WRITE setZoom NOTIFY cameraChanged)
    Q_PROPERTY(bool autorotate READ autorotate WRITE setAutorotate NOTIFY renderSettingsChanged)
    Q_PROPERTY(float rotationSpeed READ rotationSpeed WRITE setRotationSpeed NOTIFY renderSettingsChanged)

    Q_PROPERTY(bool showLine READ showLine WRITE setShowLine NOTIFY renderSettingsChanged)
    Q_PROPERTY(bool showPoints READ showPoints WRITE setShowPoints NOTIFY renderSettingsChanged)
    Q_PROPERTY(bool showGrid READ showGrid WRITE setShowGrid NOTIFY renderSettingsChanged)
    Q_PROPERTY(bool showAxes READ showAxes WRITE setShowAxes NOTIFY renderSettingsChanged)
    Q_PROPERTY(bool showHeadMarker READ showHeadMarker WRITE setShowHeadMarker NOTIFY renderSettingsChanged)
    Q_PROPERTY(float lineWidth READ lineWidth WRITE setLineWidth NOTIFY renderSettingsChanged)
    Q_PROPERTY(float pointScale READ pointScale WRITE setPointScale NOTIFY renderSettingsChanged)
    Q_PROPERTY(float baseAlpha READ baseAlpha WRITE setBaseAlpha NOTIFY renderSettingsChanged)
    Q_PROPERTY(float trailLength READ trailLength WRITE setTrailLength NOTIFY renderSettingsChanged)
    Q_PROPERTY(float fadeCurve READ fadeCurve WRITE setFadeCurve NOTIFY renderSettingsChanged)
    Q_PROPERTY(float headScale READ headScale WRITE setHeadScale NOTIFY renderSettingsChanged)
    Q_PROPERTY(float haloScale READ haloScale WRITE setHaloScale NOTIFY renderSettingsChanged)
    Q_PROPERTY(int historyMode READ historyMode WRITE setHistoryMode NOTIFY renderSettingsChanged)

    Q_PROPERTY(QString performanceMode READ performanceMode WRITE setPerformanceMode NOTIFY performanceChanged)
    Q_PROPERTY(int targetFps READ targetFps WRITE setTargetFps NOTIFY performanceChanged)
    Q_PROPERTY(int pointBudget READ pointBudget WRITE setPointBudget NOTIFY performanceChanged)

    Q_PROPERTY(AudioEngine *audio READ audio CONSTANT)

public:
    explicit SceneController(QObject *parent = nullptr);

    [[nodiscard]] QString sourceName() const { return m_sourceName; }
    [[nodiscard]] QString sourceKind() const { return m_sourceKind; }
    [[nodiscard]] QString sourcePath() const { return m_sourcePath; }
    [[nodiscard]] QString presetName() const { return m_presetName; }
    [[nodiscard]] QString status() const { return m_status; }
    [[nodiscard]] int pointCount() const noexcept { return m_points.size(); }
    [[nodiscard]] int renderedPointCount() const noexcept { return m_renderPoints.size(); }
    [[nodiscard]] double duration() const noexcept { return m_duration; }
    [[nodiscard]] double progress() const noexcept { return m_progress; }
    [[nodiscard]] bool playing() const noexcept { return m_playing; }

    [[nodiscard]] float azimuth() const noexcept { return m_azimuth; }
    [[nodiscard]] float elevation() const noexcept { return m_elevation; }
    [[nodiscard]] float zoom() const noexcept { return m_zoom; }
    [[nodiscard]] bool autorotate() const noexcept { return m_autorotate; }
    [[nodiscard]] float rotationSpeed() const noexcept { return m_rotationSpeed; }

    [[nodiscard]] bool showLine() const noexcept { return m_showLine; }
    [[nodiscard]] bool showPoints() const noexcept { return m_showPoints; }
    [[nodiscard]] bool showGrid() const noexcept { return m_showGrid; }
    [[nodiscard]] bool showAxes() const noexcept { return m_showAxes; }
    [[nodiscard]] bool showHeadMarker() const noexcept { return m_showHeadMarker; }
    [[nodiscard]] float lineWidth() const noexcept { return m_lineWidth; }
    [[nodiscard]] float pointScale() const noexcept { return m_pointScale; }
    [[nodiscard]] float baseAlpha() const noexcept { return m_baseAlpha; }
    [[nodiscard]] float trailLength() const noexcept { return m_trailLength; }
    [[nodiscard]] float fadeCurve() const noexcept { return m_fadeCurve; }
    [[nodiscard]] float headScale() const noexcept { return m_headScale; }
    [[nodiscard]] float haloScale() const noexcept { return m_haloScale; }
    [[nodiscard]] int historyMode() const noexcept { return m_historyMode; }

    [[nodiscard]] QString performanceMode() const { return m_performanceMode; }
    [[nodiscard]] int targetFps() const noexcept { return m_targetFps; }
    [[nodiscard]] int pointBudget() const noexcept { return m_pointBudget; }
    [[nodiscard]] AudioEngine *audio() noexcept { return &m_audio; }

    [[nodiscard]] const QVector<ScenePoint> &renderPoints() const noexcept { return m_renderPoints; }
    [[nodiscard]] quint64 sceneRevision() const noexcept { return m_sceneRevision; }

    Q_INVOKABLE void generateDemo(int count = 6000);
    Q_INVOKABLE bool loadSource(const QUrl &url);
    Q_INVOKABLE bool loadPreset(const QUrl &url);
    Q_INVOKABLE bool savePreset(const QUrl &url);
    Q_INVOKABLE bool loadProject(const QUrl &url);
    Q_INVOKABLE bool saveProject(const QUrl &url);
    Q_INVOKABLE void toggleMicrophone();
    Q_INVOKABLE void advance(double seconds);
    Q_INVOKABLE void orbit(float deltaX, float deltaY);
    Q_INVOKABLE void resetCamera();
    Q_INVOKABLE void setApplicationActive(bool active);

public slots:
    void setProgress(double value);
    void setPlaying(bool playing);
    void setAzimuth(float value);
    void setElevation(float value);
    void setZoom(float value);
    void setAutorotate(bool value);
    void setRotationSpeed(float value);
    void setShowLine(bool value);
    void setShowPoints(bool value);
    void setShowGrid(bool value);
    void setShowAxes(bool value);
    void setShowHeadMarker(bool value);
    void setLineWidth(float value);
    void setPointScale(float value);
    void setBaseAlpha(float value);
    void setTrailLength(float value);
    void setFadeCurve(float value);
    void setHeadScale(float value);
    void setHaloScale(float value);
    void setHistoryMode(int value);
    void setPerformanceMode(const QString &value);
    void setTargetFps(int value);
    void setPointBudget(int value);

signals:
    void sourceChanged();
    void presetChanged();
    void statusChanged();
    void sceneChanged();
    void progressChanged();
    void playingChanged();
    void cameraChanged();
    void renderSettingsChanged();
    void performanceChanged();

private:
    bool loadTable(const QString &path);
    void applyState(const QJsonObject &state);
    QJsonObject currentState() const;
    void appendLiveMetrics(const AudioMetrics &metrics);
    void rebuildRenderPoints();
    void replaceScene(QVector<ScenePoint> points,
                      QString sourceName,
                      QString sourceKind,
                      QString sourcePath,
                      double duration);
    void setStatus(QString status);

    QVector<ScenePoint> m_points;
    QVector<ScenePoint> m_renderPoints;
    quint64 m_sceneRevision = 0;

    QString m_sourceName;
    QString m_sourceKind;
    QString m_sourcePath;
    QString m_presetName = QStringLiteral("Data Disco");
    QString m_status = QStringLiteral("Ready");
    QJsonObject m_presetDocument;
    QJsonObject m_projectDocument;
    double m_duration = 60.0;
    double m_progress = 1.0;
    bool m_playing = true;

    float m_azimuth = 35.0f;
    float m_elevation = 24.0f;
    float m_zoom = 1.0f;
    bool m_autorotate = true;
    float m_rotationSpeed = 16.0f;

    bool m_showLine = true;
    bool m_showPoints = true;
    bool m_showGrid = true;
    bool m_showAxes = true;
    bool m_showHeadMarker = true;
    float m_lineWidth = 1.55f;
    float m_pointScale = 0.4f;
    float m_baseAlpha = 1.0f;
    float m_trailLength = 0.22f;
    float m_fadeCurve = 0.4f;
    float m_headScale = 0.24f;
    float m_haloScale = 0.45f;
    int m_historyMode = 0;

    QString m_performanceMode = QStringLiteral("Balanced");
    int m_targetFps = 60;
    int m_pointBudget = 6000;

    AudioEngine m_audio;
    quint64 m_liveSequence = 0;
};

} // namespace metriq::mobile
