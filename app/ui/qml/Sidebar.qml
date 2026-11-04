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
                text: "AFO"
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        DragHandler { target: null; onActiveChanged: if (active) brand.Window.window.startSystemMove() }
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
                        { id: "projects", name: "Проекты", icon: "folder", key: "Ctrl+2" },
                        { id: "agent", name: "Агент", icon: "agent", key: "Ctrl+3" }]
                delegate: AbstractButton {
                    id: tab
                    required property var modelData
                    readonly property bool chosen: App.mode === modelData.id
                    objectName: "tab-" + modelData.id
                    width: (tabs.width - 6) / 3
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

    function focusProjectSearch() { projectsPanel.focusSearch() }

    ProjectsPanel {
        id: projectsPanel
        visible: App.mode === "projects"
        y: tabs.y + tabs.height + 10
        width: parent.width
        height: (projectChats.visible ? projectChats.y : footer.y) - y - 8
        onProjectMenuRequested: (anchor) => sidebar.projectMenuRequested(anchor)
        onAddRequested: sidebar.addProjectRequested()
    }

    // the project's sessions (2026-10-28, the user's word): its chats stay in «Проекты», not among «Чаты»
    component PlainRow: AbstractButton {
        id: row
        property string iconName
        property bool chosen: false
        height: 32
        hoverEnabled: true
        background: Rectangle {
            radius: 6
            color: row.chosen ? Theme.p.item_selected : (row.hovered ? Theme.p.item_hover : "transparent")
        }
        contentItem: Item {
            Icon {
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                name: row.iconName
                size: 14
                tint: row.chosen ? Theme.p.text_soft : Theme.p.faint
            }
            Text {
                x: 32
                width: parent.width - 40
                anchors.verticalCenter: parent.verticalCenter
                text: row.text
                elide: Text.ElideRight
                color: row.chosen ? Theme.p.text : Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
    }
    Item {
        id: projectChats
        objectName: "projectChats"
        visible: App.mode === "projects" && Projects.currentId !== ""
        x: 8
        width: parent.width - 16
        // as tall as the user dragged its line (kept between runs), else as its chats need; files keep 200px
        readonly property int natural: Math.min(Math.round(parent.height * 0.42), 110 + Math.max(1, App.projectChats.length) * 34)
        height: App.projectChatsHeight > 0
                ? Math.max(76, Math.min(App.projectChatsHeight, footer.y - projectsPanel.y - 210)) : natural
        y: footer.y - height - 8
        Rectangle {
            width: parent.width
            height: grip.containsMouse || grip.pressed ? 2 : 1
            color: grip.containsMouse || grip.pressed ? Theme.p.accent : Theme.p.hairline
        }
        // the line moves up and down (2026-10-28, the user's word); a double click: back to as tall as needed
        MouseArea {
            id: grip
            objectName: "projectChatsGrip"
            y: -4
            width: parent.width
            height: 9
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            property real startY
            property int startHeight
            onPressed: (mouse) => { startY = mapToItem(null, 0, mouse.y).y; startHeight = projectChats.height }
            onPositionChanged: (mouse) => {
                if (pressed) App.setProjectChatsHeight(startHeight - (mapToItem(null, 0, mouse.y).y - startY))
            }
            onDoubleClicked: App.setProjectChatsHeight(0)
            ToolTip.visible: containsMouse && !pressed
            ToolTip.delay: 800
            ToolTip.text: "Потяните вверх или вниз. Двойной щелчок — по размеру списка"
        }
        Text {
            x: 10
            y: 10
            text: "Чаты проекта"
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Column {
            id: projectChatTop
            y: 32
            width: parent.width
            spacing: 2
            PlainRow {
                objectName: "projectOverviewRow"
                width: parent.width
                iconName: "layers"
                text: "Обзор проекта"
                chosen: !App.showProjectChat
                onClicked: App.showProjectOverview()
            }
            PlainRow {
                objectName: "projectNewSession"
                width: parent.width
                iconName: "plus"
                text: "Новый сеанс"
                chosen: App.showProjectChat && App.currentId === ""
                onClicked: App.newProjectChat()
            }
        }
        ListView {
            objectName: "projectChatList"
            y: projectChatTop.y + projectChatTop.height + 4
            width: parent.width
            height: parent.height - y
            clip: true
            model: App.projectChats
            spacing: 2
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ThinScrollBar {}
            delegate: PlainRow {
                required property var modelData
                width: ListView.view.width
                iconName: "chat"
                text: modelData.title
                chosen: App.showProjectChat && modelData.id === App.currentId
                onClicked: App.openProjectChat(modelData.id)
                TapHandler {
                    acceptedButtons: Qt.RightButton
                    onTapped: (point) => sidebar.chatMenuRequested(modelData.id, parent.mapToItem(null, point.position.x,
                                                                                                    point.position.y))
                }
            }
        }
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

    // ---------- the Agent mode: the autonomous agent's tasks (D40) ----------
    AbstractButton {
        id: newTask
        objectName: "newAgentTask"
        visible: App.mode === "agent"
        x: 12
        y: tabs.y + tabs.height + 10
        width: parent.width - 24
        height: 36
        hoverEnabled: true
        onClicked: Autonomy.newTask()
        background: Rectangle {
            radius: 6
            color: newTask.hovered || Autonomy.selectedId === "" ? Theme.p.card_hover : Theme.p.card
            border.color: Theme.p.hairline
        }
        contentItem: Item {
            Row {
                x: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Icon { name: "plus"; tint: Theme.p.text; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Новая задача"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
    ScheduleList {
        id: scheduled
        visible: App.mode === "agent" && Schedules.items.length > 0
        x: 8
        y: newTask.y + newTask.height + 10
        width: parent.width - 16
    }
    ListView {
        id: tasks
        objectName: "agentTasks"
        visible: App.mode === "agent"
        x: 8
        y: (scheduled.visible ? scheduled.y + scheduled.height : newTask.y + newTask.height) + 12
        width: parent.width - 16
        height: footer.y - y - 8
        clip: true
        model: Autonomy.tasks
        spacing: 2
        boundsBehavior: Flickable.StopAtBounds
        section.property: "section"
        section.delegate: Text {
            required property string section
            width: tasks.width
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
            id: taskRow
            required property var modelData
            width: tasks.width
            height: 46
            hoverEnabled: true
            onClicked: Autonomy.select(modelData.id)
            background: Rectangle {
                radius: 6
                color: taskRow.modelData.selected ? Theme.p.item_selected : (taskRow.hovered ? Theme.p.item_hover : "transparent")
            }
            contentItem: Item {
                Icon {
                    x: 10
                    anchors.verticalCenter: parent.verticalCenter
                    name: taskRow.modelData.waiting ? "shield" : taskRow.modelData.live ? "agent"
                          : taskRow.modelData.status === "done" ? "check" : "close"
                    size: 14
                    tint: taskRow.modelData.waiting ? Theme.p.warn : taskRow.modelData.live ? Theme.p.accent
                          : taskRow.modelData.status === "done" && taskRow.modelData.goalMet ? Theme.p.ok : Theme.p.faint
                }
                Column {
                    x: 32
                    width: parent.width - 40
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    Text {
                        width: parent.width
                        text: taskRow.modelData.title
                        elide: Text.ElideRight
                        color: taskRow.modelData.selected ? Theme.p.text : Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        text: taskRow.modelData.statusLabel
                        color: taskRow.modelData.waiting ? Theme.p.warn : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
            }
        }
    }

    ListView {
        id: chats
        visible: App.mode === "chats"
        x: 8
        y: newChat.y + newChat.height + 12
        width: parent.width - 16
        height: footer.y - y - 8
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

    Column {
        id: footer
        x: 12
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 12
        width: parent.width - 24
        spacing: 8

        // the local model in memory, and a way to give its VRAM and RAM back (asked first)
        Rectangle {
            id: modelCard
            objectName: "loadedModelCard"
            visible: (App.status.loaded || []).length > 0
            width: parent.width
            height: 40
            radius: 8
            color: "transparent"
            border.color: Theme.p.hairline
            Row {
                x: 10
                width: parent.width - unloadButton.width - 18
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Icon { name: "cpu"; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    width: parent.width - 22
                    elide: Text.ElideRight
                    text: App.status.local || ""
                    color: Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            PlainButton {
                id: unloadButton
                objectName: "unloadModel"
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                implicitHeight: 30
                implicitWidth: 100  // a little tighter than other buttons: the model's name needs the room
                iconName: "power"
                text: "Выгрузить"
                onClicked: App.askUnloadLocal()
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: "Выгрузить модель из видеопамяти и ОЗУ (с подтверждением)"
            }
        }

        Rectangle {
            id: hubCard
            width: parent.width
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
}
