// Copyright (c) Metriq Foundation, Inc.
// SPDX-License-Identifier: MPL-2.0
#pragma once

#include <QPointer>
#include <QQuickPaintedItem>

class VisualizerModel;

class CpuVisualizerItem final : public QQuickPaintedItem
{
    Q_OBJECT
    Q_PROPERTY(VisualizerModel *model READ model WRITE setModel NOTIFY modelChanged)

public:
    explicit CpuVisualizerItem(QQuickItem *parent = nullptr);
    ~CpuVisualizerItem() override;

    [[nodiscard]] VisualizerModel *model() const;
    void setModel(VisualizerModel *model);
    void paint(QPainter *painter) override;

signals:
    void modelChanged();

private:
    QPointer<VisualizerModel> m_model;
};
