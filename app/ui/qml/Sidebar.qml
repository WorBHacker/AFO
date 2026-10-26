import QtQuick
import QtQuick.Controls.Basic

// Chats grouped by day, a new-chat button, and the hub at the bottom (theme switch beside it).
Rectangle {
    id: sidebar
    signal themeMenuRequested(Item anchor)
    color: Theme.p.panel
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.p.hairline }

    // the brand row doubles as a handle to move the window
    Item {
        id: brand
        width: parent.width
        height: 52
        Row {
            x: 16
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            Rectangle {
                width: 24; height: 24; radius: 7
                gradient: Gradient {
                    GradientStop { position: 0; color: Theme.p.accent }
                    GradientStop { position: 1; color: "#8b5cf6" }
                }
                Icon { anchors.centerIn: parent; name: "chat"; size: 14; tint: "#ffffff" }
            }
            Text {
                text: "LocalChat"
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        DragHandler { target: null; onActiveChanged: if (active) Window.window.startSystemMove() }
    }

    AbstractButton {
        id: newChat
        x: 12
        y: 54
        width: parent.width - 24
        height: 36
        hoverEnabled: true
        onClicked: App.newChat()
        background: Rectangle {
            radius: 6
            color: newChat.hovered ? Theme.p.card_hover : Theme.p.card
            border.color: Theme.p.hairline
        }
        contentItem: Item {
            Row {
                x: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Icon { name: "plus"; tint: Theme.p.text; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Новый чат"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Kbd { text: "Ctrl N"; anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter }
        }
    }

    ListView {
        id: chats
        x: 8
        y: 102
        width: parent.width - 16
        height: hubCard.y - y - 8
        clip: true
        model: App.chats
        spacing: 2
        boundsBehavior: Flickable.StopAtBounds
        section.property: "section"
        section.delegate: Text {
            required property string section
            width: chats.width
            height: 30
            leftPadding: 10
            bottomPadding: 6
            verticalAlignment: Text.AlignBottom
            text: section
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        ScrollBar.vertical: ThinScrollBar {}
        delegate: AbstractButton {
            id: chatRow
            required property var modelData
            readonly property bool selected: modelData.id === App.currentId
            width: chats.width
            height: 34
            hoverEnabled: true
            onClicked: App.openChat(modelData.id)
            background: Rectangle {
                radius: 6
                color: chatRow.selected ? Theme.p.item_selected : (chatRow.hovered ? Theme.p.item_hover : "transparent")
            }
            contentItem: Item {
                Icon {
                    x: 10
                    anchors.verticalCenter: parent.verticalCenter
                    name: chatRow.modelData.cloud ? "cloud" : "cpu"
                    size: 14
                    tint: chatRow.selected ? Theme.p.text_soft : Theme.p.faint
                }
                Text {
                    x: 32
                    width: parent.width - 54
                    anchors.verticalCenter: parent.verticalCenter
                    text: chatRow.modelData.title
                    elide: Text.ElideRight
                    color: chatRow.selected ? Theme.p.text : Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Rectangle {
                    visible: chatRow.modelData.busy
                    width: 7; height: 7; radius: 3.5
                    color: Theme.p.accent
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    SequentialAnimation on opacity {
                        running: chatRow.modelData.busy
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.3; duration: 700 }
                        NumberAnimation { to: 1; duration: 700 }
                    }
                }
            }
            ToolTip.visible: hovered && titleTooLong
            ToolTip.delay: 700
            ToolTip.text: modelData.title
            readonly property bool titleTooLong: modelData.title.length > 30
        }
    }

    Rectangle {
        id: hubCard
        x: 12
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 12
        width: parent.width - 24
        height: 40
        radius: 8
        color: "transparent"
        border.color: Theme.p.hairline
        Row {
            x: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            Icon { name: "layers"; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: "Хаб · " + App.modelCountText
                color: Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 12
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        IconButton {
            id: themeButton
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            iconName: Theme.dark ? "moon" : "sun"
            tip: "Тема"
            onClicked: sidebar.themeMenuRequested(themeButton)
        }
    }
}
