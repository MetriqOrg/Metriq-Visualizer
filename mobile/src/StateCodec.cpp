// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "StateCodec.h"

#include "VisualizerModel.h"

#include <QDateTime>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSaveFile>

namespace {
constexpr int kPresetSchemaVersion = 4;
constexpr int kProjectSchemaVersion = 2;
const QString kPresetSchema = QStringLiteral("metriq.visualizer-preset");
const QString kProjectSchema = QStringLiteral("metriq.visualizer-project");

QString displayName(const QJsonObject &object, const QString &fallback)
{
    const QString name = object.value(QStringLiteral("name")).toString().trimmed();
    if (!name.isEmpty())
        return name;
    const QString legacy = object.value(QStringLiteral("preset_name")).toString().trimmed();
    return legacy.isEmpty() ? fallback : legacy;
}

QJsonObject rootState(const QJsonObject &payload, bool allowLegacyRoot)
{
    const QJsonValue value = payload.value(QStringLiteral("state"));
    if (value.isObject())
        return value.toObject();
    if (!allowLegacyRoot)
        return {};

    QJsonObject state = payload;
    const QStringList metadata{
        QStringLiteral("schema"), QStringLiteral("schema_version"),
        QStringLiteral("preset_schema_version"), QStringLiteral("name"),
        QStringLiteral("preset_name"), QStringLiteral("created_at"),
        QStringLiteral("saved_at_utc"), QStringLiteral("format"),
        QStringLiteral("app"), QStringLiteral("app_version"),
    };
    for (const QString &key : metadata)
        state.remove(key);
    return state;
}
} // namespace

StateCodec::StateCodec(VisualizerModel *model, QObject *parent)
    : QObject(parent)
    , m_model(model)
{
}

QString StateCodec::errorMessage() const
{
    return m_errorMessage;
}

QString StateCodec::currentDocument() const
{
    return m_currentDocument;
}

QString StateCodec::pathFromUrl(const QUrl &url)
{
    if (url.scheme() == QStringLiteral("qrc"))
        return QStringLiteral(":") + url.path();
    if (url.isLocalFile())
        return url.toLocalFile();
    return url.toString(QUrl::FullyDecoded);
}

QJsonObject StateCodec::readObject(const QUrl &url, QString *error)
{
    QFile file(pathFromUrl(url));
    if (!file.open(QIODevice::ReadOnly)) {
        if (error)
            *error = QObject::tr("Could not open %1: %2").arg(url.toDisplayString(), file.errorString());
        return {};
    }
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
        if (error)
            *error = QObject::tr("Invalid JSON in %1: %2").arg(url.toDisplayString(), parseError.errorString());
        return {};
    }
    return document.object();
}

bool StateCodec::writeObject(const QUrl &url, const QJsonObject &object, QString *error)
{
    const QString path = pathFromUrl(url);
    if (path.startsWith(QStringLiteral(":")) || path.startsWith(QStringLiteral("content:"))) {
        if (error)
            *error = QObject::tr("This document provider is read-only in the current mobile foundation. Choose an app-local or shared-files location.");
        return false;
    }
    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly)) {
        if (error)
            *error = QObject::tr("Could not write %1: %2").arg(url.toDisplayString(), file.errorString());
        return false;
    }
    const QByteArray data = QJsonDocument(object).toJson(QJsonDocument::Indented);
    if (file.write(data) != data.size() || !file.commit()) {
        if (error)
            *error = QObject::tr("Could not finish writing %1: %2").arg(url.toDisplayString(), file.errorString());
        return false;
    }
    return true;
}

bool StateCodec::loadBundledDefault()
{
    return loadPreset(QUrl(QStringLiteral("qrc:/mobile/resources/presets/Default.mvpreset")));
}

bool StateCodec::openDocument(const QUrl &url)
{
    const QString suffix = QFileInfo(url.path()).suffix().toLower();
    if (suffix == QStringLiteral("mvpreset"))
        return loadPreset(url);
    if (suffix == QStringLiteral("mvproj") || suffix == QStringLiteral("bgl"))
        return loadProject(url);
    if (suffix == QStringLiteral("csv") || suffix == QStringLiteral("tsv") || suffix == QStringLiteral("txt")) {
        if (!m_model) {
            setError(tr("The visualization model is unavailable."));
            return false;
        }
        const bool loaded = m_model->loadMappedData(url);
        if (loaded) {
            setError({});
            setCurrentDocument(pathFromUrl(url));
            emit documentLoaded(QStringLiteral("mapped data"), QFileInfo(url.path()).fileName());
        }
        return loaded;
    }

    QString error;
    const QJsonObject object = readObject(url, &error);
    if (object.isEmpty()) {
        setError(error.isEmpty() ? tr("The document is empty or unsupported.") : error);
        return false;
    }
    const QString schema = object.value(QStringLiteral("schema")).toString();
    if (schema == kPresetSchema)
        return loadPreset(url);
    if (schema == kProjectSchema)
        return loadProject(url);
    setError(tr("Unsupported Visualizer document type."));
    return false;
}

bool StateCodec::loadPreset(const QUrl &url)
{
    QString error;
    const QJsonObject payload = readObject(url, &error);
    if (payload.isEmpty()) {
        setError(error.isEmpty() ? tr("Preset is empty.") : error);
        return false;
    }
    const QString schema = payload.value(QStringLiteral("schema")).toString();
    if (!schema.isEmpty() && schema != kPresetSchema) {
        setError(tr("This JSON file is not a Metriq Visualizer preset."));
        return false;
    }
    const int version = payload.value(QStringLiteral("schema_version"))
                            .toInt(payload.value(QStringLiteral("preset_schema_version")).toInt(1));
    if (version > kPresetSchemaVersion) {
        setError(tr("Preset schema %1 is newer than this application supports.").arg(version));
        return false;
    }
    const QJsonObject state = rootState(payload, true);
    if (state.isEmpty()) {
        setError(tr("Preset state is missing or invalid."));
        return false;
    }
    if (!m_model) {
        setError(tr("The visualization model is unavailable."));
        return false;
    }

    m_presetTemplate = payload;
    m_model->applyState(state);
    const QString name = displayName(payload, QFileInfo(url.path()).completeBaseName());
    m_model->setPresetName(name);
    setError({});
    setCurrentDocument(pathFromUrl(url));
    emit documentLoaded(QStringLiteral("preset"), name);
    return true;
}

bool StateCodec::savePreset(const QUrl &url, const QString &name)
{
    if (!m_model) {
        setError(tr("The visualization model is unavailable."));
        return false;
    }
    QJsonObject payload = m_presetTemplate;
    payload.insert(QStringLiteral("schema"), kPresetSchema);
    payload.insert(QStringLiteral("schema_version"), kPresetSchemaVersion);
    payload.insert(QStringLiteral("name"), name.trimmed().isEmpty() ? m_model->presetName() : name.trimmed());
    if (!payload.contains(QStringLiteral("created_at")))
        payload.insert(QStringLiteral("created_at"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    payload.insert(QStringLiteral("state"), m_model->exportState());

    QString error;
    if (!writeObject(url, payload, &error)) {
        setError(error);
        return false;
    }
    m_presetTemplate = payload;
    setError({});
    setCurrentDocument(pathFromUrl(url));
    emit documentSaved(QStringLiteral("preset"), payload.value(QStringLiteral("name")).toString());
    return true;
}

bool StateCodec::loadProject(const QUrl &url)
{
    QString error;
    const QJsonObject payload = readObject(url, &error);
    if (payload.isEmpty()) {
        setError(error.isEmpty() ? tr("Project is empty.") : error);
        return false;
    }
    const QString schema = payload.value(QStringLiteral("schema")).toString();
    const bool legacy = QFileInfo(url.path()).suffix().compare(QStringLiteral("bgl"), Qt::CaseInsensitive) == 0;
    if (!schema.isEmpty() && schema != kProjectSchema) {
        setError(tr("This JSON file is not a Metriq Visualizer project."));
        return false;
    }
    const int version = payload.value(QStringLiteral("schema_version")).toInt(1);
    if (version > kProjectSchemaVersion) {
        setError(tr("Project schema %1 is newer than this application supports.").arg(version));
        return false;
    }
    const QJsonObject state = rootState(payload, legacy);
    if (state.isEmpty()) {
        setError(tr("Project state is missing or invalid."));
        return false;
    }
    if (!m_model) {
        setError(tr("The visualization model is unavailable."));
        return false;
    }

    m_projectTemplate = payload;
    m_model->applyState(state);
    const QString name = displayName(payload, QFileInfo(url.path()).completeBaseName());
    setError({});
    setCurrentDocument(pathFromUrl(url));
    emit documentLoaded(QStringLiteral("project"), name);
    return true;
}

bool StateCodec::saveProject(const QUrl &url, const QString &name)
{
    if (!m_model) {
        setError(tr("The visualization model is unavailable."));
        return false;
    }
    QJsonObject payload = m_projectTemplate;
    payload.insert(QStringLiteral("schema"), kProjectSchema);
    payload.insert(QStringLiteral("schema_version"), kProjectSchemaVersion);
    payload.insert(QStringLiteral("name"), name.trimmed().isEmpty() ? QStringLiteral("Metriq Visualizer Mobile Project") : name.trimmed());
    if (!payload.contains(QStringLiteral("created_at")))
        payload.insert(QStringLiteral("created_at"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    payload.insert(QStringLiteral("state"), m_model->exportState());
    if (!payload.contains(QStringLiteral("relative_source")))
        payload.insert(QStringLiteral("relative_source"), QString());

    QString error;
    if (!writeObject(url, payload, &error)) {
        setError(error);
        return false;
    }
    m_projectTemplate = payload;
    setError({});
    setCurrentDocument(pathFromUrl(url));
    emit documentSaved(QStringLiteral("project"), payload.value(QStringLiteral("name")).toString());
    return true;
}

void StateCodec::setError(const QString &message)
{
    if (m_errorMessage == message)
        return;
    m_errorMessage = message;
    emit errorChanged();
}

void StateCodec::setCurrentDocument(const QString &path)
{
    if (m_currentDocument == path)
        return;
    m_currentDocument = path;
    emit currentDocumentChanged();
}
