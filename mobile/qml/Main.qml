import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Metriq.Visualizer.Mobile 1.0
import "components"

ApplicationWindow {
    id: window
    width: 430
    height: 880
    minimumWidth: 360
    minimumHeight: 640
    visible: true
    title: "Metriq Visualizer"
    color: "#071016"

    property real lastPointerX: 0
    property real lastPointerY: 0
    property real pinchStartDistance: 0
    property real pinchStartZoom: 1

    Component.onCompleted: visualizer.setApplicationActive(Qt.application.state === Qt.ApplicationActive)
    Connections {
        target: Qt.application
        function onStateChanged() {
            visualizer.setApplicationActive(Qt.application.state === Qt.ApplicationActive)
        }
    }

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0; color: "#0A1820" }
            GradientStop { position: 0.55; color: "#071117" }
            GradientStop { position: 1; color: "#04090D" }
        }
    }

    CpuVisualizerFallback {
        id: cpuViewport
        anchors.fill: parent
        controller: visualizer
        visible: !gpuViewport.accelerated
    }

    GpuVisualizer {
        id: gpuViewport
        anchors.fill: parent
        controller: visualizer
        visible: accelerated
    }

    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.width: 1
        border.color: "#132A35"
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        preventStealing: true
        onPressed: mouse => {
            window.lastPointerX = mouse.x
            window.lastPointerY = mouse.y
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return
            visualizer.orbit(mouse.x - window.lastPointerX, mouse.y - window.lastPointerY)
            window.lastPointerX = mouse.x
            window.lastPointerY = mouse.y
        }
        onWheel: wheel => {
            visualizer.zoom = Math.max(0.25, Math.min(4.0, visualizer.zoom * Math.pow(1.0015, wheel.angleDelta.y)))
            wheel.accepted = true
        }
    }

    MultiPointTouchArea {
        anchors.fill: parent
        mouseEnabled: false
        minimumTouchPoints: 2
        maximumTouchPoints: 2
        touchPoints: [TouchPoint { id: touchOne }, TouchPoint { id: touchTwo }]
        onPressed: {
            window.pinchStartDistance = Math.hypot(touchTwo.x - touchOne.x, touchTwo.y - touchOne.y)
            window.pinchStartZoom = visualizer.zoom
        }
        onUpdated: {
            const distance = Math.hypot(touchTwo.x - touchOne.x, touchTwo.y - touchOne.y)
            if (window.pinchStartDistance > 2)
                visualizer.zoom = Math.max(0.25, Math.min(4.0, window.pinchStartZoom * distance / window.pinchStartDistance))
        }
    }


    Column {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: 18
        anchors.rightMargin: 18
        anchors.topMargin: 22
        spacing: 10

        Row {
            width: parent.width
            spacing: 10
            Column {
                width: parent.width - backendPill.width - 10
                Text {
                    text: "METRIQ VISUALIZER"
                    color: "#EAF8FB"
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                    font.letterSpacing: 2.0
                }
                Text {
                    text: visualizer.sourceKind.toUpperCase() + "  ·  " + visualizer.pointCount.toLocaleString() + " POINTS"
                    color: "#6F96A5"
                    font.pixelSize: 10
                    font.letterSpacing: 1.1
                }
            }
            MetricPill {
                id: backendPill
                label: gpuViewport.accelerated ? "GPU" : "FALLBACK"
                value: gpuViewport.backendName.replace(" GPU", "")
            }
        }

        Row {
            spacing: 7
            MetricPill { label: "FPS"; value: visualizer.targetFps.toString() }
            MetricPill { label: "MODE"; value: visualizer.performanceMode }
            MetricPill {
                visible: visualizer.audio.active
                label: "RMS"
                value: Math.round(visualizer.audio.rms * 100) + "%"
            }
        }
    }

    Column {
        anchors.right: parent.right
        anchors.rightMargin: 18
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10

        RoundButton {
            width: 46; height: 46
            text: "⌂"
            onClicked: visualizer.resetCamera()
            ToolTip.visible: hovered
            ToolTip.text: "Reset camera"
        }
        RoundButton {
            width: 46; height: 46
            checkable: true
            checked: visualizer.audio.active
            text: checked ? "■" : "●"
            onClicked: visualizer.toggleMicrophone()
            background: Rectangle {
                radius: width / 2
                color: parent.checked ? "#D9435F" : "#BD15232C"
                border.width: 1
                border.color: parent.checked ? "#FF7A90" : "#3E687A"
            }
            ToolTip.visible: hovered
            ToolTip.text: checked ? "Stop microphone" : "Visualize microphone"
        }
    }

    GlassPanel {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: transport.top
        anchors.leftMargin: 18
        anchors.rightMargin: 18
        anchors.bottomMargin: 10
        height: statusText.implicitHeight + 20
        visible: visualizer.status.length > 0
        panelRadius: 13
        panelColor: "#B40B151C"
        Text {
            id: statusText
            anchors.fill: parent
            anchors.margins: 10
            text: visualizer.status
            color: "#9AB9C5"
            font.pixelSize: 11
            wrapMode: Text.Wrap
            verticalAlignment: Text.AlignVCenter
        }
    }

    TransportBar {
        id: transport
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        anchors.bottomMargin: 16
        controller: visualizer
        onOpenInspector: inspector.open()
        onOpenFiles: sourceDialog.open()
    }

    InspectorSheet {
        id: inspector
        controller: visualizer
    }

    FileDialog {
        id: sourceDialog
        title: "Open data, preset, or project"
        nameFilters: [
            "Metriq files (*.mvpreset *.mvproj *.bgl)",
            "Tables (*.csv *.tsv *.txt)",
            "All files (*)"
        ]
        onAccepted: visualizer.loadSource(selectedFile)
    }

    FileDialog {
        id: savePresetDialog
        title: "Save preset"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Metriq preset (*.mvpreset)"]
        onAccepted: visualizer.savePreset(selectedFile)
    }

    Timer {
        interval: Math.max(8, Math.round(1000 / visualizer.targetFps))
        repeat: true
        running: visualizer.playing && Qt.application.state === Qt.ApplicationActive
        onTriggered: visualizer.advance(interval / 1000.0)
    }
}
