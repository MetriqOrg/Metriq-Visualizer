// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "VisualizerItem.h"

#include "VisualizerModel.h"

#include <QMatrix4x4>
#include <QSGGeometry>
#include <QSGGeometryNode>
#include <QSGMaterial>
#include <QSGMaterialShader>
#include <QSGRendererInterface>
#include <QtMath>

#include <algorithm>
#include <array>
#include <cstring>
#include <limits>

namespace {
struct Vertex {
    float x;
    float y;
    float z;
    float r;
    float g;
    float b;
    float a;
    float size;
    float time;
};

const QSGGeometry::Attribute kAttributes[] = {
    QSGGeometry::Attribute::create(0, 3, QSGGeometry::FloatType, true),
    QSGGeometry::Attribute::create(1, 4, QSGGeometry::FloatType),
    QSGGeometry::Attribute::create(2, 1, QSGGeometry::FloatType),
    QSGGeometry::Attribute::create(3, 1, QSGGeometry::FloatType),
};
const QSGGeometry::AttributeSet kAttributeSet = {
    4,
    static_cast<int>(sizeof(Vertex)),
    kAttributes,
};

class VisualizerShader final : public QSGMaterialShader
{
public:
    VisualizerShader()
    {
        setShaderFileName(VertexStage, QStringLiteral(":/mobile/shaders/visualizer.vert.qsb"));
        setShaderFileName(FragmentStage, QStringLiteral(":/mobile/shaders/visualizer.frag.qsb"));
    }

    bool updateUniformData(RenderState &state, QSGMaterial *newMaterial,
                           QSGMaterial *oldMaterial) override;
};

class VisualizerMaterial final : public QSGMaterial
{
public:
    VisualizerMaterial()
    {
        setFlag(Blending, true);
    }

    QSGMaterialType *type() const override
    {
        static QSGMaterialType materialType;
        return &materialType;
    }

    int compare(const QSGMaterial *other) const override
    {
        if (other == this)
            return 0;
        return this < other ? -1 : 1;
    }

    QSGMaterialShader *createShader(QSGRendererInterface::RenderMode) const override
    {
        return new VisualizerShader;
    }

    void configure(const VisualizerModel::Snapshot &snapshot, const QSizeF &viewport,
                   float globalAlpha, float primitiveScale, float historyOverride = -2.0F)
    {
        const QString mode = snapshot.historyMode.toCaseFolded();
        float history = 2.0F;
        if (mode.contains(QStringLiteral("full")))
            history = 0.0F;
        else if (mode.contains(QStringLiteral("cumulative")))
            history = 1.0F;
        if (historyOverride > -1.5F)
            history = historyOverride;

        values.viewport = {
            static_cast<float>(viewport.width()),
            static_cast<float>(viewport.height()),
        };
        values.camera = {
            qDegreesToRadians(90.0F - snapshot.elevation),
            qDegreesToRadians(snapshot.azimuth),
            snapshot.zoom,
            snapshot.pointSizeScale,
        };
        values.temporal = {
            snapshot.currentTime,
            std::max(snapshot.duration, 0.001F),
            std::max(snapshot.pointLifespan, 0.001F),
            history,
        };
        values.style = {
            snapshot.baseAlpha,
            globalAlpha,
            0.4F,
            primitiveScale,
        };
        dirty = true;
    }

    struct UniformValues {
        std::array<float, 2> viewport{};
        std::array<float, 4> camera{};
        std::array<float, 4> temporal{};
        std::array<float, 4> style{};
    } values;
    bool dirty = true;
};

bool VisualizerShader::updateUniformData(RenderState &state, QSGMaterial *newMaterial,
                                         QSGMaterial *oldMaterial)
{
    QByteArray *buffer = state.uniformData();
    if (buffer->size() < 128)
        return false;

    bool changed = false;
    if (state.isMatrixDirty()) {
        const QMatrix4x4 matrix = state.combinedMatrix();
        std::memcpy(buffer->data(), matrix.constData(), 64);
        changed = true;
    }
    if (state.isOpacityDirty()) {
        const float opacity = state.opacity();
        std::memcpy(buffer->data() + 64, &opacity, sizeof(float));
        changed = true;
    }

    auto *material = static_cast<VisualizerMaterial *>(newMaterial);
    if (newMaterial != oldMaterial || material->dirty) {
        std::memcpy(buffer->data() + 72, material->values.viewport.data(), 2 * sizeof(float));
        std::memcpy(buffer->data() + 80, material->values.camera.data(), 4 * sizeof(float));
        std::memcpy(buffer->data() + 96, material->values.temporal.data(), 4 * sizeof(float));
        std::memcpy(buffer->data() + 112, material->values.style.data(), 4 * sizeof(float));
        material->dirty = false;
        changed = true;
    }
    return changed;
}

QSGGeometryNode *makeGeometryNode(QSGGeometry::DrawingMode mode)
{
    auto *node = new QSGGeometryNode;
    auto *geometry = new QSGGeometry(kAttributeSet, 0);
    geometry->setDrawingMode(mode);
    node->setGeometry(geometry);
    node->setFlag(QSGNode::OwnsGeometry, true);
    node->setMaterial(new VisualizerMaterial);
    node->setFlag(QSGNode::OwnsMaterial, true);
    return node;
}

class VisualizerSceneNode final : public QSGNode
{
public:
    VisualizerSceneNode()
    {
        grid = makeGeometryNode(QSGGeometry::DrawLines);
        trail = makeGeometryNode(QSGGeometry::DrawLineStrip);
        points = makeGeometryNode(QSGGeometry::DrawPoints);
        head = makeGeometryNode(QSGGeometry::DrawPoints);
        appendChildNode(grid);
        appendChildNode(trail);
        appendChildNode(points);
        appendChildNode(head);
    }

    QSGGeometryNode *grid = nullptr;
    QSGGeometryNode *trail = nullptr;
    QSGGeometryNode *points = nullptr;
    QSGGeometryNode *head = nullptr;
    quint64 revision = std::numeric_limits<quint64>::max();
};

Vertex makeVertex(const QVector3D &position, const QColor &color, float size, float time)
{
    return {
        position.x(), position.y(), position.z(),
        color.redF(), color.greenF(), color.blueF(), color.alphaF(),
        size, time,
    };
}

void setVertices(QSGGeometryNode *node, const QVector<Vertex> &vertices,
                 QSGGeometry::DrawingMode mode, float lineWidth = 1.0F)
{
    QSGGeometry *geometry = node->geometry();
    geometry->allocate(vertices.size());
    geometry->setDrawingMode(mode);
    geometry->setLineWidth(lineWidth);
    if (!vertices.isEmpty())
        std::memcpy(geometry->vertexData(), vertices.constData(), vertices.size() * sizeof(Vertex));
    node->markDirty(QSGNode::DirtyGeometry);
}

QVector<Vertex> gridVertices()
{
    QVector<Vertex> vertices;
    vertices.reserve(60);
    const QColor color = QColor::fromRgbF(0.24, 0.38, 0.44, 0.42);
    constexpr std::array<float, 5> positions{-1.0F, -0.5F, 0.0F, 0.5F, 1.0F};
    for (const float value : positions) {
        vertices.push_back(makeVertex({-1.0F, value, -1.0F}, color, 1.0F, 0.0F));
        vertices.push_back(makeVertex({1.0F, value, -1.0F}, color, 1.0F, 0.0F));
        vertices.push_back(makeVertex({value, -1.0F, -1.0F}, color, 1.0F, 0.0F));
        vertices.push_back(makeVertex({value, 1.0F, -1.0F}, color, 1.0F, 0.0F));
        vertices.push_back(makeVertex({-1.0F, -1.0F, value}, color, 1.0F, 0.0F));
        vertices.push_back(makeVertex({1.0F, -1.0F, value}, color, 1.0F, 0.0F));
    }
    return vertices;
}

void configureMaterial(QSGGeometryNode *node, const VisualizerModel::Snapshot &snapshot,
                       const QSizeF &viewport, float globalAlpha, float primitiveScale,
                       float historyOverride = -2.0F)
{
    auto *material = static_cast<VisualizerMaterial *>(node->material());
    material->configure(snapshot, viewport, globalAlpha, primitiveScale, historyOverride);
    node->markDirty(QSGNode::DirtyMaterial);
}
} // namespace

GpuVisualizerItem::GpuVisualizerItem(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(ItemHasContents, true);
}

GpuVisualizerItem::~GpuVisualizerItem()
{
    if (m_model)
        disconnect(m_model, nullptr, this, nullptr);
}

VisualizerModel *GpuVisualizerItem::model() const
{
    return m_model;
}

void GpuVisualizerItem::setModel(VisualizerModel *model)
{
    if (m_model == model)
        return;
    if (m_model)
        disconnect(m_model, nullptr, this, nullptr);
    m_model = model;
    if (m_model) {
        connect(m_model, &VisualizerModel::geometryChanged, this, &GpuVisualizerItem::invalidateGeometry);
        connect(m_model, &VisualizerModel::currentTimeChanged, this, &GpuVisualizerItem::invalidateMaterial);
        connect(m_model, &VisualizerModel::visualChanged, this, &GpuVisualizerItem::invalidateMaterial);
        connect(m_model, &QObject::destroyed, this, [this]() {
            m_model = nullptr;
            invalidateGeometry();
        });
    }
    invalidateGeometry();
    emit modelChanged();
}

void GpuVisualizerItem::invalidateGeometry()
{
    m_geometryDirty.store(true, std::memory_order_release);
    m_materialDirty.store(true, std::memory_order_release);
    update();
}

void GpuVisualizerItem::invalidateMaterial()
{
    m_materialDirty.store(true, std::memory_order_release);
    update();
}

QSGNode *GpuVisualizerItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    if (!m_model || width() <= 0.0 || height() <= 0.0) {
        delete oldNode;
        return nullptr;
    }

    auto *scene = static_cast<VisualizerSceneNode *>(oldNode);
    if (!scene)
        scene = new VisualizerSceneNode;

    const VisualizerModel::Snapshot snapshot = m_model->snapshot();
    const bool rebuild = m_geometryDirty.exchange(false, std::memory_order_acq_rel)
        || scene->revision != snapshot.revision;
    if (rebuild) {
        QVector<Vertex> vertices;
        vertices.reserve(snapshot.positions.size());
        for (int index = 0; index < snapshot.positions.size(); ++index) {
            const QColor color = index < snapshot.colors.size() ? snapshot.colors.at(index) : QColor(Qt::white);
            const float size = index < snapshot.sizes.size() ? snapshot.sizes.at(index) : 16.0F;
            const float time = index < snapshot.times.size() ? snapshot.times.at(index) : 0.0F;
            vertices.push_back(makeVertex(snapshot.positions.at(index), color, size, time));
        }
        setVertices(scene->trail, vertices, QSGGeometry::DrawLineStrip, snapshot.lineWidth);
        setVertices(scene->points, vertices, QSGGeometry::DrawPoints);
        setVertices(scene->grid, gridVertices(), QSGGeometry::DrawLines, 1.0F);
        scene->revision = snapshot.revision;
    }

    QVector<Vertex> headVertex;
    if (!snapshot.positions.isEmpty()) {
        auto iterator = std::upper_bound(snapshot.times.cbegin(), snapshot.times.cend(), snapshot.currentTime);
        int index = static_cast<int>(std::distance(snapshot.times.cbegin(), iterator)) - 1;
        index = std::clamp(index, 0, snapshot.positions.size() - 1);
        headVertex.push_back(makeVertex(
            snapshot.positions.at(index),
            snapshot.colors.value(index, QColor(Qt::white)),
            snapshot.sizes.value(index, 20.0F),
            snapshot.times.value(index, snapshot.currentTime)));
    }
    setVertices(scene->head, headVertex, QSGGeometry::DrawPoints);

    const QString renderMode = snapshot.renderMode.toCaseFolded();
    const bool pointsOnly = renderMode.contains(QStringLiteral("points only"));
    const bool showPoints = renderMode.contains(QStringLiteral("point"));
    const QSizeF viewport(width(), height());
    configureMaterial(scene->grid, snapshot, viewport, snapshot.showAxes ? 1.0F : 0.0F, 1.0F, -1.0F);
    configureMaterial(scene->trail, snapshot, viewport,
                      snapshot.connectLines && !pointsOnly ? 1.0F : 0.0F, 1.0F);
    configureMaterial(scene->points, snapshot, viewport, showPoints ? 1.0F : 0.0F, 1.0F);
    configureMaterial(scene->head, snapshot, viewport,
                      snapshot.showHeadMarker ? 1.0F : 0.0F, 2.8F, -1.0F);
    m_materialDirty.store(false, std::memory_order_release);
    return scene;
}

void GpuVisualizerItem::releaseResources()
{
    m_geometryDirty.store(true, std::memory_order_release);
    m_materialDirty.store(true, std::memory_order_release);
}
