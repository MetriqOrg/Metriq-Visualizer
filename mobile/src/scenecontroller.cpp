#include "scenecontroller.h"

#include <QColor>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QSaveFile>
#include <QTextStream>

#include <algorithm>
#include <array>
#include <cmath>
#include <numbers>
#include <utility>

namespace metriq::mobile {
namespace {

constexpr int kMaximumImportedRows = 1'000'000;
constexpr int kMaximumLivePoints = 12'000;

float clampFinite(float value, float minimum, float maximum, float fallback)
{
    return std::isfinite(value) ? std::clamp(value, minimum, maximum) : fallback;
}

double jsonDouble(const QJsonObject &object, const char *key, double fallback)
{
    const QJsonValue value = object.value(QLatin1String(key));
    return value.isDouble() ? value.toDouble(fallback) : fallback;
}

bool jsonBool(const QJsonObject &object, const char *key, bool fallback)
{
    const QJsonValue value = object.value(QLatin1String(key));
    return value.isBool() ? value.toBool(fallback) : fallback;
}

QString jsonString(const QJsonObject &object, const char *key, const QString &fallback = {})
{
    const QJsonValue value = object.value(QLatin1String(key));
    return value.isString() ? value.toString(fallback) : fallback;
}

QVector4D colorFromProgress(float progress, float strength = 1.0f)
{
    const float hue = std::fmod(0.54f + progress * 0.46f, 1.0f);
    const QColor color = QColor::fromHsvF(hue, 0.72f, std::clamp(0.58f + strength * 0.35f, 0.0f, 1.0f));
    return QVector4D(color.redF(), color.greenF(), color.blueF(), 1.0f);
}

QString localPath(const QUrl &url)
{
    if (url.isLocalFile())
        return url.toLocalFile();
    if (url.scheme().isEmpty())
        return url.toString();
    return {};
}

QVector<QString> parseDelimitedLine(const QString &line, QChar delimiter)
{
    QVector<QString> fields;
    QString field;
    bool quoted = false;
    for (int index = 0; index < line.size(); ++index) {
        const QChar character = line.at(index);
        if (character == QLatin1Char('"')) {
            if (quoted && index + 1 < line.size() && line.at(index + 1) == QLatin1Char('"')) {
                field.append(QLatin1Char('"'));
                ++index;
            } else {
                quoted = !quoted;
            }
        } else if (!quoted && character == delimiter) {
            fields.append(field.trimmed());
            field.clear();
        } else {
            field.append(character);
        }
    }
    fields.append(field.trimmed());
    return fields;
}

int delimiterScore(const QString &line, QChar delimiter)
{
    bool quoted = false;
    int score = 0;
    for (int index = 0; index < line.size(); ++index) {
        const QChar character = line.at(index);
        if (character == QLatin1Char('"'))
            quoted = !quoted;
        else if (!quoted && character == delimiter)
            ++score;
    }
    return score;
}

QChar detectDelimiter(const QString &line)
{
    const std::array<QChar, 3> candidates{QLatin1Char(','), QLatin1Char('\t'), QLatin1Char(';')};
    return *std::max_element(candidates.cbegin(), candidates.cend(), [&line](QChar left, QChar right) {
        return delimiterScore(line, left) < delimiterScore(line, right);
    });
}

bool parseNumber(const QString &text, double *value)
{
    bool ok = false;
    const double parsed = text.trimmed().toDouble(&ok);
    if (!ok || !std::isfinite(parsed))
        return false;
    if (value)
        *value = parsed;
    return true;
}

QVector<int> chooseNumericColumns(const QVector<QVector<QString>> &sampleRows, int maximumColumns = 3)
{
    if (sampleRows.isEmpty())
        return {};
    int columnCount = 0;
    for (const auto &row : sampleRows)
        columnCount = std::max(columnCount, row.size());

    struct Candidate { int column; int count; };
    QVector<Candidate> candidates;
    for (int column = 0; column < columnCount; ++column) {
        int count = 0;
        for (const auto &row : sampleRows) {
            if (column < row.size() && parseNumber(row.at(column), nullptr))
                ++count;
        }
        if (count >= std::max(2, static_cast<int>(std::ceil(sampleRows.size() * 0.55))))
            candidates.append({column, count});
    }
    std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate &left, const Candidate &right) {
        return left.count > right.count;
    });
    QVector<int> result;
    for (const auto &candidate : candidates) {
        result.append(candidate.column);
        if (result.size() == maximumColumns)
            break;
    }
    std::sort(result.begin(), result.end());
    return result;
}

QVector<float> normalizeColumn(const QVector<double> &values)
{
    QVector<float> normalized(values.size(), 0.0f);
    if (values.isEmpty())
        return normalized;

    QVector<double> sorted = values;
    std::sort(sorted.begin(), sorted.end());
    const int lastIndex = static_cast<int>(sorted.size()) - 1;
    const int lowIndex = std::clamp(static_cast<int>(sorted.size() * 0.01), 0, lastIndex);
    const int highIndex = std::clamp(static_cast<int>(sorted.size() * 0.99), 0, lastIndex);
    const double low = sorted.at(lowIndex);
    const double high = sorted.at(highIndex);
    const double center = (low + high) * 0.5;
    const double halfSpan = std::max(1.0e-12, (high - low) * 0.5);
    for (int index = 0; index < values.size(); ++index)
        normalized[index] = static_cast<float>(std::clamp((values.at(index) - center) / halfSpan, -1.35, 1.35));
    return normalized;
}

QJsonObject objectValue(const QJsonObject &parent, const char *key)
{
    const auto value = parent.value(QLatin1String(key));
    return value.isObject() ? value.toObject() : QJsonObject{};
}

QJsonObject mergeObjects(QJsonObject base, const QJsonObject &updates)
{
    for (auto iterator = updates.begin(); iterator != updates.end(); ++iterator) {
        if (iterator.value().isObject() && base.value(iterator.key()).isObject())
            base.insert(iterator.key(), mergeObjects(base.value(iterator.key()).toObject(), iterator.value().toObject()));
        else
            base.insert(iterator.key(), iterator.value());
    }
    return base;
}

bool writeJson(const QString &path, const QJsonObject &object, QString *error)
{
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        if (error)
            *error = file.errorString();
        return false;
    }
    const QByteArray bytes = QJsonDocument(object).toJson(QJsonDocument::Indented);
    if (file.write(bytes) != bytes.size() || !file.commit()) {
        if (error)
            *error = file.errorString();
        return false;
    }
    return true;
}

bool readJson(const QString &path, QJsonObject *object, QString *error)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        if (error)
            *error = file.errorString();
        return false;
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error)
            *error = parseError.errorString();
        return false;
    }
    *object = document.object();
    return true;
}

} // namespace

SceneController::SceneController(QObject *parent)
    : QObject(parent)
    , m_audio(this)
{
    connect(&m_audio, &AudioEngine::metricsReady, this, &SceneController::appendLiveMetrics);
    generateDemo();
}

void SceneController::generateDemo(int count)
{
    count = std::clamp(count, 128, 200'000);
    QVector<ScenePoint> points;
    points.reserve(count);
    for (int index = 0; index < count; ++index) {
        const float time = count > 1 ? static_cast<float>(index) / static_cast<float>(count - 1) : 0.0f;
        const float phase = time * 2.0f * std::numbers::pi_v<float>;
        const float pulse = 0.5f + 0.5f * std::sin(phase * 17.0f);
        const float radius = 0.52f + 0.18f * std::sin(phase * 7.0f);
        ScenePoint point;
        point.position = QVector3D(
            radius * std::cos(phase * 3.0f),
            radius * std::sin(phase * 3.0f),
            0.68f * std::sin(phase * 5.0f) + 0.12f * std::cos(phase * 13.0f));
        point.color = colorFromProgress(time, pulse);
        point.size = 0.65f + pulse * 1.1f;
        point.time = time;
        points.append(point);
    }
    m_audio.stop();
    replaceScene(std::move(points), QStringLiteral("Data Disco"), QStringLiteral("Generated demo"), {}, 60.0);
    m_progress = 1.0;
    emit progressChanged();
    setStatus(QStringLiteral("GPU demo ready"));
}

bool SceneController::loadSource(const QUrl &url)
{
    const QString path = localPath(url);
    if (path.isEmpty()) {
        setStatus(QStringLiteral("Only local files are supported in this build"));
        return false;
    }
    const QString suffix = QFileInfo(path).suffix().toLower();
    if (suffix == QLatin1String("mvpreset"))
        return loadPreset(url);
    if (suffix == QLatin1String("mvproj") || suffix == QLatin1String("bgl"))
        return loadProject(url);
    if (suffix == QLatin1String("csv") || suffix == QLatin1String("tsv") || suffix == QLatin1String("txt"))
        return loadTable(path);
    setStatus(QStringLiteral("This vertical slice currently imports CSV, TSV, TXT, .mvpreset and .mvproj files"));
    return false;
}

bool SceneController::loadTable(const QString &path)
{
    QFile sampleFile(path);
    if (!sampleFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        setStatus(QStringLiteral("Could not open source: %1").arg(sampleFile.errorString()));
        return false;
    }
    QTextStream sampleStream(&sampleFile);
    QVector<QString> rawSampleLines;
    while (!sampleStream.atEnd() && rawSampleLines.size() < 65) {
        QString line = sampleStream.readLine();
        if (rawSampleLines.isEmpty() && line.startsWith(QChar::ByteOrderMark))
            line.remove(0, 1);
        if (!line.trimmed().isEmpty())
            rawSampleLines.append(line);
    }
    if (rawSampleLines.size() < 2) {
        setStatus(QStringLiteral("The table does not contain enough rows"));
        return false;
    }
    const QChar delimiter = QFileInfo(path).suffix().compare(QLatin1String("tsv"), Qt::CaseInsensitive) == 0
        ? QLatin1Char('\t') : detectDelimiter(rawSampleLines.front());
    QVector<QVector<QString>> sampleRows;
    sampleRows.reserve(rawSampleLines.size());
    for (const QString &line : std::as_const(rawSampleLines))
        sampleRows.append(parseDelimitedLine(line, delimiter));
    const QVector<int> columns = chooseNumericColumns(sampleRows);
    if (columns.isEmpty()) {
        setStatus(QStringLiteral("No consistently numeric columns were found"));
        return false;
    }

    const bool firstLineIsHeader = std::any_of(columns.cbegin(), columns.cend(), [&sampleRows](int column) {
        return column >= sampleRows.front().size() || !parseNumber(sampleRows.front().at(column), nullptr);
    });

    QFile dataFile(path);
    if (!dataFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
        setStatus(QStringLiteral("Could not reopen source: %1").arg(dataFile.errorString()));
        return false;
    }
    QTextStream stream(&dataFile);
    QVector<QVector<double>> numeric(columns.size());
    int lineNumber = 0;
    int accepted = 0;
    while (!stream.atEnd() && accepted < kMaximumImportedRows) {
        QString line = stream.readLine();
        if (lineNumber == 0 && line.startsWith(QChar::ByteOrderMark))
            line.remove(0, 1);
        if (lineNumber++ == 0 && firstLineIsHeader)
            continue;
        if (line.trimmed().isEmpty())
            continue;
        const QVector<QString> fields = parseDelimitedLine(line, delimiter);
        QVector<double> row;
        row.reserve(columns.size());
        bool valid = true;
        for (const int column : columns) {
            double value = 0.0;
            if (column >= fields.size() || !parseNumber(fields.at(column), &value)) {
                valid = false;
                break;
            }
            row.append(value);
        }
        if (!valid)
            continue;
        for (int index = 0; index < row.size(); ++index)
            numeric[index].append(row.at(index));
        ++accepted;
    }
    if (accepted < 2) {
        setStatus(QStringLiteral("The selected numeric columns contain too little usable data"));
        return false;
    }

    QVector<QVector<float>> normalized;
    normalized.reserve(numeric.size());
    for (const auto &column : std::as_const(numeric))
        normalized.append(normalizeColumn(column));

    QVector<ScenePoint> points;
    points.reserve(accepted);
    float priorMagnitude = 0.0f;
    for (int row = 0; row < accepted; ++row) {
        const float time = static_cast<float>(row) / static_cast<float>(accepted - 1);
        const float first = normalized[0][row];
        const float second = normalized.size() > 1 ? normalized[1][row] : 0.0f;
        const float third = normalized.size() > 2 ? normalized[2][row] : 0.0f;
        const float magnitude = std::sqrt(first * first + second * second + third * third);
        ScenePoint point;
        if (normalized.size() == 1) {
            point.position = QVector3D(time * 2.0f - 1.0f, first, std::clamp(magnitude - priorMagnitude, -1.0f, 1.0f));
        } else if (normalized.size() == 2) {
            point.position = QVector3D(first, second, std::clamp(magnitude - priorMagnitude, -1.0f, 1.0f));
        } else {
            point.position = QVector3D(first, second, third);
        }
        point.color = colorFromProgress(time, std::clamp(magnitude / 1.7f, 0.0f, 1.0f));
        point.size = 0.55f + std::clamp(magnitude / 1.7f, 0.0f, 1.0f) * 1.3f;
        point.time = time;
        points.append(point);
        priorMagnitude = magnitude;
    }

    m_audio.stop();
    replaceScene(std::move(points), QFileInfo(path).completeBaseName(), QStringLiteral("Table"), path,
                 std::max(1.0, accepted / 60.0));
    m_progress = 1.0;
    emit progressChanged();
    setStatus(accepted >= kMaximumImportedRows
                  ? QStringLiteral("Loaded the first %1 valid rows (mobile safety ceiling)").arg(accepted)
                  : QStringLiteral("Loaded %1 rows using %2 numeric column(s)").arg(accepted).arg(columns.size()));
    return true;
}

bool SceneController::loadPreset(const QUrl &url)
{
    const QString path = localPath(url);
    QJsonObject document;
    QString error;
    if (path.isEmpty() || !readJson(path, &document, &error)) {
        setStatus(QStringLiteral("Could not read preset: %1").arg(error));
        return false;
    }
    const QString schema = jsonString(document, "schema");
    if (!schema.isEmpty() && schema != QLatin1String("metriq.visualizer-preset")) {
        setStatus(QStringLiteral("The selected JSON is not a Metriq Visualizer preset"));
        return false;
    }
    const int version = document.value(QStringLiteral("schema_version")).toInt(1);
    if (version > 4) {
        setStatus(QStringLiteral("Preset schema %1 is newer than this mobile build supports").arg(version));
        return false;
    }
    const QJsonObject state = document.value(QStringLiteral("state")).isObject()
        ? document.value(QStringLiteral("state")).toObject() : document;
    m_presetDocument = document;
    m_presetName = jsonString(document, "name", QFileInfo(path).completeBaseName());
    applyState(state);
    emit presetChanged();
    setStatus(QStringLiteral("Loaded preset “%1”; unsupported fields are retained for round-trip saving").arg(m_presetName));
    return true;
}

bool SceneController::savePreset(const QUrl &url)
{
    QString path = localPath(url);
    if (path.isEmpty()) {
        setStatus(QStringLiteral("Choose a local preset destination"));
        return false;
    }
    if (!path.endsWith(QLatin1String(".mvpreset"), Qt::CaseInsensitive))
        path += QStringLiteral(".mvpreset");

    QJsonObject document = m_presetDocument;
    document.insert(QStringLiteral("schema"), QStringLiteral("metriq.visualizer-preset"));
    document.insert(QStringLiteral("schema_version"), 4);
    document.insert(QStringLiteral("name"), m_presetName.isEmpty() ? QStringLiteral("Mobile Preset") : m_presetName);
    const QJsonObject state = mergeObjects(document.value(QStringLiteral("state")).toObject(), currentState());
    document.insert(QStringLiteral("state"), state);

    QString error;
    if (!writeJson(path, document, &error)) {
        setStatus(QStringLiteral("Could not save preset: %1").arg(error));
        return false;
    }
    m_presetDocument = document;
    setStatus(QStringLiteral("Saved preset transactionally"));
    return true;
}

bool SceneController::loadProject(const QUrl &url)
{
    const QString path = localPath(url);
    QJsonObject document;
    QString error;
    if (path.isEmpty() || !readJson(path, &document, &error)) {
        setStatus(QStringLiteral("Could not read project: %1").arg(error));
        return false;
    }
    const QString schema = jsonString(document, "schema");
    if (!schema.isEmpty() && schema != QLatin1String("metriq.visualizer-project")) {
        setStatus(QStringLiteral("The selected JSON is not a Metriq Visualizer project"));
        return false;
    }
    const int version = document.value(QStringLiteral("schema_version")).toInt(1);
    if (version > 2) {
        setStatus(QStringLiteral("Project schema %1 is newer than this mobile build supports").arg(version));
        return false;
    }
    const QJsonObject state = document.value(QStringLiteral("state")).isObject()
        ? document.value(QStringLiteral("state")).toObject() : document;
    m_projectDocument = document;
    applyState(state);

    QString candidate;
    const QString relative = jsonString(document, "relative_source");
    if (!relative.isEmpty())
        candidate = QFileInfo(path).dir().absoluteFilePath(relative);
    if (candidate.isEmpty())
        candidate = jsonString(objectValue(state, "session"), "file_path");
    if (!candidate.isEmpty() && QFileInfo::exists(candidate))
        loadSource(QUrl::fromLocalFile(candidate));
    setStatus(QStringLiteral("Loaded project state%1").arg(candidate.isEmpty() ? QString() : QStringLiteral(" and its source")));
    return true;
}

bool SceneController::saveProject(const QUrl &url)
{
    QString path = localPath(url);
    if (path.isEmpty()) {
        setStatus(QStringLiteral("Choose a local project destination"));
        return false;
    }
    if (!path.endsWith(QLatin1String(".mvproj"), Qt::CaseInsensitive))
        path += QStringLiteral(".mvproj");

    QJsonObject document = m_projectDocument;
    document.insert(QStringLiteral("schema"), QStringLiteral("metriq.visualizer-project"));
    document.insert(QStringLiteral("schema_version"), 2);
    document.insert(QStringLiteral("name"), m_sourceName.isEmpty() ? QStringLiteral("Mobile Project") : m_sourceName);
    QJsonObject state = mergeObjects(document.value(QStringLiteral("state")).toObject(), currentState());
    QJsonObject session = state.value(QStringLiteral("session")).toObject();
    session.insert(QStringLiteral("file_path"), m_sourcePath);
    state.insert(QStringLiteral("session"), session);
    document.insert(QStringLiteral("state"), state);
    if (!m_sourcePath.isEmpty()) {
        const QDir projectDir = QFileInfo(path).dir();
        document.insert(QStringLiteral("relative_source"), projectDir.relativeFilePath(m_sourcePath));
    }

    QString error;
    if (!writeJson(path, document, &error)) {
        setStatus(QStringLiteral("Could not save project: %1").arg(error));
        return false;
    }
    m_projectDocument = document;
    setStatus(QStringLiteral("Saved project transactionally"));
    return true;
}

void SceneController::toggleMicrophone()
{
    if (m_audio.active())
        m_audio.stop();
    else
        m_audio.start();
}

void SceneController::advance(double seconds)
{
    if (!m_playing || !std::isfinite(seconds) || seconds <= 0.0)
        return;
    if (m_autorotate)
        setAzimuth(m_azimuth + m_rotationSpeed * static_cast<float>(seconds));
    if (m_sourceKind == QLatin1String("Live microphone")) {
        setProgress(1.0);
        return;
    }
    const double safeDuration = std::max(0.1, m_duration);
    double next = m_progress + seconds / safeDuration;
    if (next > 1.0)
        next = std::fmod(next, 1.0);
    setProgress(next);
}

void SceneController::orbit(float deltaX, float deltaY)
{
    if (!std::isfinite(deltaX) || !std::isfinite(deltaY))
        return;
    setAzimuth(m_azimuth - deltaX * 0.18f);
    setElevation(m_elevation - deltaY * 0.18f);
}

void SceneController::resetCamera()
{
    const bool changed = !qFuzzyCompare(m_azimuth, 35.0f)
        || !qFuzzyCompare(m_elevation, 24.0f)
        || !qFuzzyCompare(m_zoom, 1.0f);
    m_azimuth = 35.0f;
    m_elevation = 24.0f;
    m_zoom = 1.0f;
    if (changed)
        emit cameraChanged();
}

void SceneController::setApplicationActive(bool active)
{
    m_audio.setApplicationActive(active);
    if (!active && m_playing)
        setStatus(QStringLiteral("Playback paused while the app is in the background"));
}

void SceneController::setProgress(double value)
{
    value = std::isfinite(value) ? std::clamp(value, 0.0, 1.0) : 0.0;
    if (qFuzzyCompare(m_progress, value))
        return;
    m_progress = value;
    emit progressChanged();
}

void SceneController::setPlaying(bool playing)
{
    if (m_playing == playing)
        return;
    m_playing = playing;
    emit playingChanged();
}

void SceneController::setAzimuth(float value)
{
    value = std::isfinite(value) ? std::remainder(value, 360.0f) : 0.0f;
    if (qFuzzyCompare(m_azimuth, value))
        return;
    m_azimuth = value;
    emit cameraChanged();
}

void SceneController::setElevation(float value)
{
    value = clampFinite(value, -89.0f, 89.0f, 24.0f);
    if (qFuzzyCompare(m_elevation, value))
        return;
    m_elevation = value;
    emit cameraChanged();
}

void SceneController::setZoom(float value)
{
    value = clampFinite(value, 0.25f, 4.0f, 1.0f);
    if (qFuzzyCompare(m_zoom, value))
        return;
    m_zoom = value;
    emit cameraChanged();
}

#define METRIQ_BOOL_SETTER(Method, Member) \
    void SceneController::Method(bool value) \
    { \
        if (Member == value) \
            return; \
        Member = value; \
        emit renderSettingsChanged(); \
    }

METRIQ_BOOL_SETTER(setAutorotate, m_autorotate)
METRIQ_BOOL_SETTER(setShowLine, m_showLine)
METRIQ_BOOL_SETTER(setShowPoints, m_showPoints)
METRIQ_BOOL_SETTER(setShowGrid, m_showGrid)
METRIQ_BOOL_SETTER(setShowAxes, m_showAxes)
METRIQ_BOOL_SETTER(setShowHeadMarker, m_showHeadMarker)

#undef METRIQ_BOOL_SETTER

void SceneController::setRotationSpeed(float value)
{
    value = clampFinite(value, -180.0f, 180.0f, 16.0f);
    if (qFuzzyCompare(m_rotationSpeed, value))
        return;
    m_rotationSpeed = value;
    emit renderSettingsChanged();
}

#define METRIQ_FLOAT_SETTER(Method, Member, Minimum, Maximum, Fallback) \
    void SceneController::Method(float value) \
    { \
        value = clampFinite(value, Minimum, Maximum, Fallback); \
        if (qFuzzyCompare(Member, value)) \
            return; \
        Member = value; \
        emit renderSettingsChanged(); \
    }

METRIQ_FLOAT_SETTER(setLineWidth, m_lineWidth, 0.25f, 16.0f, 1.55f)
METRIQ_FLOAT_SETTER(setPointScale, m_pointScale, 0.0f, 4.0f, 0.4f)
METRIQ_FLOAT_SETTER(setBaseAlpha, m_baseAlpha, 0.0f, 1.0f, 1.0f)
METRIQ_FLOAT_SETTER(setTrailLength, m_trailLength, 0.005f, 1.0f, 0.22f)
METRIQ_FLOAT_SETTER(setFadeCurve, m_fadeCurve, 0.05f, 5.0f, 0.4f)
METRIQ_FLOAT_SETTER(setHeadScale, m_headScale, 0.0f, 4.0f, 0.24f)
METRIQ_FLOAT_SETTER(setHaloScale, m_haloScale, 0.0f, 6.0f, 0.45f)

#undef METRIQ_FLOAT_SETTER

void SceneController::setHistoryMode(int value)
{
    value = std::clamp(value, 0, 2);
    if (m_historyMode == value)
        return;
    m_historyMode = value;
    emit renderSettingsChanged();
}

void SceneController::setPerformanceMode(const QString &value)
{
    const QString requested = value.trimmed();
    QString normalized = QStringLiteral("Balanced");
    if (requested.contains(QLatin1String("fast"), Qt::CaseInsensitive)
        || requested.contains(QLatin1String("battery"), Qt::CaseInsensitive)
        || requested.contains(QLatin1String("draft"), Qt::CaseInsensitive)) {
        normalized = QStringLiteral("Fast preview");
    } else if (requested.contains(QLatin1String("high"), Qt::CaseInsensitive)
               || requested.compare(QLatin1String("quality"), Qt::CaseInsensitive) == 0) {
        normalized = QStringLiteral("High quality");
    } else if (requested.contains(QLatin1String("full"), Qt::CaseInsensitive)
               || requested.contains(QLatin1String("ultra"), Qt::CaseInsensitive)
               || requested.contains(QLatin1String("render"), Qt::CaseInsensitive)) {
        normalized = QStringLiteral("Full live simulation");
    }

    int targetFps = 60;
    int pointBudget = 6000;
    if (normalized == QLatin1String("Fast preview")) {
        targetFps = 30;
        pointBudget = 3000;
    } else if (normalized == QLatin1String("High quality")) {
        pointBudget = 12'000;
    } else if (normalized == QLatin1String("Full live simulation")) {
        pointBudget = 50'000;
    }

    const bool nameChanged = m_performanceMode != normalized;
    const bool fpsChanged = m_targetFps != targetFps;
    const bool budgetChanged = m_pointBudget != pointBudget;
    if (!nameChanged && !fpsChanged && !budgetChanged)
        return;
    m_performanceMode = normalized;
    m_targetFps = targetFps;
    m_pointBudget = pointBudget;
    if (budgetChanged)
        rebuildRenderPoints();
    emit performanceChanged();
}

void SceneController::setTargetFps(int value)
{
    value = std::clamp(value, 15, 120);
    if (m_targetFps == value)
        return;
    m_targetFps = value;
    emit performanceChanged();
}

void SceneController::setPointBudget(int value)
{
    value = std::clamp(value, 128, 250'000);
    if (m_pointBudget == value)
        return;
    m_pointBudget = value;
    rebuildRenderPoints();
    emit performanceChanged();
}

void SceneController::applyState(const QJsonObject &state)
{
    const QJsonObject visual = objectValue(state, "visual");
    const QJsonObject performance = objectValue(state, "performance");

    setAzimuth(static_cast<float>(jsonDouble(visual, "azim", m_azimuth)));
    setElevation(static_cast<float>(jsonDouble(visual, "elev", m_elevation)));
    setZoom(static_cast<float>(jsonDouble(visual, "zoom", m_zoom)));
    setAutorotate(jsonBool(visual, "autorotate", m_autorotate));
    setRotationSpeed(static_cast<float>(jsonDouble(visual, "rotation_speed", m_rotationSpeed)));
    const QString renderMode = jsonString(visual, "render_mode", QStringLiteral("Points + line"));
    const bool pointsOnly = renderMode.contains(QLatin1String("points only"), Qt::CaseInsensitive);
    setShowLine(jsonBool(visual, "connect_lines", m_showLine) && !pointsOnly);
    setShowPoints(renderMode.contains(QLatin1String("point"), Qt::CaseInsensitive));
    setShowGrid(jsonBool(visual, "show_grid", m_showGrid));
    setShowAxes(jsonBool(visual, "show_axes", m_showAxes));
    setShowHeadMarker(jsonBool(visual, "show_head_marker", m_showHeadMarker));
    setLineWidth(static_cast<float>(jsonDouble(visual, "line_width", m_lineWidth)));
    setPointScale(static_cast<float>(jsonDouble(visual, "point_size_scale", m_pointScale)));
    setBaseAlpha(static_cast<float>(jsonDouble(visual, "base_alpha", m_baseAlpha)));
    setFadeCurve(static_cast<float>(jsonDouble(visual, "fade_curve", m_fadeCurve)));
    setHeadScale(static_cast<float>(jsonDouble(visual, "head_size_scale", m_headScale)));
    setHaloScale(static_cast<float>(jsonDouble(visual, "halo_size_scale", m_haloScale)));

    const QString history = jsonString(visual, "history_mode", QStringLiteral("Trail fade"));
    setHistoryMode(history.contains(QLatin1String("cumulative"), Qt::CaseInsensitive) ? 1
                   : history.contains(QLatin1String("full"), Qt::CaseInsensitive) ? 2 : 0);
    const double lifespan = jsonDouble(visual, "point_lifespan", 1.0);
    const double comet = jsonDouble(visual, "comet_duration", 0.28);
    setTrailLength(static_cast<float>(std::clamp(std::max(lifespan * 0.18, comet * 0.75), 0.005, 1.0)));

    if (!performance.isEmpty()) {
        setPerformanceMode(jsonString(performance, "mode", m_performanceMode));
        if (performance.value(QStringLiteral("live_redraw_fps")).isDouble())
            setTargetFps(performance.value(QStringLiteral("live_redraw_fps")).toInt(m_targetFps));
        if (performance.value(QStringLiteral("live_point_budget")).isDouble())
            setPointBudget(performance.value(QStringLiteral("live_point_budget")).toInt(m_pointBudget));
    }
}

QJsonObject SceneController::currentState() const
{
    QJsonObject visual;
    visual.insert(QStringLiteral("azim"), m_azimuth);
    visual.insert(QStringLiteral("elev"), m_elevation);
    visual.insert(QStringLiteral("zoom"), m_zoom);
    visual.insert(QStringLiteral("autorotate"), m_autorotate);
    visual.insert(QStringLiteral("rotation_speed"), m_rotationSpeed);
    visual.insert(QStringLiteral("connect_lines"), m_showLine);
    visual.insert(QStringLiteral("render_mode"), m_showPoints ? (m_showLine ? QStringLiteral("Points + line") : QStringLiteral("Points only")) : QStringLiteral("Line only"));
    visual.insert(QStringLiteral("show_grid"), m_showGrid);
    visual.insert(QStringLiteral("show_axes"), m_showAxes);
    visual.insert(QStringLiteral("show_head_marker"), m_showHeadMarker);
    visual.insert(QStringLiteral("line_width"), m_lineWidth);
    visual.insert(QStringLiteral("point_size_scale"), m_pointScale);
    visual.insert(QStringLiteral("base_alpha"), m_baseAlpha);
    visual.insert(QStringLiteral("fade_curve"), m_fadeCurve);
    visual.insert(QStringLiteral("head_size_scale"), m_headScale);
    visual.insert(QStringLiteral("halo_size_scale"), m_haloScale);
    visual.insert(QStringLiteral("history_mode"), m_historyMode == 1 ? QStringLiteral("Cumulative reveal")
                  : m_historyMode == 2 ? QStringLiteral("Full static") : QStringLiteral("Trail fade"));
    visual.insert(QStringLiteral("point_lifespan"), std::clamp(m_trailLength / 0.18f, 0.0f, 1.0f));
    visual.insert(QStringLiteral("comet_duration"), std::clamp(m_trailLength / 0.75f, 0.0f, 1.0f));

    QJsonObject performance;
    performance.insert(QStringLiteral("mode"), m_performanceMode);
    performance.insert(QStringLiteral("live_redraw_fps"), m_targetFps);
    performance.insert(QStringLiteral("live_point_budget"), m_pointBudget);
    performance.insert(QStringLiteral("adaptive"), true);

    QJsonObject state;
    state.insert(QStringLiteral("visual"), visual);
    state.insert(QStringLiteral("performance"), performance);
    return state;
}

void SceneController::appendLiveMetrics(const AudioMetrics &metrics)
{
    if (m_sourceKind != QLatin1String("Live microphone")) {
        m_points.clear();
        m_renderPoints.clear();
        m_sourceName = QStringLiteral("Live microphone");
        m_sourceKind = QStringLiteral("Live microphone");
        m_sourcePath.clear();
        m_duration = 60.0;
        m_liveSequence = 0;
        emit sourceChanged();
    }

    const float phase = static_cast<float>(m_liveSequence) * 0.052f;
    const float centroid = std::clamp(metrics.spectralCentroidHz / 12'000.0f, 0.0f, 1.0f);
    const float energy = std::clamp(metrics.rms * 8.0f, 0.0f, 1.0f);
    const float radius = 0.22f + centroid * 0.62f;
    ScenePoint point;
    point.position = QVector3D(
        radius * std::cos(phase),
        radius * std::sin(phase),
        std::clamp((energy - 0.5f) * 1.4f + metrics.onsetStrength * 0.55f, -1.0f, 1.0f));
    point.color = colorFromProgress(centroid, std::max(energy, metrics.onsetStrength));
    point.size = 0.55f + energy * 1.2f + metrics.onsetStrength * 0.8f;
    m_points.append(point);
    ++m_liveSequence;
    if (m_points.size() > kMaximumLivePoints)
        m_points.remove(0, m_points.size() - kMaximumLivePoints);
    const int divisor = std::max(1, m_points.size() - 1);
    for (int index = 0; index < m_points.size(); ++index)
        m_points[index].time = static_cast<float>(index) / divisor;
    m_progress = 1.0;
    rebuildRenderPoints();
    emit progressChanged();
    setStatus(QStringLiteral("Live: %1 Hz centroid · %2% level")
                  .arg(qRound(metrics.spectralCentroidHz))
                  .arg(qRound(energy * 100.0f)));
}

void SceneController::rebuildRenderPoints()
{
    if (m_points.size() <= m_pointBudget) {
        m_renderPoints = m_points;
    } else {
        m_renderPoints.clear();
        m_renderPoints.reserve(m_pointBudget);
        const double step = static_cast<double>(m_points.size() - 1) / (m_pointBudget - 1);
        int previous = -1;
        for (int index = 0; index < m_pointBudget; ++index) {
            const int source = std::clamp(qRound(index * step), 0, m_points.size() - 1);
            if (source != previous) {
                m_renderPoints.append(m_points.at(source));
                previous = source;
            }
        }
        if (m_renderPoints.isEmpty() || m_renderPoints.back().time != m_points.back().time)
            m_renderPoints.append(m_points.back());
    }
    ++m_sceneRevision;
    emit sceneChanged();
}

void SceneController::replaceScene(QVector<ScenePoint> points,
                                   QString sourceName,
                                   QString sourceKind,
                                   QString sourcePath,
                                   double duration)
{
    m_points = std::move(points);
    m_sourceName = std::move(sourceName);
    m_sourceKind = std::move(sourceKind);
    m_sourcePath = std::move(sourcePath);
    m_duration = std::max(0.1, duration);
    rebuildRenderPoints();
    emit sourceChanged();
}

void SceneController::setStatus(QString status)
{
    if (m_status == status)
        return;
    m_status = std::move(status);
    emit statusChanged();
}

} // namespace metriq::mobile
