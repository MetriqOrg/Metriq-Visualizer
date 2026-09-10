#include "cpuvisualizeritem.h"

#include <QColor>
#include <QMatrix4x4>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QVector4D>

#include <algorithm>
#include <cmath>
#include <limits>

namespace metriq::mobile {
namespace {

struct ProjectedPoint
{
    QPointF position;
    float depth = 0.0f;
    bool visible = false;
};

QMatrix4x4 sceneMatrix(const SceneController &controller, qreal width, qreal height)
{
    QMatrix4x4 projection;
    const float aspect = std::max(0.1f, static_cast<float>(width / std::max<qreal>(height, 1.0)));
    projection.perspective(42.0f, aspect, 0.1f, 20.0f);
    QMatrix4x4 view;
    view.translate(0.0f, 0.0f, -3.45f / controller.zoom());
    view.rotate(controller.elevation(), 1.0f, 0.0f, 0.0f);
    view.rotate(controller.azimuth(), 0.0f, 1.0f, 0.0f);
    return projection * view;
}

ProjectedPoint projectPoint(const QMatrix4x4 &matrix,
                            const QVector3D &point,
                            qreal width,
                            qreal height)
{
    const QVector4D clip = matrix * QVector4D(point, 1.0f);
    if (!std::isfinite(clip.w()) || clip.w() <= 0.00001f)
        return {};
    const float inverseW = 1.0f / clip.w();
    const float x = clip.x() * inverseW;
    const float y = clip.y() * inverseW;
    const float z = clip.z() * inverseW;
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z))
        return {};
    return {
        QPointF((x * 0.5f + 0.5f) * width,
                (0.5f - y * 0.5f) * height),
        z,
        z >= -1.2f && z <= 1.2f,
    };
}

float temporalAlpha(const SceneController &controller, float timestamp)
{
    if (controller.historyMode() == 2)
        return 1.0f;
    if (controller.historyMode() == 1)
        return timestamp <= controller.progress() ? 1.0f : 0.0f;
    const float age = static_cast<float>(controller.progress()) - timestamp;
    const float trail = std::max(0.0001f, controller.trailLength());
    if (age < 0.0f || age > trail)
        return 0.0f;
    const float remaining = std::clamp(1.0f - age / trail, 0.0f, 1.0f);
    return std::pow(remaining, std::max(0.05f, controller.fadeCurve()));
}

QColor withAlpha(const QVector4D &rgba, float alpha)
{
    QColor color = QColor::fromRgbF(
        std::clamp(rgba.x(), 0.0f, 1.0f),
        std::clamp(rgba.y(), 0.0f, 1.0f),
        std::clamp(rgba.z(), 0.0f, 1.0f),
        std::clamp(rgba.w() * alpha, 0.0f, 1.0f));
    return color;
}

void drawSegment(QPainter *painter,
                 const ProjectedPoint &a,
                 const ProjectedPoint &b,
                 const QColor &color,
                 qreal width)
{
    if (!a.visible || !b.visible || color.alpha() == 0)
        return;
    QPen pen(color, width, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
    painter->setPen(pen);
    painter->drawLine(a.position, b.position);
}

} // namespace

CpuVisualizerItem::CpuVisualizerItem(QQuickItem *parent)
    : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
    setOpaquePainting(false);
    setMipmap(false);
}

void CpuVisualizerItem::setController(SceneController *controller)
{
    if (m_controller == controller)
        return;
    if (m_controller)
        disconnect(m_controller, nullptr, this, nullptr);
    m_controller = controller;
    connectController();
    update();
    emit controllerChanged();
}

void CpuVisualizerItem::connectController()
{
    if (!m_controller)
        return;
    const auto repaint = [this] { update(); };
    connect(m_controller, &SceneController::sceneChanged, this, repaint);
    connect(m_controller, &SceneController::progressChanged, this, repaint);
    connect(m_controller, &SceneController::cameraChanged, this, repaint);
    connect(m_controller, &SceneController::renderSettingsChanged, this, repaint);
    connect(m_controller, &SceneController::performanceChanged, this, repaint);
}

void CpuVisualizerItem::paint(QPainter *painter)
{
    if (!m_controller || width() <= 0.0 || height() <= 0.0)
        return;

    painter->save();
    painter->setRenderHint(QPainter::Antialiasing, true);
    painter->setRenderHint(QPainter::TextAntialiasing, true);
    painter->setCompositionMode(QPainter::CompositionMode_SourceOver);

    const QMatrix4x4 matrix = sceneMatrix(*m_controller, width(), height());

    if (m_controller->showGrid()) {
        const QColor grid(52, 78, 92, 58);
        constexpr int divisions = 4;
        for (int division = 0; division <= divisions; ++division) {
            const float value = -1.0f + 2.0f * division / divisions;
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(-1.0f, value, -1.0f), width(), height()),
                        projectPoint(matrix, QVector3D(1.0f, value, -1.0f), width(), height()),
                        grid, 0.65);
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(value, -1.0f, -1.0f), width(), height()),
                        projectPoint(matrix, QVector3D(value, 1.0f, -1.0f), width(), height()),
                        grid, 0.65);
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(-1.0f, -1.0f, value), width(), height()),
                        projectPoint(matrix, QVector3D(1.0f, -1.0f, value), width(), height()),
                        grid, 0.65);
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(value, -1.0f, -1.0f), width(), height()),
                        projectPoint(matrix, QVector3D(value, -1.0f, 1.0f), width(), height()),
                        grid, 0.65);
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(-1.0f, -1.0f, value), width(), height()),
                        projectPoint(matrix, QVector3D(-1.0f, 1.0f, value), width(), height()),
                        grid, 0.65);
            drawSegment(painter,
                        projectPoint(matrix, QVector3D(-1.0f, value, -1.0f), width(), height()),
                        projectPoint(matrix, QVector3D(-1.0f, value, 1.0f), width(), height()),
                        grid, 0.65);
        }
    }

    if (m_controller->showAxes()) {
        drawSegment(painter,
                    projectPoint(matrix, QVector3D(-1, -1, -1), width(), height()),
                    projectPoint(matrix, QVector3D(1, -1, -1), width(), height()),
                    QColor(46, 199, 235, 184), 1.2);
        drawSegment(painter,
                    projectPoint(matrix, QVector3D(-1, -1, -1), width(), height()),
                    projectPoint(matrix, QVector3D(-1, 1, -1), width(), height()),
                    QColor(41, 224, 148, 184), 1.2);
        drawSegment(painter,
                    projectPoint(matrix, QVector3D(-1, -1, -1), width(), height()),
                    projectPoint(matrix, QVector3D(-1, -1, 1), width(), height()),
                    QColor(204, 107, 240, 184), 1.2);
    }

    const QVector<ScenePoint> &points = m_controller->renderPoints();
    QVector<ProjectedPoint> projected;
    projected.reserve(points.size());
    for (const ScenePoint &point : points)
        projected.append(projectPoint(matrix, point.position, width(), height()));

    if (m_controller->showLine()) {
        for (int index = 1; index < points.size(); ++index) {
            const float alpha = std::min(temporalAlpha(*m_controller, points.at(index - 1).time),
                                         temporalAlpha(*m_controller, points.at(index).time));
            const QVector4D average = (points.at(index - 1).color + points.at(index).color) * 0.5f;
            drawSegment(painter, projected.at(index - 1), projected.at(index),
                        withAlpha(average, alpha * m_controller->baseAlpha()),
                        std::clamp<qreal>(m_controller->lineWidth(), 0.25, 16.0));
        }
    }

    int headIndex = -1;
    float headDistance = std::numeric_limits<float>::max();
    if (m_controller->showPoints() || m_controller->showHeadMarker()) {
        for (int index = 0; index < points.size(); ++index) {
            const float alpha = temporalAlpha(*m_controller, points.at(index).time);
            const ProjectedPoint &screen = projected.at(index);
            if (m_controller->showPoints() && screen.visible && alpha > 0.001f) {
                const qreal radius = std::clamp<qreal>(
                    1.1 + m_controller->pointScale() * points.at(index).size * 3.8,
                    0.75, 10.0);
                painter->setPen(Qt::NoPen);
                painter->setBrush(withAlpha(points.at(index).color,
                                            alpha * m_controller->baseAlpha()));
                painter->drawEllipse(screen.position, radius, radius);
            }
            const float distance = std::abs(points.at(index).time - static_cast<float>(m_controller->progress()));
            if (distance < headDistance) {
                headDistance = distance;
                headIndex = index;
            }
        }
    }

    if (m_controller->showHeadMarker() && headIndex >= 0 && projected.at(headIndex).visible) {
        const QPointF center = projected.at(headIndex).position;
        const QColor source = withAlpha(points.at(headIndex).color, m_controller->baseAlpha());
        const qreal haloRadius = std::clamp<qreal>(4.0 + m_controller->haloScale() * 8.0, 4.0, 44.0);
        QColor halo = source;
        halo.setAlphaF(std::clamp(0.13 * m_controller->baseAlpha(), 0.0, 1.0));
        painter->setPen(Qt::NoPen);
        painter->setBrush(halo);
        painter->drawEllipse(center, haloRadius, haloRadius);
        const qreal headRadius = std::clamp<qreal>(2.0 + m_controller->headScale() * 5.0, 2.0, 20.0);
        painter->setBrush(source);
        painter->drawEllipse(center, headRadius, headRadius);
    }

    painter->restore();
}

} // namespace metriq::mobile
