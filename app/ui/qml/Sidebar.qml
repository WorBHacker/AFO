import QtQuick
import QtQuick.Controls.Basic

// Chats or projects (tabs at the top): chats grouped by day with a new-chat button, or a project's file tree;
// the hub at the bottom (theme switch beside it).
Rectangle {
    id: sidebar
    signal themeMenuRequested(Item anchor)
    signal settingsRequested()
    signal chatMenuRequested(string chatId, point at)
    signal projectMenuRequested(Item anchor)
    signal addProjectRequested()
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

    Rectangle {
        id: tabs
        x: 12
        y: 50
        width: parent.width - 24
        height: 32
        radius: 6
        color: Theme.p.window
        border.color: Theme.p.hairline
        Row {
            x: 3
            y: 3
            Repeater {
                model: [{ id: "chats", name: "Чаты", icon: "chat", key: "Ctrl+1" },
                        { id: "projects", name: "Проекты", icon: "folder", key: "Ctrl+2" }]
                delegate: AbstractButton {
                    id: tab
                    required property var modelData
                    readonly property bool chosen: App.mode === modelData.id
                    objectName: "tab-" + modelData.id
                    width: (tabs.width - 6) / 2
                    height: tabs.height - 6
                    hoverEnabled: true
                    onClicked: App.setMode(modelData.id)
                    background: Rectangle {
                        radius: 4
                        color: tab.chosen ? Theme.p.card_hover : (tab.hovered ? Theme.p.item_hover : "transparent")
                        border.color: tab.chosen ? Theme.p.hairline : "transparent"
                    }
                    contentItem: Item {
                        Row {
                            anchors.centerIn: parent
                            spacing: 6
                            Icon {
                                name: tab.modelData.icon
                                size: 14
                                tint: tab.chosen ? Theme.p.text : Theme.p.muted
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: tab.modelData.name
                                color: tab.chosen ? Theme.p.text : Theme.p.muted
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                                font.weight: tab.chosen ? Font.DemiBold : Font.Normal
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 900
                    ToolTip.text: modelData.key
                }
            }
        }
    }

    ProjectsPanel {
        visible: App.mode === "projects"
        y: tabs.y + tabs.height + 10
        width: parent.width
        height: hubCard.y - y - 8
        onProjectMenuRequested: (anchor) => sidebar.projectMenuRequested(anchor)
        onAddRequested: sidebar.addProjectRequested()
    }

    AbstractButton {
        id: newChat
        visible: App.mode === "chats"
        x: 12
        y: tabs.y + tabs.height + 10
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
        visible: App.mode === "chats"
        x: 8
        y: newChat.y + newChat.height + 12
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
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: (point) => sidebar.chatMenuRequested(
                    chatRow.modelData.id, chatRow.mapToItem(null, point.position.x, point.position.y))
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
            id: settingsButton
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            iconName: "sliders"
            tip: "Настройки (Ctrl+,)"
            onClicked: sidebar.settingsRequested()
        }
        IconButton {
            id: themeButton
            anchors.right: settingsButton.left
            anchors.verticalCenter: parent.verticalCenter
            iconName: Theme.dark ? "moon" : "sun"
            tip: "Тема"
            onClicked: sidebar.themeMenuRequested(themeButton)
        }
    }
}
