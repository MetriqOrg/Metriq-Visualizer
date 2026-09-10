#include "visualizergpuitem.h"

#include "visualizergeometry.h"
#include "visualizermaterial.h"

#include <QMatrix4x4>
#include <QQuickWindow>
#include <QSGGeometry>
#include <QSGGeometryNode>
#include <QSGRendererInterface>

#include <algorithm>
#include <cstring>

namespace metriq::mobile {
namespace {

const QSGGeometry::Attribute kAttributes[] = {
    QSGGeometry::Attribute::create(0, 3, QSGGeometry::FloatType, true),
    QSGGeometry::Attribute::create(1, 3, QSGGeometry::FloatType, false),
    QSGGeometry::Attribute::create(2, 4, QSGGeometry::FloatType, false),
    QSGGeometry::Attribute::create(3, 2, QSGGeometry::FloatType, false),
    QSGGeometry::Attribute::create(4, 4, QSGGeometry::FloatType, false),
};

const QSGGeometry::AttributeSet kAttributeSet = {
    5,
    sizeof(VisualizerVertex),
    kAttributes,
};

class VisualizerNode final : public QSGGeometryNode
{
public:
    VisualizerNode()
    {
        auto *geometry = new QSGGeometry(kAttributeSet, 0);
        geometry->setDrawingMode(QSGGeometry::DrawTriangles);
        geometry->setVertexDataPattern(QSGGeometry::StaticPattern);
        setGeometry(geometry);
        setFlag(QSGNode::OwnsGeometry, true);

        auto *material = new VisualizerMaterial;
        setMaterial(material);
        setFlag(QSGNode::OwnsMaterial, true);
    }

    VisualizerMaterial *visualizerMaterial() const
    {
        return static_cast<VisualizerMaterial *>(material());
    }
};

QString backendLabel(QSGRendererInterface::GraphicsApi api)
{
    switch (api) {
    case QSGRendererInterface::Software: return QStringLiteral("Software fallback");
    case QSGRendererInterface::OpenVG: return QStringLiteral("OpenVG");
    case QSGRendererInterface::OpenGL: return QStringLiteral("OpenGL / OpenGL ES GPU");
    case QSGRendererInterface::Direct3D11: return QStringLiteral("Direct3D 11 GPU");
    case QSGRendererInterface::Vulkan: return QStringLiteral("Vulkan GPU");
    case QSGRendererInterface::Metal: return QStringLiteral("Metal GPU");
    case QSGRendererInterface::Direct3D12: return QStringLiteral("Direct3D 12 GPU");
    case QSGRendererInterface::Null: return QStringLiteral("Null renderer");
    case QSGRendererInterface::Unknown: return QStringLiteral("Automatic renderer");
    }
    return QStringLiteral("Unknown renderer");
}

} // namespace

GpuVisualizerItem::GpuVisualizerItem(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(QQuickItem::ItemHasContents, true);
    setAcceptedMouseButtons(Qt::AllButtons);
}

GpuVisualizerItem::~GpuVisualizerItem() = default;

void GpuVisualizerItem::setController(SceneController *controller)
{
    if (m_controller == controller)
        return;
    if (m_controller)
        disconnect(m_controller, nullptr, this, nullptr);
    m_controller = controller;
    connectController();
    m_geometryDirty = true;
    m_materialDirty = true;
    update();
    emit controllerChanged();
}

void GpuVisualizerItem::connectController()
{
    if (!m_controller)
        return;
    connect(m_controller, &SceneController::sceneChanged, this, [this] {
        m_geometryDirty = true;
        m_materialDirty = true;
        update();
    });
    const auto materialChanged = [this] {
        m_materialDirty = true;
        update();
    };
    connect(m_controller, &SceneController::progressChanged, this, materialChanged);
    connect(m_controller, &SceneController::cameraChanged, this, materialChanged);
    connect(m_controller, &SceneController::renderSettingsChanged, this, materialChanged);
    connect(m_controller, &SceneController::performanceChanged, this, materialChanged);
}

QSGNode *GpuVisualizerItem::updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *)
{
    auto *node = static_cast<VisualizerNode *>(oldNode);
    if (!node)
        node = new VisualizerNode;
    if (!m_controller || width() <= 0.0 || height() <= 0.0)
        return node;

    if (m_geometryDirty || m_lastSceneRevision != m_controller->sceneRevision()) {
        const QVector<VisualizerVertex> vertices = buildVisualizerVertices(m_controller->renderPoints());
        QSGGeometry *geometry = node->geometry();
        geometry->allocate(vertices.size());
        if (!vertices.isEmpty())
            std::memcpy(geometry->vertexData(), vertices.constData(), vertices.size() * sizeof(VisualizerVertex));
        node->markDirty(QSGNode::DirtyGeometry);
        m_lastSceneRevision = m_controller->sceneRevision();
        m_geometryDirty = false;
    }

    auto *material = node->visualizerMaterial();
    const float aspect = std::max(0.1f, static_cast<float>(width() / height()));
    QMatrix4x4 projection;
    projection.perspective(42.0f, aspect, 0.1f, 20.0f);
    QMatrix4x4 view;
    view.translate(0.0f, 0.0f, -3.45f / m_controller->zoom());
    view.rotate(m_controller->elevation(), 1.0f, 0.0f, 0.0f);
    view.rotate(m_controller->azimuth(), 0.0f, 1.0f, 0.0f);
    material->sceneMatrix = projection * view;
    material->viewport = QSizeF(width(), height());
    material->progress = static_cast<float>(m_controller->progress());
    material->lineWidth = m_controller->lineWidth();
    material->pointScale = m_controller->pointScale();
    material->trailLength = m_controller->trailLength();
    material->historyMode = static_cast<float>(m_controller->historyMode());
    material->showLine = m_controller->showLine() ? 1.0f : 0.0f;
    material->showPoints = m_controller->showPoints() ? 1.0f : 0.0f;
    material->showGrid = m_controller->showGrid() ? 1.0f : 0.0f;
    material->showAxes = m_controller->showAxes() ? 1.0f : 0.0f;
    material->showHead = m_controller->showHeadMarker() ? 1.0f : 0.0f;
    material->headScale = m_controller->headScale();
    material->haloScale = m_controller->haloScale();
    material->baseAlpha = m_controller->baseAlpha();
    material->fadeCurve = m_controller->fadeCurve();
    material->pointCount = std::max(1, m_controller->renderedPointCount());
    material->dirty = true;
    node->markDirty(QSGNode::DirtyMaterial);
    m_materialDirty = false;
    return node;
}

void GpuVisualizerItem::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    m_materialDirty = true;
    update();
    QQuickItem::geometryChange(newGeometry, oldGeometry);
}

void GpuVisualizerItem::itemChange(ItemChange change, const ItemChangeData &data)
{
    QQuickItem::itemChange(change, data);
    if (change == ItemSceneChange) {
        if (data.window) {
            connect(data.window, &QQuickWindow::sceneGraphInitialized,
                    this, &GpuVisualizerItem::updateBackendName,
                    Qt::QueuedConnection);
            connect(data.window, &QQuickWindow::sceneGraphInvalidated,
                    this, &GpuVisualizerItem::updateBackendName,
                    Qt::QueuedConnection);
        }
        updateBackendName();
    }
}

void GpuVisualizerItem::updateBackendName()
{
    QQuickWindow *quickWindow = window();
    const auto api = quickWindow
        ? quickWindow->rendererInterface()->graphicsApi()
        : QQuickWindow::graphicsApi();
    const QString label = backendLabel(api);
    const bool accelerated = api != QSGRendererInterface::Software
        && api != QSGRendererInterface::Null
        && api != QSGRendererInterface::Unknown;
    if (m_backendName == label && m_accelerated == accelerated)
        return;
    m_backendName = label;
    m_accelerated = accelerated;
    emit backendNameChanged();
}

} // namespace metriq::mobile
