// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QJsonObject>
#include <QObject>
#include <QPointer>
#include <QUrl>

class VisualizerModel;

class StateCodec final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString errorMessage READ errorMessage NOTIFY errorChanged)
    Q_PROPERTY(QString currentDocument READ currentDocument NOTIFY currentDocumentChanged)

public:
    explicit StateCodec(VisualizerModel *model, QObject *parent = nullptr);

    [[nodiscard]] QString errorMessage() const;
    [[nodiscard]] QString currentDocument() const;

    Q_INVOKABLE bool loadBundledDefault();
    Q_INVOKABLE bool openDocument(const QUrl &url);
    Q_INVOKABLE bool loadPreset(const QUrl &url);
    Q_INVOKABLE bool savePreset(const QUrl &url, const QString &name = {});
    Q_INVOKABLE bool loadProject(const QUrl &url);
    Q_INVOKABLE bool saveProject(const QUrl &url, const QString &name = {});

signals:
    void errorChanged();
    void currentDocumentChanged();
    void documentLoaded(const QString &kind, const QString &name);
    void documentSaved(const QString &kind, const QString &name);

private:
    static QString pathFromUrl(const QUrl &url);
    static QJsonObject readObject(const QUrl &url, QString *error);
    static bool writeObject(const QUrl &url, const QJsonObject &object, QString *error);
    void setError(const QString &message);
    void setCurrentDocument(const QString &path);

    QPointer<VisualizerModel> m_model;
    QJsonObject m_presetTemplate;
    QJsonObject m_projectTemplate;
    QString m_errorMessage;
    QString m_currentDocument;
};
