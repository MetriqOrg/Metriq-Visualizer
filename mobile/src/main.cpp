#include "cpuvisualizeritem.h"
#include "scenecontroller.h"
#include "visualizergpuitem.h"

#include <QCommandLineParser>
#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <QTimer>

using metriq::mobile::CpuVisualizerItem;
using metriq::mobile::GpuVisualizerItem;
using metriq::mobile::SceneController;

int main(int argc, char *argv[])
{
    QGuiApplication application(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("Metriq Foundation"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("metriq.org"));
    QCoreApplication::setApplicationName(QStringLiteral("Metriq Visualizer Mobile"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.1.0"));
    QQuickStyle::setStyle(QStringLiteral("Basic"));

    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addVersionOption();
    QCommandLineOption smokeOption(QStringLiteral("smoke-test"), QStringLiteral("Load QML and exit automatically."));
    parser.addOption(smokeOption);
    parser.process(application);

    qmlRegisterType<CpuVisualizerItem>("Metriq.Visualizer.Mobile", 1, 0, "CpuVisualizerFallback");
    qmlRegisterType<GpuVisualizerItem>("Metriq.Visualizer.Mobile", 1, 0, "GpuVisualizer");

    SceneController controller;
    QObject::connect(&application, &QGuiApplication::applicationStateChanged,
                     &controller, [&controller](Qt::ApplicationState state) {
        controller.setApplicationActive(state == Qt::ApplicationActive);
    });

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("visualizer"), &controller);
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed,
                     &application, [] { QCoreApplication::exit(2); }, Qt::QueuedConnection);
    engine.loadFromModule("Metriq.Visualizer.Mobile", "Main");

    if (parser.isSet(smokeOption))
        QTimer::singleShot(900, &application, &QCoreApplication::quit);
    return application.exec();
}
