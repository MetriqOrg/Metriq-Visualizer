import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import Metriq.Visualizer 1.0
import "components"

ApplicationWindow {
    id: root
    width: 430
    height: 860
    minimumWidth: 320
    minimumHeight: 560
    visible: true
    title: "Metriq Visualizer"
    color: "#05090d"

    property bool tablet: width >= 900
    property bool inspectorOpen: false
    property string toastText: ""

    function formatTime(seconds) {
        var value = Math.max(0, Math.round(seconds))
        var minutes = Math.floor(value / 60)
        var remainder = value % 60
        return minutes + ":" + (remainder < 10 ? "0" : "") + remainder
    }

    function showToast(message) {
        toastText = message
        toastTimer.restart()
    }

    Rectangle {
        anchors.fill: parent
        color: "#05090d"
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#08131a" }
            GradientStop { position: 0.62; color: "#05090d" }
            GradientStop { position: 1.0; color: "#071014" }
        }
    }

    Item {
        id: viewportHost
        x: 0
        y: 0
        width: root.width - (root.tablet ? inspector.width : 0)
        height: root.height

        GpuVisualizer {
            id: gpuViewport
            anchors.fill: parent
            model: visualizerModel
            visible: rendererInfo.gpuAccelerated
        }

        CpuVisualizer {
            id: cpuViewport
            anchors.fill: parent
            model: visualizerModel
            visible: !rendererInfo.gpuAccelerated
        }

        PinchArea {
            anchors.fill: parent
            pinch.target: null
            property real previousScale: 1.0
            onPinchStarted: previousScale = 1.0
            onPinchUpdated: function(pinch) {
                var ratio = pinch.scale / Math.max(previousScale, 0.001)
                visualizerModel.zoomBy(ratio)
                previousScale = pinch.scale
            }

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton
                hoverEnabled: true
                property real previousX: 0
                property real previousY: 0
                onPressed: function(mouse) {
                    previousX = mouse.x
                    previousY = mouse.y
                }
                onPositionChanged: function(mouse) {
                    if (!(mouse.buttons & Qt.LeftButton))
                        return
                    visualizerModel.orbit(mouse.x - previousX, mouse.y - previousY)
                    previousX = mouse.x
                    previousY = mouse.y
                }
                onWheel: function(wheel) {
                    visualizerModel.zoomBy(wheel.angleDelta.y > 0 ? 1.08 : 0.92)
                    wheel.accepted = true
                }
            }
        }

        Rectangle {
            id: topBar
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 78
            color: "#d905090d"
            border.color: "#1d2a33"
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 12
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Label {
                        text: "METRIQ VISUALIZER"
                        color: "#f1f8fa"
                        font.pixelSize: 14
                        font.weight: Font.Bold
                        font.letterSpacing: 1.6
                    }
                    Label {
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: visualizerModel.sourceName + "  ·  " + visualizerModel.pointCount.toLocaleString() + " points"
                        color: "#8399a4"
                        font.pixelSize: 11
                    }
                }

                MetricChip {
                    visible: root.width > 390
                    label: rendererInfo.gpuAccelerated ? "GPU" : "CPU"
                    value: rendererInfo.initialized ? rendererInfo.backendName : "Starting"
                    emphasized: rendererInfo.gpuAccelerated
                    Layout.maximumWidth: 182
                }

                ToolButton {
                    text: "•••"
                    font.pixelSize: 18
                    Accessible.name: "Open application menu"
                    onClicked: appMenu.open()

                    Menu {
                        id: appMenu
                        y: parent.height
                        MenuItem { text: "Open document…"; onTriggered: openDialog.open() }
                        MenuItem { text: "Save preset…"; onTriggered: savePresetDialog.open() }
                        MenuItem { text: "Save project…"; onTriggered: saveProjectDialog.open() }
                        MenuSeparator { }
                        MenuItem { text: "Generate demo"; onTriggered: visualizerModel.generateDemo(3500) }
                        MenuItem { text: "Reset camera"; onTriggered: visualizerModel.resetCamera() }
                    }
                }
            }
        }

        Column {
            anchors.left: parent.left
            anchors.leftMargin: 14
            anchors.top: topBar.bottom
            anchors.topMargin: 12
            spacing: 8
            z: 4

            MetricChip {
                label: "Preset"
                value: visualizerModel.presetName
            }
            MetricChip {
                visible: audioEngine.active
                label: "Live"
                value: Math.round(audioEngine.fundamentalHz) + " Hz"
                emphasized: true
            }
        }

        Rectangle {
            id: playbackBar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 12
            height: 94
            radius: 20
            color: "#e60b1117"
            border.color: "#26343d"
            border.width: 1
            z: 8

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 7

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    RoundButton {
                        text: visualizerModel.playing ? "Ⅱ" : "▶"
                        enabled: !audioEngine.active && visualizerModel.duration > 0
                        Accessible.name: visualizerModel.playing ? "Pause" : "Play"
                        onClicked: visualizerModel.playing = !visualizerModel.playing
                    }

                    Slider {
                        id: timeline
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(0.001, visualizerModel.duration)
                        value: visualizerModel.currentTime
                        enabled: !audioEngine.active && visualizerModel.duration > 0
                        onMoved: visualizerModel.currentTime = value
                        Accessible.name: "Playback position"
                    }

                    Label {
                        text: root.formatTime(visualizerModel.currentTime) + " / " + root.formatTime(visualizerModel.duration)
                        color: "#c8d6da"
                        font.family: "monospace"
                        font.pixelSize: 11
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Button {
                        text: audioEngine.active ? "Stop live" : "Live microphone"
                        highlighted: audioEngine.active
                        enabled: audioEngine.active || audioEngine.inputAvailable
                        onClicked: audioEngine.active ? audioEngine.stop() : audioEngine.start()
                    }
                    Item { Layout.fillWidth: true }
                    Button {
                        text: "Controls"
                        visible: !root.tablet
                        onClicked: root.inspectorOpen = true
                    }
                    Label {
                        visible: root.tablet
                        text: "DRAG TO ORBIT  ·  PINCH TO ZOOM"
                        color: "#617780"
                        font.pixelSize: 10
                        font.letterSpacing: 0.8
                    }
                }
            }
        }

        Label {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: playbackBar.top
            anchors.bottomMargin: 10
            visible: !root.tablet
            text: "DRAG TO ORBIT  ·  PINCH TO ZOOM"
            color: "#60737c"
            font.pixelSize: 9
            font.letterSpacing: 0.8
            z: 5
        }
    }

    InspectorSheet {
        id: inspector
        x: root.tablet ? root.width - width : 0
        y: root.tablet ? 0 : root.height - height
        width: root.tablet ? 370 : root.width
        height: root.tablet ? root.height : Math.min(root.height * 0.74, 660)
        visible: root.tablet || root.inspectorOpen
        compact: !root.tablet
        model: visualizerModel
        audio: audioEngine
        z: 30
        onCloseRequested: root.inspectorOpen = false
        onOpenRequested: openDialog.open()
        onSavePresetRequested: savePresetDialog.open()
        onSaveProjectRequested: saveProjectDialog.open()
    }

    Rectangle {
        visible: !root.tablet && root.inspectorOpen
        anchors.fill: parent
        color: "#66000000"
        z: 29
        MouseArea {
            anchors.fill: parent
            onClicked: root.inspectorOpen = false
        }
    }

    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 28
        width: Math.min(root.width - 32, toastLabel.implicitWidth + 32)
        height: toastLabel.implicitHeight + 22
        radius: 13
        color: "#e91a252d"
        border.color: "#3a4d58"
        visible: root.toastText.length > 0
        z: 100
        Label {
            id: toastLabel
            anchors.centerIn: parent
            width: Math.min(implicitWidth, root.width - 64)
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            text: root.toastText
            color: "#eef7f8"
            font.pixelSize: 12
        }
    }

    Timer {
        id: toastTimer
        interval: 3500
        onTriggered: root.toastText = ""
    }

    Timer {
        interval: 16
        repeat: true
        running: visualizerModel.playing && !audioEngine.active
        onTriggered: visualizerModel.advance(interval / 1000.0)
    }

    FileDialog {
        id: openDialog
        title: "Open Visualizer document"
        fileMode: FileDialog.OpenFile
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        nameFilters: [
            "Visualizer files (*.mvpreset *.mvproj *.bgl *.csv *.tsv *.txt)",
            "Presets (*.mvpreset)",
            "Projects (*.mvproj *.bgl)",
            "Mapped data (*.csv *.tsv *.txt)",
            "All files (*)"
        ]
        onAccepted: stateCodec.openDocument(selectedFile)
    }

    FileDialog {
        id: savePresetDialog
        title: "Save Visualizer preset"
        fileMode: FileDialog.SaveFile
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        defaultSuffix: "mvpreset"
        nameFilters: ["Visualizer preset (*.mvpreset)"]
        onAccepted: stateCodec.savePreset(selectedFile, visualizerModel.presetName)
    }

    FileDialog {
        id: saveProjectDialog
        title: "Save Visualizer project"
        fileMode: FileDialog.SaveFile
        currentFolder: StandardPaths.writableLocation(StandardPaths.DocumentsLocation)
        defaultSuffix: "mvproj"
        nameFilters: ["Visualizer project (*.mvproj)"]
        onAccepted: stateCodec.saveProject(selectedFile, "Metriq Visualizer Mobile Project")
    }

    Connections {
        target: stateCodec
        function onDocumentLoaded(kind, name) { root.showToast("Loaded " + kind + ": " + name) }
        function onDocumentSaved(kind, name) { root.showToast("Saved " + kind + ": " + name) }
        function onErrorChanged() {
            if (stateCodec.errorMessage.length > 0)
                root.showToast(stateCodec.errorMessage)
        }
    }

    Connections {
        target: audioEngine
        function onErrorChanged() {
            if (audioEngine.errorMessage.length > 0)
                root.showToast(audioEngine.errorMessage)
        }
    }

    Connections {
        target: rendererInfo
        function onChanged() {
            if (rendererInfo.errorMessage.length > 0)
                root.showToast(rendererInfo.errorMessage)
        }
    }
}
