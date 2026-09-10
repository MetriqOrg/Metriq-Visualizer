import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root
    property var model
    property var audio
    property bool compact: true
    signal closeRequested()
    signal openRequested()
    signal savePresetRequested()
    signal saveProjectRequested()

    color: "#0b1117"
    radius: compact ? 22 : 0
    border.color: "#25313a"
    border.width: 1

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "CONTROLS"
                color: "#eef6f8"
                font.pixelSize: 13
                font.weight: Font.Bold
                font.letterSpacing: 1.4
            }
            Item { Layout.fillWidth: true }
            ToolButton {
                visible: root.compact
                text: "×"
                font.pixelSize: 24
                Accessible.name: "Close controls"
                onClicked: root.closeRequested()
            }
        }

        TabBar {
            id: tabs
            Layout.fillWidth: true
            background: Rectangle { color: "transparent" }
            TabButton { text: "Scene" }
            TabButton { text: "Style" }
            TabButton { text: "Live" }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 14

                    Label {
                        text: "CAMERA"
                        color: "#6f8792"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                    }
                    Label { text: "Elevation  " + Math.round(root.model.elevation) + "°"; color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: -90
                        to: 90
                        value: root.model.elevation
                        onMoved: root.model.elevation = value
                        Accessible.name: "Camera elevation"
                    }
                    Label { text: "Azimuth  " + Math.round(root.model.azimuth) + "°"; color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: -180
                        to: 180
                        value: root.model.azimuth
                        onMoved: root.model.azimuth = value
                        Accessible.name: "Camera azimuth"
                    }
                    Label { text: "Zoom  " + root.model.zoom.toFixed(2) + "×"; color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: 0.2
                        to: 5.0
                        value: root.model.zoom
                        onMoved: root.model.zoom = value
                        Accessible.name: "Camera zoom"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Button { text: "Reset camera"; onClicked: root.model.resetCamera() }
                        Button { text: "New demo"; onClicked: root.model.generateDemo(3500) }
                    }

                    Label {
                        text: "TIMELINE"
                        color: "#6f8792"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Trail fade", "Cumulative reveal", "Full static"]
                        currentIndex: Math.max(0, model.indexOf(root.model.historyMode))
                        onActivated: root.model.historyMode = currentText
                        Accessible.name: "History mode"
                    }
                    Label { text: "Trail lifespan  " + root.model.pointLifespan.toFixed(2) + " s"; color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: 0.05
                        to: 30.0
                        value: root.model.pointLifespan
                        onMoved: root.model.pointLifespan = value
                        Accessible.name: "Trail lifespan"
                    }
                    CheckBox {
                        text: "Autorotate"
                        checked: root.model.autorotate
                        onToggled: root.model.autorotate = checked
                    }
                    CheckBox {
                        text: "Show coordinate field"
                        checked: root.model.showAxes
                        onToggled: root.model.showAxes = checked
                    }
                }
            }

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 14

                    Label {
                        text: "RENDERING"
                        color: "#6f8792"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        model: ["Points only", "Points + line", "Tube"]
                        currentIndex: Math.max(0, model.indexOf(root.model.renderMode))
                        onActivated: root.model.renderMode = currentText
                        Accessible.name: "Render mode"
                    }
                    Label { text: "Point scale  " + root.model.pointSizeScale.toFixed(2); color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: 0.0
                        to: 3.0
                        value: root.model.pointSizeScale
                        onMoved: root.model.pointSizeScale = value
                        Accessible.name: "Point size scale"
                    }
                    Label { text: "Line width  " + root.model.lineWidth.toFixed(2); color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: 0.1
                        to: 8.0
                        value: root.model.lineWidth
                        onMoved: root.model.lineWidth = value
                        Accessible.name: "Line width"
                    }
                    Label { text: "Opacity  " + Math.round(root.model.baseAlpha * 100) + "%"; color: "#dfeaec" }
                    Slider {
                        Layout.fillWidth: true
                        from: 0.0
                        to: 1.0
                        value: root.model.baseAlpha
                        onMoved: root.model.baseAlpha = value
                        Accessible.name: "Scene opacity"
                    }
                    CheckBox {
                        text: "Connect path"
                        checked: root.model.connectLines
                        onToggled: root.model.connectLines = checked
                    }
                    CheckBox {
                        text: "Show playback head"
                        checked: root.model.showHeadMarker
                        onToggled: root.model.showHeadMarker = checked
                    }

                    Label {
                        text: "DOCUMENTS"
                        color: "#6f8792"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                    }
                    Button { Layout.fillWidth: true; text: "Open preset, project, or mapped data"; onClicked: root.openRequested() }
                    RowLayout {
                        Layout.fillWidth: true
                        Button { Layout.fillWidth: true; text: "Save preset"; onClicked: root.savePresetRequested() }
                        Button { Layout.fillWidth: true; text: "Save project"; onClicked: root.saveProjectRequested() }
                    }
                }
            }

            ScrollView {
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 12

                    Label {
                        text: "MICROPHONE ANALYSIS"
                        color: "#6f8792"
                        font.pixelSize: 10
                        font.weight: Font.DemiBold
                        font.letterSpacing: 1.2
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: root.audio.active
                              ? "Capture is active. PCM buffering and FFT analysis run separately from GPU rendering."
                              : "Live input stays local. Permission is requested only when capture starts."
                        color: "#b7c8ce"
                    }
                    Button {
                        Layout.fillWidth: true
                        text: root.audio.active ? "Stop live input" : "Start live input"
                        enabled: root.audio.active || root.audio.inputAvailable
                        onClicked: root.audio.active ? root.audio.stop() : root.audio.start()
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        MetricChip { label: "RMS"; value: root.audio.rms.toFixed(3); emphasized: root.audio.active }
                        MetricChip { label: "Pitch"; value: Math.round(root.audio.fundamentalHz) + " Hz" }
                        MetricChip { label: "Centroid"; value: Math.round(root.audio.centroidHz) + " Hz" }
                        MetricChip { label: "Flux"; value: root.audio.spectralFlux.toFixed(3) }
                    }
                    Label {
                        visible: root.audio.errorMessage.length > 0
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: root.audio.errorMessage
                        color: "#ff9c9c"
                    }
                    Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "This vertical slice implements bounded native live features. Full desktop file-analysis parity remains a shared-core milestone, not a removed capability."
                        color: "#718791"
                        font.pixelSize: 11
                    }
                }
            }
        }
    }
}
