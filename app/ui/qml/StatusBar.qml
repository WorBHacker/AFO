import QtQuick
import QtQuick.Controls.Basic

// 26px: the hub and its port, the active model, this session's tokens and cost, short messages; on the right the
// loaded local model, VRAM, Windows' commit charge and the GPU.
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
        Rectangle {
            width: 7; height: 7; radius: 3.5
            color: bar.hubColour(App.hubState)
            anchors.verticalCenter: parent.verticalCenter
        }
        Small {
            text: "Хаб " + App.hubPort
            HoverHandler { id: hubHover }
            ToolTip.visible: hubHover.hovered
            ToolTip.text: App.hubTooltip
        }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Icon { name: App.modelCloud ? "cloud" : "cpu"; size: 13; anchors.verticalCenter: parent.verticalCenter }
        Small { text: App.modelName }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Small {
            text: App.sessionText
            color: App.sessionWarn ? Theme.p.warn : Theme.p.muted
            HoverHandler { id: sessionHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: bar.budgetsRequested() }
            ToolTip.visible: sessionHover.hovered
            ToolTip.text: App.sessionTooltip
        }
    }

    Small {
        x: left.x + left.width + 16
        width: right.x - x - 16
        elide: Text.ElideRight
        text: App.flashText
        color: App.flashError ? Theme.p.warn : Theme.p.text_soft
    }

    Row {
        id: right
        anchors.right: parent.right
        anchors.rightMargin: 12
        height: parent.height
        spacing: 10
        HoverHandler { id: machineHover }
        ToolTip.visible: machineHover.hovered && App.status.details !== ""
        ToolTip.text: App.status.details
        Icon { name: "cpu"; size: 13; anchors.verticalCenter: parent.verticalCenter }
        Small { text: App.status.local }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Meter {
            label: "VRAM"
            value: App.status.vram
            valueText: App.status.vramText
            anchors.verticalCenter: parent.verticalCenter
        }
        Separator { anchors.verticalCenter: parent.verticalCenter }
        Meter {
            label: "Память"
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
