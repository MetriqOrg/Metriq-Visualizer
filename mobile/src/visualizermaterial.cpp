#include "visualizermaterial.h"

#include <QByteArray>
#include <QtGlobal>

#include <cstring>

namespace metriq::mobile {
namespace {

constexpr int kUniformBufferSize = 208;

void copyFloat(QByteArray *buffer, int offset, float value)
{
    std::memcpy(buffer->data() + offset, &value, sizeof(value));
}

class VisualizerShader final : public QSGMaterialShader
{
public:
    VisualizerShader()
    {
        setShaderFileName(VertexStage, QStringLiteral(":/metriq/visualizer/mobile/shaders/visualizer.vert.qsb"));
        setShaderFileName(FragmentStage, QStringLiteral(":/metriq/visualizer/mobile/shaders/visualizer.frag.qsb"));
    }

    bool updateUniformData(RenderState &state,
                           QSGMaterial *newMaterial,
                           QSGMaterial *oldMaterial) override
    {
        QByteArray *buffer = state.uniformData();
        Q_ASSERT(buffer->size() >= kUniformBufferSize);
        bool changed = false;
        if (state.isMatrixDirty()) {
            const QMatrix4x4 matrix = state.combinedMatrix();
            std::memcpy(buffer->data(), matrix.constData(), 64);
            changed = true;
        }
        if (state.isOpacityDirty()) {
            copyFloat(buffer, 64, state.opacity());
            changed = true;
        }

        auto *material = static_cast<VisualizerMaterial *>(newMaterial);
        if (newMaterial != oldMaterial || material->dirty) {
            copyFloat(buffer, 68, material->progress);
            copyFloat(buffer, 72, material->lineWidth);
            copyFloat(buffer, 76, material->pointScale);
            std::memcpy(buffer->data() + 80, material->sceneMatrix.constData(), 64);
            const float viewport[2] = {
                static_cast<float>(material->viewport.width()),
                static_cast<float>(material->viewport.height()),
            };
            std::memcpy(buffer->data() + 144, viewport, sizeof(viewport));
            copyFloat(buffer, 152, material->trailLength);
            copyFloat(buffer, 156, material->historyMode);
            copyFloat(buffer, 160, material->showLine);
            copyFloat(buffer, 164, material->showPoints);
            copyFloat(buffer, 168, material->showGrid);
            copyFloat(buffer, 172, material->showAxes);
            copyFloat(buffer, 176, material->showHead);
            copyFloat(buffer, 180, material->headScale);
            copyFloat(buffer, 184, material->haloScale);
            copyFloat(buffer, 188, material->baseAlpha);
            copyFloat(buffer, 192, material->fadeCurve);
            copyFloat(buffer, 196, material->pointCount);
            copyFloat(buffer, 200, 0.0f);
            copyFloat(buffer, 204, 0.0f);
            material->dirty = false;
            changed = true;
        }
        return changed;
    }
};

} // namespace

VisualizerMaterial::VisualizerMaterial()
{
    setFlag(QSGMaterial::Blending, true);
}

QSGMaterialType *VisualizerMaterial::type() const
{
    static QSGMaterialType materialType;
    return &materialType;
}

QSGMaterialShader *VisualizerMaterial::createShader(QSGRendererInterface::RenderMode) const
{
    return new VisualizerShader;
}

int VisualizerMaterial::compare(const QSGMaterial *other) const
{
    return this == other ? 0 : 1;
}

} // namespace metriq::mobile
