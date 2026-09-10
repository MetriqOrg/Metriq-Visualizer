#include "audioanalysis.h"
#include "cpuvisualizeritem.h"
#include "scenecontroller.h"
#include "visualizergeometry.h"

#include <QFile>
#include <QImage>
#include <QPainter>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>

#include <cmath>
#include <numbers>

using namespace metriq::mobile;

class MobileFoundationTests final : public QObject
{
    Q_OBJECT

private slots:
    void demoRetainsFullDataAndBoundsGpuPreview();
    void csvImportProducesFiniteNormalizedGeometry();
    void presetRoundTripPreservesUnknownFields();
    void audioAnalysisFindsSineCentroid();
    void geometryUsesPortableTrianglePrimitives();
    void cpuFallbackRendersTheSameSceneWithoutAnAcceleratedBackend();
};

void MobileFoundationTests::demoRetainsFullDataAndBoundsGpuPreview()
{
    SceneController controller;
    controller.generateDemo(20'000);
    QCOMPARE(controller.pointCount(), 20'000);
    controller.setPointBudget(1500);
    QVERIFY(controller.renderedPointCount() >= 1500);
    QVERIFY(controller.renderedPointCount() <= 1501);
    QCOMPARE(controller.renderPoints().front().time, 0.0f);
    QCOMPARE(controller.renderPoints().back().time, 1.0f);
}

void MobileFoundationTests::csvImportProducesFiniteNormalizedGeometry()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("sample.csv"));
    QFile file(path);
    QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Text));
    file.write("pitch,energy,flux,label\n");
    for (int index = 0; index < 1000; ++index) {
        file.write(QByteArray::number(220.0 + std::sin(index * 0.03) * 80.0));
        file.write(",");
        file.write(QByteArray::number(std::abs(std::sin(index * 0.07))));
        file.write(",");
        file.write(QByteArray::number(std::cos(index * 0.04)));
        file.write(",row\n");
    }
    file.close();

    SceneController controller;
    QVERIFY(controller.loadSource(QUrl::fromLocalFile(path)));
    QCOMPARE(controller.pointCount(), 1000);
    QCOMPARE(controller.sourceKind(), QStringLiteral("Table"));
    for (const ScenePoint &point : controller.renderPoints()) {
        QVERIFY(std::isfinite(point.position.x()));
        QVERIFY(std::isfinite(point.position.y()));
        QVERIFY(std::isfinite(point.position.z()));
        QVERIFY(point.time >= 0.0f && point.time <= 1.0f);
    }
}

void MobileFoundationTests::presetRoundTripPreservesUnknownFields()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString inputPath = directory.filePath(QStringLiteral("input.mvpreset"));
    const QString outputPath = directory.filePath(QStringLiteral("output.mvpreset"));
    const QByteArray input = R"JSON({
        "schema":"metriq.visualizer-preset",
        "schema_version":4,
        "name":"Compatibility",
        "future_metadata":{"owner":"creator","value":42},
        "state":{
            "mapping":{"x":"pc1","future_mapping":"preserve-me"},
            "visual":{"line_width":3.25,"render_mode":"Points + line"},
            "performance":{"mode":"Balanced","live_point_budget":4321}
        }
    })JSON";
    QFile file(inputPath);
    QVERIFY(file.open(QIODevice::WriteOnly));
    QCOMPARE(file.write(input), static_cast<qint64>(input.size()));
    file.close();

    SceneController controller;
    QVERIFY(controller.loadPreset(QUrl::fromLocalFile(inputPath)));
    QCOMPARE(controller.lineWidth(), 3.25f);
    QCOMPARE(controller.pointBudget(), 4321);
    QVERIFY(controller.savePreset(QUrl::fromLocalFile(outputPath)));

    QFile output(outputPath);
    QVERIFY(output.open(QIODevice::ReadOnly));
    const QJsonObject document = QJsonDocument::fromJson(output.readAll()).object();
    QCOMPARE(document.value(QStringLiteral("future_metadata")).toObject().value(QStringLiteral("owner")).toString(), QStringLiteral("creator"));
    QCOMPARE(document.value(QStringLiteral("state")).toObject()
                 .value(QStringLiteral("mapping")).toObject()
                 .value(QStringLiteral("future_mapping")).toString(), QStringLiteral("preserve-me"));
}

void MobileFoundationTests::audioAnalysisFindsSineCentroid()
{
    constexpr int sampleRate = 48'000;
    constexpr int samples = 4096;
    QVector<float> wave(samples);
    for (int index = 0; index < samples; ++index)
        wave[index] = 0.7f * std::sin(2.0 * std::numbers::pi * 440.0 * index / sampleRate);
    const AudioMetrics metrics = AudioAnalysis::analyze(wave, sampleRate);
    QVERIFY(metrics.rms > 0.45f && metrics.rms < 0.55f);
    QVERIFY(std::abs(metrics.spectralCentroidHz - 440.0f) < 15.0f);
}

void MobileFoundationTests::geometryUsesPortableTrianglePrimitives()
{
    QVector<ScenePoint> points;
    points.append({QVector3D(-1, 0, 0), QVector4D(1, 0, 0, 1), 1.0f, 0.0f});
    points.append({QVector3D(1, 0, 0), QVector4D(0, 1, 1, 1), 2.0f, 1.0f});
    const QVector<VisualizerVertex> vertices = buildVisualizerVertices(points, 2);
    QVERIFY(!vertices.isEmpty());
    QCOMPARE(vertices.size() % 6, 0);
    bool foundLine = false;
    bool foundPoint = false;
    bool foundGrid = false;
    bool foundAxis = false;
    for (const auto &vertex : vertices) {
        foundLine |= vertex.kind == 0.0f;
        foundPoint |= vertex.kind == 1.0f;
        foundGrid |= vertex.kind == 2.0f;
        foundAxis |= vertex.kind == 3.0f;
    }
    QVERIFY(foundLine && foundPoint && foundGrid && foundAxis);
}

void MobileFoundationTests::cpuFallbackRendersTheSameSceneWithoutAnAcceleratedBackend()
{
    SceneController controller;
    controller.generateDemo(512);
    CpuVisualizerItem item;
    item.setController(&controller);
    item.setWidth(320);
    item.setHeight(240);

    QImage image(320, 240, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    QPainter painter(&image);
    item.paint(&painter);
    painter.end();

    int nonTransparent = 0;
    for (int y = 0; y < image.height(); y += 4) {
        const QRgb *scan = reinterpret_cast<const QRgb *>(image.constScanLine(y));
        for (int x = 0; x < image.width(); x += 4)
            nonTransparent += qAlpha(scan[x]) > 0 ? 1 : 0;
    }
    QVERIFY2(nonTransparent > 50, "CPU fallback produced an empty frame");
}

QTEST_MAIN(MobileFoundationTests)
#include "tst_mobile.moc"
