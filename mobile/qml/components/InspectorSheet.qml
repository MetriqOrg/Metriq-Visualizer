import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Drawer {
    id: root
    property var controller
    edge: Qt.BottomEdge
    modal: true
    width: parent ? parent.width : 0
    height: parent ? Math.min(parent.height * 0.78, 720) : 0
    background: Rectangle {
        color: "#F20D151C"
        radius: 26
        border.width: 1
        border.color: "#2D8AA6B8"
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: 18
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 14

            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                width: 44
                height: 4
                radius: 2
                color: "#48616E"
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "VISUALIZER CONTROLS"
                    color: "#EAF7FA"
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1.3
                }
                Item { Layout.fillWidth: true }
                Button { text: "Done"; onClicked: root.close() }
            }

            GroupBox {
                title: "Scene"
                Layout.fillWidth: true
                GridLayout {
                    columns: 2
                    width: parent.width
                    columnSpacing: 12
                    rowSpacing: 8
                    CheckBox { text: "Path"; checked: root.controller.showLine; onToggled: root.controller.showLine = checked }
                    CheckBox { text: "Points"; checked: root.controller.showPoints; onToggled: root.controller.showPoints = checked }
                    CheckBox { text: "Grid"; checked: root.controller.showGrid; onToggled: root.controller.showGrid = checked }
                    CheckBox { text: "Axes"; checked: root.controller.showAxes; onToggled: root.controller.showAxes = checked }
                    CheckBox { text: "Head marker"; checked: root.controller.showHeadMarker; onToggled: root.controller.showHeadMarker = checked }
                    CheckBox { text: "Autorotate"; checked: root.controller.autorotate; onToggled: root.controller.autorotate = checked }
                }
            }

            GroupBox {
                title: "Timeline"
                Layout.fillWidth: true
                ColumnLayout {
                    width: parent.width
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Trail fade", "Cumulative reveal", "Full static"]
                        currentIndex: root.controller.historyMode
                        onActivated: root.controller.historyMode = currentIndex
                    }
                    Label { text: "Trail length  " + root.controller.trailLength.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0.01; to: 1; value: root.controller.trailLength; onMoved: root.controller.trailLength = value }
                    Label { text: "Fade curve  " + root.controller.fadeCurve.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0.05; to: 3; value: root.controller.fadeCurve; onMoved: root.controller.fadeCurve = value }
                }
            }

            GroupBox {
                title: "Appearance"
                Layout.fillWidth: true
                ColumnLayout {
                    width: parent.width
                    Label { text: "Line width  " + root.controller.lineWidth.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0.25; to: 10; value: root.controller.lineWidth; onMoved: root.controller.lineWidth = value }
                    Label { text: "Point scale  " + root.controller.pointScale.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0; to: 2; value: root.controller.pointScale; onMoved: root.controller.pointScale = value }
                    Label { text: "Opacity  " + root.controller.baseAlpha.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0; to: 1; value: root.controller.baseAlpha; onMoved: root.controller.baseAlpha = value }
                    Label { text: "Head / halo  " + root.controller.headScale.toFixed(2) + " / " + root.controller.haloScale.toFixed(2); color: "#A6C0CA" }
                    RowLayout {
                        Layout.fillWidth: true
                        Slider { Layout.fillWidth: true; from: 0; to: 2; value: root.controller.headScale; onMoved: root.controller.headScale = value }
                        Slider { Layout.fillWidth: true; from: 0; to: 3; value: root.controller.haloScale; onMoved: root.controller.haloScale = value }
                    }
                }
            }

            GroupBox {
                title: "Performance"
                Layout.fillWidth: true
                ColumnLayout {
                    width: parent.width
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Fast preview", "Balanced", "High quality", "Full live simulation"]
                        currentIndex: root.controller.performanceMode === "Fast preview" ? 0
                                      : root.controller.performanceMode === "High quality" ? 2
                                      : root.controller.performanceMode === "Full live simulation" ? 3 : 1
                        onActivated: root.controller.performanceMode = currentText
                    }
                    Label { text: root.controller.renderedPointCount.toLocaleString() + " / " + root.controller.pointCount.toLocaleString() + " points in live viewport"; color: "#A6C0CA" }
                    Label { text: root.controller.targetFps + " FPS target · full source remains retained"; color: "#7E9EAB"; font.pixelSize: 11 }
                }
            }

            GroupBox {
                title: "Camera"
                Layout.fillWidth: true
                ColumnLayout {
                    width: parent.width
                    Label { text: "Zoom  " + root.controller.zoom.toFixed(2); color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: 0.25; to: 4; value: root.controller.zoom; onMoved: root.controller.zoom = value }
                    Label { text: "Rotation  " + root.controller.rotationSpeed.toFixed(0) + "°/s"; color: "#A6C0CA" }
                    Slider { Layout.fillWidth: true; from: -90; to: 90; value: root.controller.rotationSpeed; onMoved: root.controller.rotationSpeed = value }
                    Button { text: "Reset camera"; onClicked: root.controller.resetCamera() }
                }
            }

            Item { Layout.preferredHeight: 28 }
        }
    }
}
