// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "RenderBackendInfo.h"

#include <QMetaObject>
#include <QQuickWindow>
#include <QSGRendererInterface>
#include <QTimer>

RenderBackendInfo::RenderBackendInfo(QObject *parent)
    : QObject(parent)
{
}

QString RenderBackendInfo::backendName() const
{
    return m_backendName;
}

bool RenderBackendInfo::gpuAccelerated() const
{
    return m_gpuAccelerated;
}

bool RenderBackendInfo::softwareFallback() const
{
    return m_initialized && !m_gpuAccelerated;
}

bool RenderBackendInfo::initialized() const
{
    return m_initialized;
}

QString RenderBackendInfo::errorMessage() const
{
    return m_errorMessage;
}

void RenderBackendInfo::attach(QQuickWindow *window)
{
    if (m_window == window || !window)
        return;
    if (m_window)
        disconnect(m_window, nullptr, this, nullptr);
    m_window = window;

    connect(window, &QQuickWindow::sceneGraphInitialized, this, [this, window]() {
        const auto api = window->rendererInterface()->graphicsApi();
        QMetaObject::invokeMethod(this, [this, api]() { publish(static_cast<int>(api)); }, Qt::QueuedConnection);
    }, Qt::DirectConnection);

    connect(window, &QQuickWindow::sceneGraphInvalidated, this, [this]() {
        QMetaObject::invokeMethod(this, [this]() {
            m_initialized = false;
            m_backendName = QStringLiteral("Renderer unavailable");
            m_gpuAccelerated = false;
            emit changed();
        }, Qt::QueuedConnection);
    }, Qt::DirectConnection);

    connect(window, &QQuickWindow::sceneGraphError, this,
            [this](QQuickWindow::SceneGraphError, const QString &message) {
                QMetaObject::invokeMethod(this, [this, message]() {
                    publish(static_cast<int>(QSGRendererInterface::Software), message);
                }, Qt::QueuedConnection);
            });

    QTimer::singleShot(0, this, [this, window]() {
        if (!window->rendererInterface())
            return;
        const auto api = window->rendererInterface()->graphicsApi();
        if (api != QSGRendererInterface::Unknown)
            publish(static_cast<int>(api));
    });
}

void RenderBackendInfo::publish(int graphicsApi, const QString &error)
{
    const auto api = static_cast<QSGRendererInterface::GraphicsApi>(graphicsApi);
    QString name;
    bool accelerated = false;
    switch (api) {
    case QSGRendererInterface::OpenGL:
        name = QStringLiteral("OpenGL / OpenGL ES");
        accelerated = true;
        break;
    case QSGRendererInterface::Direct3D11:
        name = QStringLiteral("Direct3D 11");
        accelerated = true;
        break;
    case QSGRendererInterface::Direct3D12:
        name = QStringLiteral("Direct3D 12");
        accelerated = true;
        break;
    case QSGRendererInterface::Vulkan:
        name = QStringLiteral("Vulkan");
        accelerated = true;
        break;
    case QSGRendererInterface::Metal:
        name = QStringLiteral("Metal");
        accelerated = true;
        break;
    case QSGRendererInterface::Software:
        name = QStringLiteral("CPU software fallback");
        break;
    case QSGRendererInterface::Null:
        name = QStringLiteral("Null / headless renderer");
        break;
    case QSGRendererInterface::OpenVG:
        name = QStringLiteral("OpenVG");
        break;
    case QSGRendererInterface::Unknown:
    default:
        name = QStringLiteral("Unknown renderer");
        break;
    }

    const bool wasDifferent = m_backendName != name
        || m_gpuAccelerated != accelerated
        || m_errorMessage != error
        || !m_initialized;
    m_backendName = name;
    m_gpuAccelerated = accelerated;
    m_errorMessage = error;
    m_initialized = api != QSGRendererInterface::Unknown;
    if (wasDifferent)
        emit changed();
}
