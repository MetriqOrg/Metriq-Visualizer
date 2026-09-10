// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QPointF>
#include <QSizeF>
#include <QString>
#include <QVector3D>
#include <QtMath>

#include <algorithm>
#include <cmath>

namespace MetriqProjection {

inline QVector3D rotate(const QVector3D &point, float elevationDegrees, float azimuthDegrees)
{
    const float elevation = qDegreesToRadians(90.0F - elevationDegrees);
    const float azimuth = qDegreesToRadians(azimuthDegrees);
    const float ca = std::cos(azimuth);
    const float sa = std::sin(azimuth);
    const float ce = std::cos(elevation);
    const float se = std::sin(elevation);
    return {
        point.x() * ca + point.y() * sa,
        point.x() * (-sa * ce) + point.y() * (ca * ce) + point.z() * se,
        point.x() * (sa * se) + point.y() * (-ca * se) + point.z() * ce,
    };
}

inline QPointF project(const QVector3D &point, const QSizeF &viewport,
                       float elevationDegrees, float azimuthDegrees, float zoom,
                       float *depthOut = nullptr)
{
    const QVector3D transformed = rotate(point, elevationDegrees, azimuthDegrees);
    const float depth = transformed.z();
    const float perspective = 1.0F / std::clamp(2.75F - depth * 0.38F, 1.45F, 4.0F);
    const float scale = static_cast<float>(std::min(viewport.width(), viewport.height()))
        * 0.82F * std::clamp(zoom, 0.2F, 5.0F);
    if (depthOut)
        *depthOut = depth;
    return {
        viewport.width() * 0.5 + transformed.x() * perspective * scale,
        viewport.height() * 0.51 - transformed.y() * perspective * scale,
    };
}

inline float temporalAlpha(float pointTime, float currentTime, float lifespan,
                           const QString &historyMode)
{
    const QString mode = historyMode.toCaseFolded();
    if (mode.contains(QStringLiteral("full")))
        return 1.0F;
    if (mode.contains(QStringLiteral("cumulative")))
        return pointTime <= currentTime ? 1.0F : 0.0F;
    const float age = currentTime - pointTime;
    if (age < 0.0F || age > lifespan)
        return 0.0F;
    const float progress = 1.0F - age / std::max(lifespan, 0.001F);
    return std::pow(std::clamp(progress, 0.0F, 1.0F), 0.4F);
}

} // namespace MetriqProjection
