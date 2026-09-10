// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#include "CpuVisualizerItem.h"

#include "ProjectionMath.h"
#include "VisualizerModel.h"

#include <QPainter>
#include <QPainterPath>
#include <QPen>

#include <algorithm>
#include <cmath>

CpuVisualizerItem::CpuVisualizerItem(QQuickItem *parent)
    : QQuickPaintedItem(parent)
{
    setAntialiasing(true);
    setOpaquePainting(true);
    setRenderTarget(QQuickPaintedItem::Image);
}

CpuVisualizerItem::~CpuVisualizerItem()
{
    if (m_model)
        disconnect(m_model, nullptr, this, nullptr);
}

VisualizerModel *CpuVisualizerItem::model() const
{
    return m_model;
}

void CpuVisualizerItem::setModel(VisualizerModel *model)
{
    if (m_model == model)
        return;
    if (m_model)
        disconnect(m_model, nullptr, this, nullptr);
    m_model = model;
    if (m_model) {
        connect(m_model, &VisualizerModel::geometryChanged, this, qOverload<>(&CpuVisualizerItem::update));
        connect(m_model, &VisualizerModel::currentTimeChanged, this, qOverload<>(&CpuVisualizerItem::update));
        connect(m_model, &VisualizerModel::visualChanged, this, qOverload<>(&CpuVisualizerItem::update));
        connect(m_model, &QObject::destroyed, this, [this]() {
            m_model = nullptr;
            update();
        });
    }
    update();
    emit modelChanged();
}

void CpuVisualizerItem::paint(QPainter *painter)
{
    painter->setRenderHint(QPainter::Antialiasing, true);
    painter->fillRect(boundingRect(), QColor(QStringLiteral("#05090d")));
    if (!m_model)
        return;

    const VisualizerModel::Snapshot snapshot = m_model->snapshot();
    const QSizeF viewport(width(), height());

    if (snapshot.showAxes) {
        QPen gridPen(QColor(68, 101, 113, 92));
        gridPen.setWidthF(0.8);
        painter->setPen(gridPen);
        constexpr float positions[] = {-1.0F, -0.5F, 0.0F, 0.5F, 1.0F};
        for (const float value : positions) {
            painter->drawLine(
                MetriqProjection::project({-1.0F, value, -1.0F}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom),
                MetriqProjection::project({1.0F, value, -1.0F}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom));
            painter->drawLine(
                MetriqProjection::project({value, -1.0F, -1.0F}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom),
                MetriqProjection::project({value, 1.0F, -1.0F}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom));
            painter->drawLine(
                MetriqProjection::project({-1.0F, -1.0F, value}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom),
                MetriqProjection::project({1.0F, -1.0F, value}, viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom));
        }
    }

    QVector<QPointF> projected;
    QVector<float> alphas;
    projected.reserve(snapshot.positions.size());
    alphas.reserve(snapshot.positions.size());
    for (int index = 0; index < snapshot.positions.size(); ++index) {
        projected.push_back(MetriqProjection::project(
            snapshot.positions.at(index), viewport, snapshot.elevation, snapshot.azimuth, snapshot.zoom));
        alphas.push_back(MetriqProjection::temporalAlpha(
            snapshot.times.value(index, 0.0F), snapshot.currentTime,
            snapshot.pointLifespan, snapshot.historyMode));
    }

    const QString mode = snapshot.renderMode.toCaseFolded();
    const bool pointsOnly = mode.contains(QStringLiteral("points only"));
    const bool showPoints = mode.contains(QStringLiteral("point"));
    if (snapshot.connectLines && !pointsOnly && projected.size() > 1) {
        constexpr int batch = 64;
        for (int start = 0; start + 1 < projected.size(); start += batch) {
            const int end = std::min(start + batch + 1, projected.size());
            QPainterPath path;
            bool active = false;
            float alphaSum = 0.0F;
            int alphaCount = 0;
            for (int index = start; index < end; ++index) {
                const float alpha = alphas.at(index);
                if (alpha <= 0.001F) {
                    active = false;
                    continue;
                }
                if (!active)
                    path.moveTo(projected.at(index));
                else
                    path.lineTo(projected.at(index));
                active = true;
                alphaSum += alpha;
                ++alphaCount;
            }
            if (alphaCount > 1) {
                QColor color = snapshot.colors.value((start + end - 1) / 2, QColor(Qt::cyan));
                color.setAlphaF(std::clamp(
                    static_cast<double>(snapshot.baseAlpha * alphaSum / alphaCount), 0.0, 1.0));
                QPen pen(color);
                pen.setWidthF(snapshot.lineWidth);
                pen.setCapStyle(Qt::RoundCap);
                pen.setJoinStyle(Qt::RoundJoin);
                painter->setPen(pen);
                painter->setBrush(Qt::NoBrush);
                painter->drawPath(path);
            }
        }
    }

    if (showPoints) {
        painter->setPen(Qt::NoPen);
        for (int index = 0; index < projected.size(); ++index) {
            const float alpha = alphas.at(index);
            if (alpha <= 0.001F)
                continue;
            QColor color = snapshot.colors.value(index, QColor(Qt::white));
            color.setAlphaF(std::clamp(
                static_cast<double>(color.alphaF() * snapshot.baseAlpha * alpha), 0.0, 1.0));
            painter->setBrush(color);
            const float radius = std::clamp(
                std::sqrt(std::max(1.0F, snapshot.sizes.value(index, 16.0F)))
                    * 0.16F * snapshot.pointSizeScale,
                0.8F, 9.0F);
            painter->drawEllipse(projected.at(index), radius, radius);
        }
    }

    if (snapshot.showHeadMarker && !projected.isEmpty()) {
        const auto iterator = std::upper_bound(snapshot.times.cbegin(), snapshot.times.cend(), snapshot.currentTime);
        int index = static_cast<int>(std::distance(snapshot.times.cbegin(), iterator)) - 1;
        index = std::clamp(index, 0, projected.size() - 1);
        QColor color = snapshot.colors.value(index, QColor(Qt::white));
        color.setAlphaF(0.22);
        painter->setPen(Qt::NoPen);
        painter->setBrush(color);
        painter->drawEllipse(projected.at(index), 13.0, 13.0);
        color.setAlphaF(1.0);
        painter->setBrush(color);
        painter->drawEllipse(projected.at(index), 4.5, 4.5);
    }
}
