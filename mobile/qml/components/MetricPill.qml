import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property string label: ""
    property string value: ""
    implicitWidth: content.implicitWidth + 24
    implicitHeight: 34
    radius: height / 2
    color: "#C818242E"
    border.width: 1
    border.color: "#2C86A5B7"

    Row {
        id: content
        anchors.centerIn: parent
        spacing: 7
        Text {
            text: root.label.toUpperCase()
            color: "#7FA1AF"
            font.pixelSize: 10
            font.weight: Font.DemiBold
            font.letterSpacing: 1.1
        }
        Text {
            text: root.value
            color: "#EDF8FC"
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
    }
}
