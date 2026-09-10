// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "AudioFeatureEngine.h"
#include "CpuVisualizerItem.h"
#include "RenderBackendInfo.h"
#include "StateCodec.h"
#include "VisualizerItem.h"
#include "VisualizerModel.h"

#include <QCoreApplication>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QSGRendererInterface>
#include <QSurfaceFormat>
#include <QTimer>

int main(int argc, char *argv[])
{
    QCoreApplication::setOrganizationName(QStringLiteral("Metriq Foundation"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("metriq.org"));
    QCoreApplication::setApplicationName(QStringLiteral("Metriq Visualizer"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.1.0-mobile-foundation"));

    QSurfaceFormat format = QSurfaceFormat::defaultFormat();
    format.setSamples(4);
    QSurfaceFormat::setDefaultFormat(format);

    QGuiApplication application(argc, argv);
    QQuickStyle::setStyle(QStringLiteral("Basic"));

    if (qEnvironmentVariableIntValue("METRIQ_FORCE_SOFTWARE") == 1)
        QQuickWindow::setGraphicsApi(QSGRendererInterface::Software);

    qmlRegisterType<GpuVisualizerItem>("Metriq.Visualizer", 1, 0, "GpuVisualizer");
    qmlRegisterType<CpuVisualizerItem>("Metriq.Visualizer", 1, 0, "CpuVisualizer");

    VisualizerModel model;
    StateCodec stateCodec(&model);
    AudioFeatureEngine audioEngine(&model);
    RenderBackendInfo rendererInfo;
    stateCodec.loadBundledDefault();

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("visualizerModel"), &model);
    engine.rootContext()->setContextProperty(QStringLiteral("stateCodec"), &stateCodec);
    engine.rootContext()->setContextProperty(QStringLiteral("audioEngine"), &audioEngine);
    engine.rootContext()->setContextProperty(QStringLiteral("rendererInfo"), &rendererInfo);

    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed,
                     &application, []() { QCoreApplication::exit(-1); },
                     Qt::QueuedConnection);
    engine.loadFromModule("Metriq.Mobile", "Main");
    if (engine.rootObjects().isEmpty())
        return -1;

    if (auto *window = qobject_cast<QQuickWindow *>(engine.rootObjects().constFirst()))
        rendererInfo.attach(window);

    const QStringList arguments = application.arguments();
    if (arguments.size() > 1) {
        const QUrl source = QUrl::fromUserInput(arguments.at(1));
        QTimer::singleShot(0, &stateCodec, [&stateCodec, source]() {
            stateCodec.openDocument(source);
        });
    }

    QObject::connect(&application, &QCoreApplication::aboutToQuit,
                     &audioEngine, &AudioFeatureEngine::stop);
    return application.exec();
}
