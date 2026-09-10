// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QObject>
#include <QPointer>

class QQuickWindow;

class RenderBackendInfo final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString backendName READ backendName NOTIFY changed)
    Q_PROPERTY(bool gpuAccelerated READ gpuAccelerated NOTIFY changed)
    Q_PROPERTY(bool softwareFallback READ softwareFallback NOTIFY changed)
    Q_PROPERTY(bool initialized READ initialized NOTIFY changed)
    Q_PROPERTY(QString errorMessage READ errorMessage NOTIFY changed)

public:
    explicit RenderBackendInfo(QObject *parent = nullptr);

    [[nodiscard]] QString backendName() const;
    [[nodiscard]] bool gpuAccelerated() const;
    [[nodiscard]] bool softwareFallback() const;
    [[nodiscard]] bool initialized() const;
    [[nodiscard]] QString errorMessage() const;

    void attach(QQuickWindow *window);

signals:
    void changed();

private:
    void publish(int graphicsApi, const QString &error = {});

    QPointer<QQuickWindow> m_window;
    QString m_backendName = QStringLiteral("Initializing");
    QString m_errorMessage;
    bool m_gpuAccelerated = false;
    bool m_initialized = false;
};
