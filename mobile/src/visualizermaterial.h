#pragma once

#include <QMatrix4x4>
#include <QSGMaterial>
#include <QSGMaterialShader>

namespace metriq::mobile {

class VisualizerMaterial final : public QSGMaterial
{
public:
    VisualizerMaterial();

    QSGMaterialType *type() const override;
    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode renderMode) const override;
    int compare(const QSGMaterial *other) const override;

    QMatrix4x4 sceneMatrix;
    QSizeF viewport;
    float progress = 1.0f;
    float lineWidth = 1.5f;
    float pointScale = 0.4f;
    float trailLength = 0.22f;
    float historyMode = 0.0f;
    float showLine = 1.0f;
    float showPoints = 1.0f;
    float showGrid = 1.0f;
    float showAxes = 1.0f;
    float showHead = 1.0f;
    float headScale = 0.24f;
    float haloScale = 0.45f;
    float baseAlpha = 1.0f;
    float fadeCurve = 0.4f;
    float pointCount = 1.0f;
    bool dirty = true;
};

} // namespace metriq::mobile
