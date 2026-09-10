import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Control {
    id: root
    property string label: ""
    property string value: ""
    property bool emphasized: false

    implicitHeight: 42
    implicitWidth: Math.max(116, content.implicitWidth + 24)
    padding: 10

    background: Rectangle {
        radius: 11
        color: root.emphasized ? "#133c3a" : "#101820"
        border.color: root.emphasized ? "#35d6b5" : "#26333d"
        border.width: 1
    }

    contentItem: RowLayout {
        id: content
        spacing: 8
        Label {
            text: root.label.toUpperCase()
            color: "#7f939f"
            font.pixelSize: 10
            font.weight: Font.DemiBold
            font.letterSpacing: 0.8
        }
        Item { Layout.fillWidth: true }
        Label {
            text: root.value
            color: root.emphasized ? "#a9ffea" : "#eaf3f6"
            font.pixelSize: 12
            font.family: "monospace"
            font.weight: Font.Medium
        }
    }
}
