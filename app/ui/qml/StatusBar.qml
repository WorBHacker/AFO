import QtQuick
import QtQuick.Controls.Basic

// 26px: the hub and its port, the active model, this session's tokens and cost, Claude Code's subscription limits
// (when it has told them, 20.3), short messages; on the right a model
// being installed (its percent, speed, time left - a click opens «Установки»), the loaded local model, VRAM, Windows'
// commit charge and the GPU.
Rectangle {
    id: bar
    signal budgetsRequested()
    height: 26
    color: Theme.p.panel
    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }

    function hubColour(state) {
        return state === "up" ? Theme.p.ok : state === "lost" ? Theme.p.warn
               : state === "connecting" ? Theme.p.muted : Theme.p.error
    }

    component Small: Text {
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    }

    Row {
        id: left
        x: 12
        height: parent.height
        spacing: 10
        // the right side of the bar stays whole: here the model's name gives way first, then the session's words
        readonly property real room: right.x - x - 16 - 82 - hubLabel.implicitWidth
                                     - (App.subscriptionText !== "" ? 21 + subscriptionLabel.implicitWidth : 0)
        Rectangle {
            width: 7; height: 7; radius: 3.5
            color: bar.hubColour(App.hubState)
            anchors.verticalCenter: parent.verticalCenter
        }
        Small {
            id: hubLabel
            text: qsTr("Хаб %1").arg(App.hubPort)
            HoverHandler { id: hubHover }
            ToolTip.visible: hubHover.hovered
            ToolTip.text: App.hubTooltip
        }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Icon { name: App.modelCloud ? "cloud" : "cpu"; size: 13; anchors.verticalCenter: parent.verticalCenter }
        Small {
            id: modelLabel
            objectName: "statusModel"
            width: Math.max(0, Math.min(implicitWidth, left.room - sessionLabel.implicitWidth))
            elide: Text.ElideRight
            text: App.modelName
            HoverHandler { id: modelHover }
            ToolTip.visible: modelHover.hovered && truncated
            ToolTip.text: text
        }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Small {
            id: sessionLabel
            width: Math.max(0, Math.min(implicitWidth, left.room))
            elide: Text.ElideRight
            text: App.sessionText
            color: App.sessionWarn ? Theme.p.warn : Theme.p.muted
            HoverHandler { id: sessionHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: bar.budgetsRequested() }
            ToolTip.visible: sessionHover.hovered
            ToolTip.text: App.sessionTooltip
        }
        Separator { visible: App.subscriptionText !== ""; anchors.verticalCenter: parent.verticalCenter }
        Small {
            id: subscriptionLabel
            objectName: "statusSubscription"
            visible: App.subscriptionText !== ""
            text: App.subscriptionText
            color: App.subscriptionWarn ? Theme.p.warn : Theme.p.muted
            HoverHandler { id: subscriptionHover }
            ToolTip.visible: subscriptionHover.hovered
            ToolTip.text: App.subscriptionTip
        }
    }

    Small {
        x: left.x + left.width + 16
        width: (engine.visible ? engine.x : installing.visible ? installing.x : right.x) - x - 16
        elide: Text.ElideRight
        text: App.flashText
        color: App.flashError ? Theme.p.warn : Theme.p.text_soft
    }

    // 39.3: the hub fetches the engine by itself - how far, in plain words; a click opens the wizard
    Row {
        id: engine
        objectName: "statusEngine"
        visible: !!Setup.status.text
        anchors.right: installing.visible ? installing.left : right.left
        anchors.rightMargin: 14
        height: parent.height
        spacing: 6
        HoverHandler { id: engineHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: App.openUi("setup") }
        ToolTip.visible: engineHover.hovered
        ToolTip.text: Setup.status.tip || ""
        readonly property bool roomy: (installing.visible ? installing.x : right.x) - (left.x + left.width)
                                      >= engineFull.width + 150
        TextMetrics { id: engineFull; font.family: Theme.uiFont; font.pixelSize: 12; text: Setup.status.text || "" }
        Icon { name: "download"; size: 13; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
        Rectangle {
            width: 44; height: 4; radius: 2
            color: Theme.p.hairline
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, Setup.status.percent || 0))
                height: 4; radius: 2
                color: Theme.p.accent
            }
        }
        Small { text: engine.roomy ? Setup.status.text || "" : Setup.status.short || "" }
        Separator { anchors.verticalCenter: parent.verticalCenter }
    }

    Row {
        id: installing
        objectName: "statusInstall"
        visible: Installer.status.name !== ""
        anchors.right: right.left
        anchors.rightMargin: 14
        height: parent.height
        spacing: 6
        HoverHandler { id: installHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: Installer.showJobs() }
        ToolTip.visible: installHover.hovered
        ToolTip.text: Installer.status.tip
        // a narrow bar: the name gives way first, then the speed and time left (the tooltip says it all)
        readonly property real room: right.x - (left.x + left.width) - 40
        readonly property bool roomy: room >= fullText.width + progressBar.width + 50
        TextMetrics { id: fullText; font.family: Theme.uiFont; font.pixelSize: 12; text: Installer.status.text }
        Icon { name: "download"; size: 13; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
        Small {
            width: Math.max(0, Math.min(implicitWidth, installing.room - progressBar.width - installText.implicitWidth - 60))
            visible: installing.roomy && width > 24
            elide: Text.ElideRight
            text: Installer.status.name
        }
        Rectangle {
            id: progressBar
            width: 44; height: 4; radius: 2
            color: Theme.p.hairline
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                width: parent.width * Math.max(0, Math.min(1, Installer.status.percent || 0))
                height: 4; radius: 2
                color: Theme.p.accent
            }
        }
        Small { id: installText; text: installing.roomy ? Installer.status.text : Installer.status.short }
        Separator { anchors.verticalCenter: parent.verticalCenter }
    }

    Row {
        id: right
        objectName: "statusRight"
        anchors.right: parent.right
        anchors.rightMargin: 12
        height: parent.height
        spacing: 10
        HoverHandler { id: machineHover }
        ToolTip.visible: machineHover.hovered && App.status.details !== ""
        ToolTip.text: App.status.details
        Icon { name: "cpu"; size: 13; anchors.verticalCenter: parent.verticalCenter }
        Small { text: App.status.local; width: Math.min(implicitWidth, bar.width * 0.14); elide: Text.ElideRight }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Meter {
            label: "VRAM"
            value: App.status.vram
            valueText: App.status.vramText
            anchors.verticalCenter: parent.verticalCenter
        }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Meter {
            label: qsTr("Память")
            value: App.status.commit
            valueText: App.status.commitText
            warn: App.status.commitWarn
            anchors.verticalCenter: parent.verticalCenter
        }
        Separator { anchors.verticalCenter: parent.verticalCenter; visible: App.status.gpu !== "" }
        Icon { name: "bolt"; size: 13; visible: App.status.gpu !== ""; anchors.verticalCenter: parent.verticalCenter }
        Small { text: App.status.gpu; visible: text !== "" }
    }
}
