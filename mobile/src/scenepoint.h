#pragma once

#include <QVector3D>
#include <QVector4D>

namespace metriq::mobile {

struct ScenePoint
{
    QVector3D position;
    QVector4D color;
    float size = 1.0f;
    float time = 0.0f;
};

} // namespace metriq::mobile
