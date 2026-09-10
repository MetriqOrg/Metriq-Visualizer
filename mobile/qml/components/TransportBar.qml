import QtQuick
import QtQuick.Controls

GlassPanel {
    id: root
    property var controller
    signal openInspector()
    signal openFiles()

    implicitHeight: 76
    panelRadius: 22

    Row {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 10

        RoundButton {
            width: 48
            height: 48
            anchors.verticalCenter: parent.verticalCenter
            text: root.controller && root.controller.playing ? "Ⅱ" : "▶"
            font.pixelSize: 18
            onClicked: if (root.controller) root.controller.playing = !root.controller.playing
            background: Rectangle {
                radius: width / 2
                color: parent.down ? "#22AFC4" : "#13C7BD"
            }
            contentItem: Text {
                text: parent.text
                color: "#071013"
                font: parent.font
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }

        Column {
            width: Math.max(80, parent.width - 188)
            anchors.verticalCenter: parent.verticalCenter
            spacing: 5
            Row {
                width: parent.width
                Text {
                    text: root.controller ? root.controller.sourceName : ""
                    width: parent.width - timeLabel.width
                    elide: Text.ElideRight
                    color: "#EDF8FC"
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Text {
                    id: timeLabel
                    text: root.controller ? Math.round(root.controller.progress * root.controller.duration) + "s" : "0s"
                    color: "#82A4B2"
                    font.pixelSize: 11
                }
            }
            Slider {
                width: parent.width
                from: 0
                to: 1
                value: root.controller ? root.controller.progress : 0
                onMoved: if (root.controller) root.controller.progress = value
                background: Rectangle {
                    x: parent.leftPadding
                    y: parent.topPadding + parent.availableHeight / 2 - height / 2
                    width: parent.availableWidth
                    height: 4
                    radius: 2
                    color: "#31424D"
                    Rectangle {
                        width: parent.width * ((parent.parent.value - parent.parent.from) / (parent.parent.to - parent.parent.from))
                        height: parent.height
                        radius: parent.radius
                        color: "#17CDBD"
                    }
                }
                handle: Rectangle {
                    x: parent.leftPadding + parent.visualPosition * (parent.availableWidth - width)
                    y: parent.topPadding + parent.availableHeight / 2 - height / 2
                    width: 16
                    height: 16
                    radius: 8
                    color: "#EAFDFC"
                }
            }
        }

        ToolButton {
            width: 46
            height: 48
            anchors.verticalCenter: parent.verticalCenter
            text: "＋"
            font.pixelSize: 22
            onClicked: root.openFiles()
            ToolTip.visible: hovered
            ToolTip.text: "Open source or preset"
        }
        ToolButton {
            width: 46
            height: 48
            anchors.verticalCenter: parent.verticalCenter
            text: "≡"
            font.pixelSize: 21
            onClicked: root.openInspector()
            ToolTip.visible: hovered
            ToolTip.text: "Scene controls"
        }
    }
}
