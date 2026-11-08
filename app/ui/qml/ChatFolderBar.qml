import QtQuick
import QtQuick.Controls.Basic

// Over the composer of a Claude Code chat with no project (31.7): what it made in the chat's own folder - how many
// files, the newest by name - and the way to them.
Rectangle {
    id: bar
    objectName: "chatFolderBar"
    height: 36
    color: Theme.p.card
    border.color: Theme.p.hairline

    Row {
        x: 16
        width: parent.width - 32
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Icon { name: "folder"; size: 15; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
        Text {
            id: said
            width: parent.width - buttons.width - 40
            elide: Text.ElideRight
            text: ChatFolder.summary
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
            HoverHandler { id: saidHover }
            ToolTip.visible: saidHover.hovered
            ToolTip.delay: 700
            ToolTip.text: ChatFolder.path
        }
        Row {
            id: buttons
            spacing: 2
            anchors.verticalCenter: parent.verticalCenter
            PlainButton {
                objectName: "chatFolderOpen"
                iconName: "external"
                text: qsTr("Открыть папку")
                implicitHeight: 28
                onClicked: ChatFolder.openFolder()
            }
            IconButton {
                objectName: "chatFolderCopy"
                iconName: "copy"
                tip: qsTr("Копировать путь")
                onClicked: ChatFolder.copyPath()
            }
        }
    }
}
