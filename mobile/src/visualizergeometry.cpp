#include "visualizergeometry.h"

#include <algorithm>
#include <array>

namespace metriq::mobile {
namespace {

enum PrimitiveKind : int {
    DataLine = 0,
    DataPoint = 1,
    GridLine = 2,
    AxisLine = 3,
};

void appendLineQuad(QVector<VisualizerVertex> &vertices,
                    const QVector3D &a,
                    const QVector3D &b,
                    const QVector4D &color,
                    float time,
                    float size,
                    float kind)
{
    const std::array<std::array<float, 2>, 6> corners{{
        {0.0f, -1.0f}, {1.0f, -1.0f}, {1.0f, 1.0f},
        {0.0f, -1.0f}, {1.0f, 1.0f}, {0.0f, 1.0f},
    }};
    for (const auto &corner : corners) {
        vertices.append({
            a.x(), a.y(), a.z(), b.x(), b.y(), b.z(),
            color.x(), color.y(), color.z(), color.w(),
            corner[0], corner[1], kind, time, size, 0.0f,
        });
    }
}

void appendPointQuad(QVector<VisualizerVertex> &vertices, const ScenePoint &point)
{
    const std::array<std::array<float, 2>, 6> corners{{
        {-1.0f, -1.0f}, {1.0f, -1.0f}, {1.0f, 1.0f},
        {-1.0f, -1.0f}, {1.0f, 1.0f}, {-1.0f, 1.0f},
    }};
    for (const auto &corner : corners) {
        vertices.append({
            point.position.x(), point.position.y(), point.position.z(),
            point.position.x(), point.position.y(), point.position.z(),
            point.color.x(), point.color.y(), point.color.z(), point.color.w(),
            corner[0], corner[1], static_cast<float>(DataPoint), point.time, point.size, 0.0f,
        });
    }
}

} // namespace

QVector<VisualizerVertex> buildVisualizerVertices(const QVector<ScenePoint> &points,
                                                  int gridDivisions)
{
    gridDivisions = std::clamp(gridDivisions, 2, 12);
    const int dataLines = std::max(0, points.size() - 1);
    const int gridLines = (gridDivisions + 1) * 6;
    QVector<VisualizerVertex> vertices;
    vertices.reserve((dataLines + points.size() + gridLines + 3) * 6);

    // Painter order is deliberate because this portable scene-graph material
    // does not require a platform-specific depth attachment. Coordinate guides
    // are background geometry; mapped data remains visually dominant.
    const QVector4D gridColor(0.22f, 0.32f, 0.39f, 0.22f);
    const QVector4D axisX(0.18f, 0.78f, 0.92f, 0.72f);
    const QVector4D axisY(0.16f, 0.88f, 0.58f, 0.72f);
    const QVector4D axisZ(0.80f, 0.42f, 0.94f, 0.72f);
    for (int division = 0; division <= gridDivisions; ++division) {
        const float value = -1.0f + 2.0f * division / gridDivisions;
        appendLineQuad(vertices, QVector3D(-1.0f, value, -1.0f), QVector3D(1.0f, value, -1.0f), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
        appendLineQuad(vertices, QVector3D(value, -1.0f, -1.0f), QVector3D(value, 1.0f, -1.0f), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
        appendLineQuad(vertices, QVector3D(-1.0f, -1.0f, value), QVector3D(1.0f, -1.0f, value), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
        appendLineQuad(vertices, QVector3D(value, -1.0f, -1.0f), QVector3D(value, -1.0f, 1.0f), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
        appendLineQuad(vertices, QVector3D(-1.0f, -1.0f, value), QVector3D(-1.0f, 1.0f, value), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
        appendLineQuad(vertices, QVector3D(-1.0f, value, -1.0f), QVector3D(-1.0f, value, 1.0f), gridColor, 0.0f, 0.55f, static_cast<float>(GridLine));
    }

    appendLineQuad(vertices, QVector3D(-1.0f, -1.0f, -1.0f), QVector3D(1.0f, -1.0f, -1.0f), axisX, 0.0f, 1.2f, static_cast<float>(AxisLine));
    appendLineQuad(vertices, QVector3D(-1.0f, -1.0f, -1.0f), QVector3D(-1.0f, 1.0f, -1.0f), axisY, 0.0f, 1.2f, static_cast<float>(AxisLine));
    appendLineQuad(vertices, QVector3D(-1.0f, -1.0f, -1.0f), QVector3D(-1.0f, -1.0f, 1.0f), axisZ, 0.0f, 1.2f, static_cast<float>(AxisLine));

    for (int index = 1; index < points.size(); ++index) {
        const ScenePoint &before = points.at(index - 1);
        const ScenePoint &after = points.at(index);
        const QVector4D color = (before.color + after.color) * 0.5f;
        appendLineQuad(vertices, before.position, after.position, color,
                       std::max(before.time, after.time), 1.0f, static_cast<float>(DataLine));
    }
    for (const ScenePoint &point : points)
        appendPointQuad(vertices, point);
    return vertices;
}

} // namespace metriq::mobile
