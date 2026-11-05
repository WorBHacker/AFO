import QtQuick
import QtQuick.Controls.Basic

// Over the composer of a coordinator's run (16.4, D50): how its tasks stand - what waits for the user first - and the
// way to the board.
Rectangle {
    id: bar
    objectName: "tasksBar"
    signal boardRequested()
    readonly property color tone: Tasks.tone === "warn" ? Theme.p.warn : Tasks.tone === "error" ? Theme.p.error
                                  : Tasks.tone === "ok" ? Theme.p.ok : Theme.p.accent
    height: 36
    color: Theme.p.card
    border.color: Theme.p.hairline

    Row {
        x: 16
        width: parent.width - 32
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Icon { name: "agent"; size: 15; tint: bar.tone; anchors.verticalCenter: parent.verticalCenter }
        Text {
            objectName: "tasksSummary"
            width: parent.width - board.width - 40
            elide: Text.ElideRight
            text: Tasks.summary
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
        }
        PlainButton {
            id: board
            objectName: "tasksBoard"
            text: qsTr("Доска")
            anchors.verticalCenter: parent.verticalCenter
            onClicked: bar.boardRequested()
        }
    }
}
