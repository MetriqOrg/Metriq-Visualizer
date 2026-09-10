// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QPointer>
#include <QQuickItem>

#include <atomic>

class VisualizerModel;

class GpuVisualizerItem final : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(VisualizerModel *model READ model WRITE setModel NOTIFY modelChanged)

public:
    explicit GpuVisualizerItem(QQuickItem *parent = nullptr);
    ~GpuVisualizerItem() override;

    [[nodiscard]] VisualizerModel *model() const;
    void setModel(VisualizerModel *model);

signals:
    void modelChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *data) override;
    void releaseResources() override;

private:
    void invalidateGeometry();
    void invalidateMaterial();

    QPointer<VisualizerModel> m_model;
    std::atomic_bool m_geometryDirty{true};
    std::atomic_bool m_materialDirty{true};
};
