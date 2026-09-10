#pragma once

#include "scenecontroller.h"

#include <QQuickItem>

namespace metriq::mobile {

class GpuVisualizerItem final : public QQuickItem
{
    Q_OBJECT
    Q_PROPERTY(metriq::mobile::SceneController *controller READ controller WRITE setController NOTIFY controllerChanged)
    Q_PROPERTY(QString backendName READ backendName NOTIFY backendNameChanged)
    Q_PROPERTY(bool accelerated READ accelerated NOTIFY backendNameChanged)

public:
    explicit GpuVisualizerItem(QQuickItem *parent = nullptr);
    ~GpuVisualizerItem() override;

    [[nodiscard]] SceneController *controller() const noexcept { return m_controller; }
    void setController(SceneController *controller);
    [[nodiscard]] QString backendName() const { return m_backendName; }
    [[nodiscard]] bool accelerated() const noexcept { return m_accelerated; }

signals:
    void controllerChanged();
    void backendNameChanged();

protected:
    QSGNode *updatePaintNode(QSGNode *oldNode, UpdatePaintNodeData *) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;
    void itemChange(ItemChange change, const ItemChangeData &data) override;

private:
    void connectController();
    void updateBackendName();

    SceneController *m_controller = nullptr;
    QString m_backendName = QStringLiteral("initializing");
    bool m_accelerated = false;
    quint64 m_lastSceneRevision = 0;
    bool m_geometryDirty = true;
    bool m_materialDirty = true;
};

} // namespace metriq::mobile
