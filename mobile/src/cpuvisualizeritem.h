#pragma once

#include "scenecontroller.h"

#include <QQuickPaintedItem>

namespace metriq::mobile {

class CpuVisualizerItem final : public QQuickPaintedItem
{
    Q_OBJECT
    Q_PROPERTY(metriq::mobile::SceneController *controller READ controller WRITE setController NOTIFY controllerChanged)

public:
    explicit CpuVisualizerItem(QQuickItem *parent = nullptr);

    [[nodiscard]] SceneController *controller() const noexcept { return m_controller; }
    void setController(SceneController *controller);

    void paint(QPainter *painter) override;

signals:
    void controllerChanged();

private:
    void connectController();

    SceneController *m_controller = nullptr;
};

} // namespace metriq::mobile
