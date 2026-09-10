#pragma once

#include "scenepoint.h"

#include <QVector>

namespace metriq::mobile {

struct VisualizerVertex
{
    float ax;
    float ay;
    float az;
    float bx;
    float by;
    float bz;
    float red;
    float green;
    float blue;
    float alpha;
    float cornerX;
    float cornerY;
    float kind;
    float time;
    float size;
    float reserved;
};

static_assert(sizeof(VisualizerVertex) == sizeof(float) * 16);

QVector<VisualizerVertex> buildVisualizerVertices(const QVector<ScenePoint> &points,
                                                  int gridDivisions = 4);

} // namespace metriq::mobile
