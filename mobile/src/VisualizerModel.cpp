// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "VisualizerModel.h"

#include <QFile>
#include <QFileInfo>
#include <QJsonValue>
#include <QReadLocker>
#include <QStringConverter>
#include <QTextStream>
#include <QWriteLocker>
#include <QtMath>

#include <algorithm>
#include <array>
#include <cmath>
#include <limits>

namespace {
constexpr int kMaximumImportedPoints = 250000;
constexpr int kMaximumLivePoints = 16384;

QStringList parseDelimitedLine(const QString &line, const QChar delimiter)
{
    QStringList values;
    QString current;
    bool quoted = false;
    for (qsizetype index = 0; index < line.size(); ++index) {
        const QChar character = line.at(index);
        if (character == u'"') {
            if (quoted && index + 1 < line.size() && line.at(index + 1) == u'"') {
                current += u'"';
                ++index;
            } else {
                quoted = !quoted;
            }
        } else if (character == delimiter && !quoted) {
            values.push_back(current.trimmed());
            current.clear();
        } else {
            current += character;
        }
    }
    values.push_back(current.trimmed());
    return values;
}

float normalizedChannel(double value)
{
    if (!std::isfinite(value))
        return 1.0F;
    if (value > 1.0)
        value /= 255.0;
    return static_cast<float>(std::clamp(value, 0.0, 1.0));
}

float safeFloat(const QJsonObject &object, const QString &key, float fallback)
{
    const QJsonValue value = object.value(key);
    return value.isDouble() ? static_cast<float>(value.toDouble()) : fallback;
}

bool safeBool(const QJsonObject &object, const QString &key, bool fallback)
{
    const QJsonValue value = object.value(key);
    return value.isBool() ? value.toBool() : fallback;
}
} // namespace

VisualizerModel::VisualizerModel(QObject *parent)
    : QObject(parent)
{
    generateDemo();
}

int VisualizerModel::pointCount() const
{
    QReadLocker locker(&m_lock);
    return m_points.size();
}

qreal VisualizerModel::duration() const
{
    QReadLocker locker(&m_lock);
    return m_duration;
}

qreal VisualizerModel::currentTime() const
{
    QReadLocker locker(&m_lock);
    return m_currentTime;
}

bool VisualizerModel::playing() const
{
    QReadLocker locker(&m_lock);
    return m_playing;
}

QString VisualizerModel::presetName() const
{
    QReadLocker locker(&m_lock);
    return m_presetName;
}

QString VisualizerModel::sourceName() const
{
    QReadLocker locker(&m_lock);
    return m_sourceName;
}

qreal VisualizerModel::elevation() const
{
    QReadLocker locker(&m_lock);
    return m_elevation;
}

qreal VisualizerModel::azimuth() const
{
    QReadLocker locker(&m_lock);
    return m_azimuth;
}

qreal VisualizerModel::zoom() const
{
    QReadLocker locker(&m_lock);
    return m_zoom;
}

qreal VisualizerModel::pointSizeScale() const
{
    QReadLocker locker(&m_lock);
    return m_pointSizeScale;
}

qreal VisualizerModel::lineWidth() const
{
    QReadLocker locker(&m_lock);
    return m_lineWidth;
}

qreal VisualizerModel::baseAlpha() const
{
    QReadLocker locker(&m_lock);
    return m_baseAlpha;
}

qreal VisualizerModel::pointLifespan() const
{
    QReadLocker locker(&m_lock);
    return m_pointLifespan;
}

bool VisualizerModel::connectLines() const
{
    QReadLocker locker(&m_lock);
    return m_connectLines;
}

bool VisualizerModel::showAxes() const
{
    QReadLocker locker(&m_lock);
    return m_showAxes;
}

bool VisualizerModel::showHeadMarker() const
{
    QReadLocker locker(&m_lock);
    return m_showHeadMarker;
}

bool VisualizerModel::autorotate() const
{
    QReadLocker locker(&m_lock);
    return m_autorotate;
}

QString VisualizerModel::renderMode() const
{
    QReadLocker locker(&m_lock);
    return m_renderMode;
}

QString VisualizerModel::historyMode() const
{
    QReadLocker locker(&m_lock);
    return m_historyMode;
}

QString VisualizerModel::colormap() const
{
    QReadLocker locker(&m_lock);
    return m_colormap;
}

int VisualizerModel::livePointBudget() const
{
    QReadLocker locker(&m_lock);
    return m_livePointBudget;
}

qulonglong VisualizerModel::geometryRevision() const
{
    QReadLocker locker(&m_lock);
    return m_geometryRevision;
}

VisualizerModel::Snapshot VisualizerModel::snapshot() const
{
    QReadLocker locker(&m_lock);
    Snapshot result;
    result.positions.reserve(m_points.size());
    result.colors.reserve(m_points.size());
    result.sizes.reserve(m_points.size());
    result.times.reserve(m_points.size());
    for (const Point &point : m_points) {
        result.positions.push_back(point.position);
        result.colors.push_back(point.color);
        result.sizes.push_back(point.size);
        result.times.push_back(point.time);
    }
    result.revision = m_geometryRevision;
    result.duration = static_cast<float>(m_duration);
    result.currentTime = static_cast<float>(m_currentTime);
    result.elevation = static_cast<float>(m_elevation);
    result.azimuth = static_cast<float>(m_azimuth);
    result.zoom = static_cast<float>(m_zoom);
    result.pointSizeScale = static_cast<float>(m_pointSizeScale);
    result.lineWidth = static_cast<float>(m_lineWidth);
    result.baseAlpha = static_cast<float>(m_baseAlpha);
    result.pointLifespan = static_cast<float>(m_pointLifespan);
    result.connectLines = m_connectLines;
    result.showAxes = m_showAxes;
    result.showHeadMarker = m_showHeadMarker;
    result.renderMode = m_renderMode;
    result.historyMode = m_historyMode;
    return result;
}

QJsonObject VisualizerModel::exportState() const
{
    QReadLocker locker(&m_lock);
    QJsonObject state = m_stateTemplate;

    QJsonObject visual = state.value(QStringLiteral("visual")).toObject();
    visual.insert(QStringLiteral("elev"), m_elevation);
    visual.insert(QStringLiteral("azim"), m_azimuth);
    visual.insert(QStringLiteral("zoom"), m_zoom);
    visual.insert(QStringLiteral("point_size_scale"), m_pointSizeScale);
    visual.insert(QStringLiteral("line_width"), m_lineWidth);
    visual.insert(QStringLiteral("base_alpha"), m_baseAlpha);
    visual.insert(QStringLiteral("point_lifespan"), m_pointLifespan);
    visual.insert(QStringLiteral("connect_lines"), m_connectLines);
    visual.insert(QStringLiteral("show_axes"), m_showAxes);
    visual.insert(QStringLiteral("show_head_marker"), m_showHeadMarker);
    visual.insert(QStringLiteral("autorotate"), m_autorotate);
    visual.insert(QStringLiteral("render_mode"), m_renderMode);
    visual.insert(QStringLiteral("history_mode"), m_historyMode);
    state.insert(QStringLiteral("visual"), visual);

    QJsonObject geometry = state.value(QStringLiteral("geometry")).toObject();
    geometry.insert(QStringLiteral("colormap"), m_colormap);
    geometry.insert(QStringLiteral("max_points"), qMax(1, m_points.size()));
    state.insert(QStringLiteral("geometry"), geometry);

    QJsonObject performance = state.value(QStringLiteral("performance")).toObject();
    performance.insert(QStringLiteral("live_point_budget"), m_livePointBudget);
    if (!performance.contains(QStringLiteral("mode")))
        performance.insert(QStringLiteral("mode"), QStringLiteral("Balanced"));
    state.insert(QStringLiteral("performance"), performance);

    QJsonObject mobile = state.value(QStringLiteral("mobile")).toObject();
    mobile.insert(QStringLiteral("source_name"), m_sourceName);
    mobile.insert(QStringLiteral("gpu_state_version"), 1);
    state.insert(QStringLiteral("mobile"), mobile);
    return state;
}

void VisualizerModel::applyState(const QJsonObject &state)
{
    const QJsonObject visual = state.value(QStringLiteral("visual")).toObject();
    const QJsonObject geometry = state.value(QStringLiteral("geometry")).toObject();
    const QJsonObject performance = state.value(QStringLiteral("performance")).toObject();

    {
        QWriteLocker locker(&m_lock);
        m_stateTemplate = state;
        m_elevation = std::clamp<qreal>(safeFloat(visual, QStringLiteral("elev"), static_cast<float>(m_elevation)), -90.0, 90.0);
        m_azimuth = std::remainder<qreal>(safeFloat(visual, QStringLiteral("azim"), static_cast<float>(m_azimuth)), 360.0);
        m_zoom = std::clamp<qreal>(safeFloat(visual, QStringLiteral("zoom"), static_cast<float>(m_zoom)), 0.2, 5.0);
        m_pointSizeScale = std::clamp<qreal>(safeFloat(visual, QStringLiteral("point_size_scale"), static_cast<float>(m_pointSizeScale)), 0.0, 5.0);
        m_lineWidth = std::clamp<qreal>(safeFloat(visual, QStringLiteral("line_width"), static_cast<float>(m_lineWidth)), 0.1, 16.0);
        m_baseAlpha = std::clamp<qreal>(safeFloat(visual, QStringLiteral("base_alpha"), static_cast<float>(m_baseAlpha)), 0.0, 1.0);
        m_pointLifespan = std::clamp<qreal>(safeFloat(visual, QStringLiteral("point_lifespan"), static_cast<float>(m_pointLifespan)), 0.02, 120.0);
        m_connectLines = safeBool(visual, QStringLiteral("connect_lines"), m_connectLines);
        m_showAxes = safeBool(visual, QStringLiteral("show_axes"), m_showAxes);
        m_showHeadMarker = safeBool(visual, QStringLiteral("show_head_marker"), m_showHeadMarker);
        m_autorotate = safeBool(visual, QStringLiteral("autorotate"), m_autorotate);
        if (visual.value(QStringLiteral("render_mode")).isString())
            m_renderMode = visual.value(QStringLiteral("render_mode")).toString();
        if (visual.value(QStringLiteral("history_mode")).isString())
            m_historyMode = visual.value(QStringLiteral("history_mode")).toString();
        if (geometry.value(QStringLiteral("colormap")).isString())
            m_colormap = geometry.value(QStringLiteral("colormap")).toString();
        if (performance.value(QStringLiteral("live_point_budget")).isDouble())
            m_livePointBudget = std::clamp(performance.value(QStringLiteral("live_point_budget")).toInt(), 100, 100000);
    }

    emit visualChanged();
    emit stateChanged();
}

void VisualizerModel::setSourceName(const QString &name)
{
    const QString clean = name.trimmed().isEmpty() ? QStringLiteral("Untitled source") : name.trimmed();
    {
        QWriteLocker locker(&m_lock);
        if (m_sourceName == clean)
            return;
        m_sourceName = clean;
    }
    emit sourceChanged();
}

QColor VisualizerModel::colorForProgress(float progress, float alpha)
{
    const qreal hue = std::fmod(0.60 + 0.78 * static_cast<double>(progress), 1.0);
    return QColor::fromHsvF(hue, 0.78, 0.98, std::clamp<double>(alpha, 0.0, 1.0));
}

void VisualizerModel::generateDemo(int count)
{
    count = std::clamp(count, 128, 200000);
    QVector<Point> points;
    points.reserve(count);
    constexpr float durationSeconds = 30.0F;
    for (int index = 0; index < count; ++index) {
        const float progress = count > 1 ? static_cast<float>(index) / static_cast<float>(count - 1) : 0.0F;
        const float theta = progress * 18.0F * static_cast<float>(M_PI);
        const float modulation = 0.68F + 0.22F * std::sin(progress * 13.0F * static_cast<float>(M_PI));
        Point point;
        point.position = QVector3D(
            modulation * std::cos(theta),
            modulation * std::sin(theta),
            0.58F * std::sin(theta * 0.37F) + 0.18F * std::sin(theta * 1.73F));
        point.color = colorForProgress(progress);
        point.size = 9.0F + 20.0F * (0.25F + 0.75F * std::pow(std::sin(theta * 0.5F), 2.0F));
        point.time = progress * durationSeconds;
        points.push_back(point);
    }
    replacePoints(std::move(points), QStringLiteral("Generated demo"));
}

QString VisualizerModel::pathFromUrl(const QUrl &url)
{
    if (url.scheme() == QStringLiteral("qrc"))
        return QStringLiteral(":") + url.path();
    if (url.isLocalFile())
        return url.toLocalFile();
    return url.toString(QUrl::FullyDecoded);
}

bool VisualizerModel::loadMappedData(const QUrl &url)
{
    const QString path = pathFromUrl(url);
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        emit errorOccurred(tr("Could not open mapped data: %1").arg(file.errorString()));
        return false;
    }

    QTextStream stream(&file);
    stream.setEncoding(QStringConverter::Utf8);
    QString firstLine = stream.readLine();
    if (firstLine.trimmed().isEmpty()) {
        emit errorOccurred(tr("Mapped data is empty."));
        return false;
    }

    const QChar delimiter = firstLine.contains(u'\t') && !firstLine.contains(u',') ? u'\t' : u',';
    QStringList firstValues = parseDelimitedLine(firstLine, delimiter);
    bool firstNumeric = firstValues.size() >= 3;
    for (int index = 0; index < qMin(3, firstValues.size()) && firstNumeric; ++index) {
        bool ok = false;
        firstValues.at(index).toDouble(&ok);
        firstNumeric = ok;
    }

    QHash<QString, int> columns;
    QStringList pendingLine;
    if (firstNumeric) {
        pendingLine = firstValues;
        columns.insert(QStringLiteral("x"), 0);
        columns.insert(QStringLiteral("y"), 1);
        columns.insert(QStringLiteral("z"), 2);
    } else {
        for (int index = 0; index < firstValues.size(); ++index)
            columns.insert(firstValues.at(index).trimmed().toLower(), index);
    }

    if (!columns.contains(QStringLiteral("x")) || !columns.contains(QStringLiteral("y")) || !columns.contains(QStringLiteral("z"))) {
        emit errorOccurred(tr("Mapped data requires x, y, and z columns (or three numeric columns)."));
        return false;
    }

    struct RawPoint {
        std::array<double, 3> position{};
        QColor color;
        float size = 16.0F;
        float time = 0.0F;
        bool explicitColor = false;
    };
    QVector<RawPoint> raw;
    raw.reserve(4096);

    auto readValue = [&columns](const QStringList &values, const QString &name, double fallback, bool *okOut = nullptr) {
        const int column = columns.value(name, -1);
        bool ok = false;
        const double result = column >= 0 && column < values.size() ? values.at(column).toDouble(&ok) : fallback;
        if (okOut)
            *okOut = ok;
        return ok ? result : fallback;
    };

    auto consume = [&](const QStringList &values) {
        if (raw.size() >= kMaximumImportedPoints)
            return;
        bool xOk = false;
        bool yOk = false;
        bool zOk = false;
        RawPoint point;
        point.position[0] = readValue(values, QStringLiteral("x"), 0.0, &xOk);
        point.position[1] = readValue(values, QStringLiteral("y"), 0.0, &yOk);
        point.position[2] = readValue(values, QStringLiteral("z"), 0.0, &zOk);
        if (!xOk || !yOk || !zOk || !std::isfinite(point.position[0]) || !std::isfinite(point.position[1]) || !std::isfinite(point.position[2]))
            return;
        point.size = static_cast<float>(std::clamp(readValue(values, QStringLiteral("size"), 16.0), 0.1, 4096.0));
        point.time = static_cast<float>(std::max(0.0, readValue(values, QStringLiteral("time"), raw.size() / 30.0)));
        if (columns.contains(QStringLiteral("r")) && columns.contains(QStringLiteral("g")) && columns.contains(QStringLiteral("b"))) {
            point.color = QColor::fromRgbF(
                normalizedChannel(readValue(values, QStringLiteral("r"), 1.0)),
                normalizedChannel(readValue(values, QStringLiteral("g"), 1.0)),
                normalizedChannel(readValue(values, QStringLiteral("b"), 1.0)),
                normalizedChannel(readValue(values, QStringLiteral("a"), 1.0)));
            point.explicitColor = true;
        }
        raw.push_back(point);
    };

    if (!pendingLine.isEmpty())
        consume(pendingLine);
    while (!stream.atEnd() && raw.size() < kMaximumImportedPoints)
        consume(parseDelimitedLine(stream.readLine(), delimiter));

    if (raw.size() < 2) {
        emit errorOccurred(tr("Mapped data did not contain at least two valid rows."));
        return false;
    }

    std::array<double, 3> low{
        std::numeric_limits<double>::max(), std::numeric_limits<double>::max(), std::numeric_limits<double>::max()};
    std::array<double, 3> high{
        std::numeric_limits<double>::lowest(), std::numeric_limits<double>::lowest(), std::numeric_limits<double>::lowest()};
    for (const RawPoint &point : std::as_const(raw)) {
        for (int axis = 0; axis < 3; ++axis) {
            low[axis] = std::min(low[axis], point.position[axis]);
            high[axis] = std::max(high[axis], point.position[axis]);
        }
    }

    QVector<Point> points;
    points.reserve(raw.size());
    const float endTime = std::max(0.001F, raw.constLast().time);
    for (int index = 0; index < raw.size(); ++index) {
        const RawPoint &source = raw.at(index);
        Point point;
        std::array<float, 3> normalized{};
        for (int axis = 0; axis < 3; ++axis) {
            const double center = 0.5 * (low[axis] + high[axis]);
            const double halfSpan = std::max(0.5 * (high[axis] - low[axis]), 1.0e-9);
            normalized[axis] = static_cast<float>(std::clamp((source.position[axis] - center) / halfSpan, -1.6, 1.6));
        }
        point.position = QVector3D(normalized[0], normalized[1], normalized[2]);
        point.color = source.explicitColor
            ? source.color
            : colorForProgress(static_cast<float>(index) / static_cast<float>(raw.size() - 1));
        point.size = source.size;
        point.time = source.time;
        points.push_back(point);
    }

    replacePoints(std::move(points), QFileInfo(path).fileName());
    setCurrentTime(endTime);
    setPlaying(false);
    return true;
}

void VisualizerModel::replacePoints(QVector<Point> points, const QString &sourceName)
{
    qreal newDuration = 0.0;
    for (const Point &point : std::as_const(points))
        newDuration = std::max(newDuration, static_cast<qreal>(point.time));
    {
        QWriteLocker locker(&m_lock);
        m_points = std::move(points);
        m_duration = newDuration;
        m_currentTime = 0.0;
        m_sourceName = sourceName;
        ++m_geometryRevision;
    }
    emit geometryChanged();
    emit currentTimeChanged();
    emit sourceChanged();
}

void VisualizerModel::clear()
{
    replacePoints({}, QStringLiteral("Empty scene"));
    setPlaying(false);
}

void VisualizerModel::appendLiveFrame(qreal rms, qreal centroidHz, qreal flux,
                                      qreal fundamentalHz, qreal timestampSeconds)
{
    const float timestamp = static_cast<float>(std::max<qreal>(0.0, timestampSeconds));
    const float phase = timestamp * 1.8F;
    const float pitch = static_cast<float>(std::clamp(std::log2(std::max<qreal>(40.0, fundamentalHz) / 440.0) / 4.0, -1.0, 1.0));
    const float centroid = static_cast<float>(std::clamp(centroidHz / 8000.0, 0.0, 1.0));
    const float change = static_cast<float>(std::clamp(flux * 4.0, 0.0, 1.0));
    Point point;
    point.position = QVector3D(
        0.68F * std::cos(phase) + 0.32F * pitch,
        0.68F * std::sin(phase) + 0.32F * (2.0F * centroid - 1.0F),
        2.0F * change - 1.0F);
    point.color = colorForProgress(centroid, 0.25F + 0.75F * static_cast<float>(std::clamp(rms * 8.0, 0.0, 1.0)));
    point.size = 8.0F + 56.0F * static_cast<float>(std::clamp(rms * 5.0, 0.0, 1.0));
    point.time = timestamp;

    {
        QWriteLocker locker(&m_lock);
        if (m_sourceName != QStringLiteral("Live microphone")) {
            m_points.clear();
            m_duration = 0.0;
            m_sourceName = QStringLiteral("Live microphone");
        }
        m_points.push_back(point);
        const int retained = std::clamp(m_livePointBudget * 4, 512, kMaximumLivePoints);
        if (m_points.size() > retained)
            m_points.remove(0, m_points.size() - retained);
        m_duration = timestamp;
        m_currentTime = timestamp;
        ++m_geometryRevision;
    }
    emit geometryChanged();
    emit currentTimeChanged();
    emit sourceChanged();
}

void VisualizerModel::orbit(qreal deltaX, qreal deltaY)
{
    {
        QWriteLocker locker(&m_lock);
        m_elevation = std::clamp(m_elevation - deltaY * 0.14, -90.0, 90.0);
        m_azimuth = std::remainder(m_azimuth - deltaX * 0.14, 360.0);
    }
    emit visualChanged();
}

void VisualizerModel::zoomBy(qreal factor)
{
    if (!std::isfinite(factor) || factor <= 0.0)
        return;
    {
        QWriteLocker locker(&m_lock);
        m_zoom = std::clamp(m_zoom * factor, 0.2, 5.0);
    }
    emit visualChanged();
}

void VisualizerModel::resetCamera()
{
    {
        QWriteLocker locker(&m_lock);
        m_elevation = 24.0;
        m_azimuth = 35.0;
        m_zoom = 1.0;
    }
    emit visualChanged();
}

void VisualizerModel::advance(qreal seconds)
{
    if (!std::isfinite(seconds) || seconds <= 0.0)
        return;
    bool rotated = false;
    {
        QWriteLocker locker(&m_lock);
        if (!m_playing || m_duration <= 0.0)
            return;
        m_currentTime += seconds;
        if (m_currentTime > m_duration)
            m_currentTime = std::fmod(m_currentTime, m_duration);
        if (m_autorotate) {
            m_azimuth = std::remainder(m_azimuth + seconds * 16.0, 360.0);
            rotated = true;
        }
    }
    emit currentTimeChanged();
    if (rotated)
        emit visualChanged();
}

void VisualizerModel::setCurrentTime(qreal value)
{
    if (!std::isfinite(value))
        return;
    {
        QWriteLocker locker(&m_lock);
        const qreal bounded = m_duration > 0.0 ? std::clamp(value, 0.0, m_duration) : std::max<qreal>(0.0, value);
        if (qFuzzyCompare(m_currentTime + 1.0, bounded + 1.0))
            return;
        m_currentTime = bounded;
    }
    emit currentTimeChanged();
}

void VisualizerModel::setPlaying(bool value)
{
    {
        QWriteLocker locker(&m_lock);
        if (m_playing == value)
            return;
        m_playing = value;
    }
    emit playingChanged();
}

void VisualizerModel::setPresetName(const QString &value)
{
    const QString clean = value.trimmed().isEmpty() ? QStringLiteral("Untitled preset") : value.trimmed();
    {
        QWriteLocker locker(&m_lock);
        if (m_presetName == clean)
            return;
        m_presetName = clean;
    }
    emit presetNameChanged();
}

#define METRIQ_REAL_SETTER(Name, Member, Minimum, Maximum) \
    void VisualizerModel::set##Name(qreal value) \
    { \
        if (!std::isfinite(value)) \
            return; \
        { \
            QWriteLocker locker(&m_lock); \
            const qreal bounded = std::clamp(value, static_cast<qreal>(Minimum), static_cast<qreal>(Maximum)); \
            if (qFuzzyCompare(Member + 1.0, bounded + 1.0)) \
                return; \
            Member = bounded; \
        } \
        emit visualChanged(); \
    }

METRIQ_REAL_SETTER(Elevation, m_elevation, -90.0, 90.0)
METRIQ_REAL_SETTER(Zoom, m_zoom, 0.2, 5.0)
METRIQ_REAL_SETTER(PointSizeScale, m_pointSizeScale, 0.0, 5.0)
METRIQ_REAL_SETTER(LineWidth, m_lineWidth, 0.1, 16.0)
METRIQ_REAL_SETTER(BaseAlpha, m_baseAlpha, 0.0, 1.0)
METRIQ_REAL_SETTER(PointLifespan, m_pointLifespan, 0.02, 120.0)
#undef METRIQ_REAL_SETTER

void VisualizerModel::setAzimuth(qreal value)
{
    if (!std::isfinite(value))
        return;
    {
        QWriteLocker locker(&m_lock);
        const qreal bounded = std::remainder(value, 360.0);
        if (qFuzzyCompare(m_azimuth + 361.0, bounded + 361.0))
            return;
        m_azimuth = bounded;
    }
    emit visualChanged();
}

#define METRIQ_BOOL_SETTER(Name, Member) \
    void VisualizerModel::set##Name(bool value) \
    { \
        { \
            QWriteLocker locker(&m_lock); \
            if (Member == value) \
                return; \
            Member = value; \
        } \
        emit visualChanged(); \
    }

METRIQ_BOOL_SETTER(ConnectLines, m_connectLines)
METRIQ_BOOL_SETTER(ShowAxes, m_showAxes)
METRIQ_BOOL_SETTER(ShowHeadMarker, m_showHeadMarker)
METRIQ_BOOL_SETTER(Autorotate, m_autorotate)
#undef METRIQ_BOOL_SETTER

void VisualizerModel::setRenderMode(const QString &value)
{
    const QString clean = value.trimmed();
    if (clean.isEmpty())
        return;
    {
        QWriteLocker locker(&m_lock);
        if (m_renderMode == clean)
            return;
        m_renderMode = clean;
    }
    emit visualChanged();
}

void VisualizerModel::setHistoryMode(const QString &value)
{
    const QString clean = value.trimmed();
    if (clean.isEmpty())
        return;
    {
        QWriteLocker locker(&m_lock);
        if (m_historyMode == clean)
            return;
        m_historyMode = clean;
    }
    emit visualChanged();
}

void VisualizerModel::setColormap(const QString &value)
{
    const QString clean = value.trimmed();
    if (clean.isEmpty())
        return;
    {
        QWriteLocker locker(&m_lock);
        if (m_colormap == clean)
            return;
        m_colormap = clean;
    }
    emit stateChanged();
}

void VisualizerModel::setLivePointBudget(int value)
{
    value = std::clamp(value, 100, 100000);
    {
        QWriteLocker locker(&m_lock);
        if (m_livePointBudget == value)
            return;
        m_livePointBudget = value;
    }
    emit stateChanged();
}
