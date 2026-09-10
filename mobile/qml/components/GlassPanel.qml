import QtQuick
import QtQuick.Controls

Rectangle {
    id: root
    property real panelRadius: 18
    property color panelColor: "#D9141C25"
    property color outlineColor: "#2D8AA6B8"
    radius: panelRadius
    color: panelColor
    border.width: 1
    border.color: outlineColor
}
