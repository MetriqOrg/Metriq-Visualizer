// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "AudioFeatureEngine.h"
#include "ProjectionMath.h"
#include "StateCodec.h"
#include "VisualizerModel.h"

#include <QFile>
#include <QJsonDocument>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>

#include <cmath>
#include <numbers>

class MobileCoreTests final : public QObject
{
    Q_OBJECT

private slots:
    void demoIsDeterministicAndRetained();
    void liveHistoryIsBoundedWithoutChangingBudget();
    void stateRoundTripPreservesUnknownSections();
    void presetAndProjectSchemasRemainCompatible();
    void mappedCsvImportsAndNormalizes();
    void projectionAndTemporalFallbackMatchContract();
    void audioWorkerFindsAResolvableTone();
};

void MobileCoreTests::demoIsDeterministicAndRetained()
{
    VisualizerModel first;
    VisualizerModel second;
    first.generateDemo(2048);
    second.generateDemo(2048);
    const auto a = first.snapshot();
    const auto b = second.snapshot();
    QCOMPARE(a.positions.size(), 2048);
    QCOMPARE(a.positions, b.positions);
    QCOMPARE(a.times, b.times);
    QCOMPARE(a.positions.constFirst(), a.positions.constFirst());
    QVERIFY(a.duration > 29.9F);
    QVERIFY(first.geometryRevision() > 0);
}

void MobileCoreTests::liveHistoryIsBoundedWithoutChangingBudget()
{
    VisualizerModel model;
    model.setLivePointBudget(300);
    model.clear();
    for (int index = 0; index < 5000; ++index) {
        model.appendLiveFrame(
            0.2 + 0.1 * std::sin(index * 0.1),
            1800.0 + index % 900,
            0.04 + 0.01 * std::cos(index * 0.07),
            220.0 + index % 300,
            index / 50.0);
    }
    QCOMPARE(model.livePointBudget(), 300);
    QCOMPARE(model.pointCount(), 1200);
    QCOMPARE(model.sourceName(), QStringLiteral("Live microphone"));
    QVERIFY(model.currentTime() > 99.0);
}

void MobileCoreTests::stateRoundTripPreservesUnknownSections()
{
    VisualizerModel model;
    QJsonObject state{
        {QStringLiteral("future_section"), QJsonObject{{QStringLiteral("retained"), 42}}},
        {QStringLiteral("mapping"), QJsonObject{{QStringLiteral("x"), QStringLiteral("pc1")}}},
        {QStringLiteral("visual"), QJsonObject{
            {QStringLiteral("elev"), 12.0},
            {QStringLiteral("azim"), -55.0},
            {QStringLiteral("zoom"), 1.75},
            {QStringLiteral("render_mode"), QStringLiteral("Points only")},
            {QStringLiteral("history_mode"), QStringLiteral("Full static")},
            {QStringLiteral("show_axes"), false},
        }},
        {QStringLiteral("performance"), QJsonObject{{QStringLiteral("live_point_budget"), 2400}}},
    };
    model.applyState(state);
    QCOMPARE(model.elevation(), 12.0);
    QCOMPARE(model.azimuth(), -55.0);
    QCOMPARE(model.zoom(), 1.75);
    QCOMPARE(model.renderMode(), QStringLiteral("Points only"));
    QCOMPARE(model.historyMode(), QStringLiteral("Full static"));
    QCOMPARE(model.livePointBudget(), 2400);
    const QJsonObject saved = model.exportState();
    QCOMPARE(saved.value(QStringLiteral("future_section")).toObject()
                 .value(QStringLiteral("retained")).toInt(), 42);
    QCOMPARE(saved.value(QStringLiteral("mapping")).toObject()
                 .value(QStringLiteral("x")).toString(), QStringLiteral("pc1"));
}

void MobileCoreTests::presetAndProjectSchemasRemainCompatible()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    VisualizerModel model;
    StateCodec codec(&model);

    const QString presetPath = directory.filePath(QStringLiteral("input.mvpreset"));
    const QJsonObject preset{
        {QStringLiteral("schema"), QStringLiteral("metriq.visualizer-preset")},
        {QStringLiteral("schema_version"), 3},
        {QStringLiteral("name"), QStringLiteral("Compatibility preset")},
        {QStringLiteral("vendor_extension"), QJsonObject{{QStringLiteral("keep"), true}}},
        {QStringLiteral("state"), QJsonObject{
            {QStringLiteral("visual"), QJsonObject{{QStringLiteral("zoom"), 2.25}}},
            {QStringLiteral("unrecognized"), QJsonObject{{QStringLiteral("value"), 7}}},
        }},
    };
    QFile presetFile(presetPath);
    QVERIFY(presetFile.open(QIODevice::WriteOnly));
    presetFile.write(QJsonDocument(preset).toJson());
    presetFile.close();
    QVERIFY(codec.loadPreset(QUrl::fromLocalFile(presetPath)));
    QCOMPARE(model.presetName(), QStringLiteral("Compatibility preset"));
    QCOMPARE(model.zoom(), 2.25);

    const QString savedPresetPath = directory.filePath(QStringLiteral("saved.mvpreset"));
    QVERIFY(codec.savePreset(QUrl::fromLocalFile(savedPresetPath)));
    QFile savedPresetFile(savedPresetPath);
    QVERIFY(savedPresetFile.open(QIODevice::ReadOnly));
    const QJsonObject savedPreset = QJsonDocument::fromJson(savedPresetFile.readAll()).object();
    QCOMPARE(savedPreset.value(QStringLiteral("schema_version")).toInt(), 4);
    QCOMPARE(savedPreset.value(QStringLiteral("vendor_extension")).toObject()
                 .value(QStringLiteral("keep")).toBool(), true);
    QCOMPARE(savedPreset.value(QStringLiteral("state")).toObject()
                 .value(QStringLiteral("unrecognized")).toObject()
                 .value(QStringLiteral("value")).toInt(), 7);

    const QString projectPath = directory.filePath(QStringLiteral("input.mvproj"));
    const QJsonObject project{
        {QStringLiteral("schema"), QStringLiteral("metriq.visualizer-project")},
        {QStringLiteral("schema_version"), 2},
        {QStringLiteral("name"), QStringLiteral("Mobile compatibility project")},
        {QStringLiteral("state"), QJsonObject{
            {QStringLiteral("visual"), QJsonObject{{QStringLiteral("elev"), 33.0}}},
            {QStringLiteral("layout"), QJsonObject{{QStringLiteral("custom"), true}}},
        }},
    };
    QFile projectFile(projectPath);
    QVERIFY(projectFile.open(QIODevice::WriteOnly));
    projectFile.write(QJsonDocument(project).toJson());
    projectFile.close();
    QVERIFY(codec.loadProject(QUrl::fromLocalFile(projectPath)));
    QCOMPARE(model.elevation(), 33.0);

    const QString savedProjectPath = directory.filePath(QStringLiteral("saved.mvproj"));
    QVERIFY(codec.saveProject(QUrl::fromLocalFile(savedProjectPath), QStringLiteral("Saved project")));
    QFile savedProjectFile(savedProjectPath);
    QVERIFY(savedProjectFile.open(QIODevice::ReadOnly));
    const QJsonObject savedProject = QJsonDocument::fromJson(savedProjectFile.readAll()).object();
    QCOMPARE(savedProject.value(QStringLiteral("schema_version")).toInt(), 2);
    QCOMPARE(savedProject.value(QStringLiteral("state")).toObject()
                 .value(QStringLiteral("layout")).toObject()
                 .value(QStringLiteral("custom")).toBool(), true);
}

void MobileCoreTests::mappedCsvImportsAndNormalizes()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const QString path = directory.filePath(QStringLiteral("mapped.csv"));
    QFile file(path);
    QVERIFY(file.open(QIODevice::WriteOnly | QIODevice::Text));
    file.write("x,y,z,r,g,b,size,time\n");
    file.write("10,20,30,255,0,0,12,0\n");
    file.write("20,40,60,0,255,0,24,1\n");
    file.write("30,60,90,0,0,255,36,2\n");
    file.close();

    VisualizerModel model;
    QVERIFY(model.loadMappedData(QUrl::fromLocalFile(path)));
    QCOMPARE(model.pointCount(), 3);
    QCOMPARE(model.duration(), 2.0);
    const auto snapshot = model.snapshot();
    QCOMPARE(snapshot.positions.constFirst(), QVector3D(-1.0F, -1.0F, -1.0F));
    QCOMPARE(snapshot.positions.constLast(), QVector3D(1.0F, 1.0F, 1.0F));
    QCOMPARE(snapshot.colors.constFirst().red(), 255);
}

void MobileCoreTests::projectionAndTemporalFallbackMatchContract()
{
    const QSizeF viewport(960.0, 600.0);
    float depth = 0.0F;
    const QPointF center = MetriqProjection::project(
        QVector3D(0.0F, 0.0F, 0.0F), viewport, 24.0F, 35.0F, 1.0F, &depth);
    QCOMPARE(center, QPointF(480.0, 306.0));
    QCOMPARE(depth, 0.0F);
    QCOMPARE(MetriqProjection::temporalAlpha(2.0F, 1.0F, 1.0F, QStringLiteral("Cumulative reveal")), 0.0F);
    QCOMPARE(MetriqProjection::temporalAlpha(0.5F, 1.0F, 1.0F, QStringLiteral("Full static")), 1.0F);
    QVERIFY(MetriqProjection::temporalAlpha(0.5F, 1.0F, 1.0F, QStringLiteral("Trail fade")) > 0.0F);
}

void MobileCoreTests::audioWorkerFindsAResolvableTone()
{
    AudioAnalyzerWorker worker;
    QSignalSpy spy(&worker, &AudioAnalyzerWorker::frameReady);
    constexpr int sampleRate = 48000;
    QVector<float> samples(1024);
    for (int index = 0; index < samples.size(); ++index) {
        samples[index] = 0.5F * std::sin(
            2.0F * std::numbers::pi_v<float> * 440.0F * index / sampleRate);
    }
    worker.analyze(samples, sampleRate, 1.25);
    QCOMPARE(spy.size(), 1);
    const AudioFeatureFrame frame = qvariant_cast<AudioFeatureFrame>(spy.constFirst().constFirst());
    QVERIFY(frame.rms > 0.3F && frame.rms < 0.4F);
    QVERIFY(frame.fundamentalHz > 400.0F && frame.fundamentalHz < 490.0F);
    QVERIFY(frame.centroidHz > 300.0F && frame.centroidHz < 800.0F);
    QCOMPARE(frame.timestampSeconds, 1.25);
}

QTEST_GUILESS_MAIN(MobileCoreTests)
#include "tst_mobile_core.moc"
