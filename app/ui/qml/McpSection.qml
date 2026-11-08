import QtQuick
import QtQuick.Controls.Basic

// Outside MCP servers (9.5, D42), in Connections: their tools go to new chats and the agent's tasks, each asked as
// its class of action says. The list is always seen; a change needs the connections' password (Services.state).
Column {
    id: section
    objectName: "mcpSection"
    spacing: 8
    readonly property bool unlocked: Services.state === "open"

    component Note: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
    SectionTitle { text: qsTr("Серверы MCP") }
    Note {
        text: qsTr("Внешние серверы MCP дают свои инструменты новым чатам и задачам агента. Сервер — программа, она работает от вашего имени: добавляйте только те, которым доверяете. Что только читает — без вопроса; остальное — через подтверждение, как велит класс действия инструмента.")
    }
    Note {
        visible: Mcp.servers.length === 0
        color: Theme.p.faint
        text: qsTr("Серверов пока нет.")
    }

    Repeater {
        model: Mcp.servers
        delegate: Column {
            id: server
            required property var modelData
            width: parent ? parent.width : 400
            spacing: 6

            AbstractButton {
                objectName: "mcpServer_" + server.modelData.name
                width: parent.width
                implicitHeight: 30
                hoverEnabled: true
                onClicked: Mcp.toggle(server.modelData.name)
                background: Rectangle { radius: 6; color: parent.hovered ? Theme.p.item_hover : "transparent" }
                contentItem: Row {
                    spacing: 8
                    leftPadding: 4
                    Icon {
                        name: server.modelData.opened ? "chevron-down" : "chevron-right"
                        size: 14
                        tint: Theme.p.muted
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        id: serverName
                        width: Math.max(0, Math.min(implicitWidth, parent.width * 0.6))
                        elide: Text.ElideRight
                        text: server.modelData.name
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Rectangle {
                        width: 7; height: 7; radius: 3.5
                        anchors.verticalCenter: parent.verticalCenter
                        color: server.modelData.state === "ready" ? Theme.p.ok
                             : server.modelData.state === "error" ? Theme.p.error : Theme.p.faint
                    }
                    Text {
                        width: Math.max(0, Math.min(implicitWidth, parent.width - serverName.width - 49))
                        elide: Text.ElideRight
                        text: server.modelData.status
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
            Text {
                width: parent.width
                elide: Text.ElideMiddle
                text: server.modelData.target
                color: Theme.p.faint
                font.family: Theme.monoFont
                font.pixelSize: 11
            }
            Note {
                visible: server.modelData.detail !== ""
                color: Theme.p.error
                text: server.modelData.detail
            }
            Note {
                visible: server.modelData.env !== ""
                text: qsTr("Ключи в окружении: %1").arg(server.modelData.env)
            }

            Column {
                visible: server.modelData.opened
                width: parent.width
                spacing: 4
                Repeater {
                    model: server.modelData.tools
                    delegate: Item {
                        id: tool
                        required property var modelData
                        width: parent ? parent.width : 400
                        implicitHeight: Math.max(toolText.implicitHeight, classButton.implicitHeight) + 6
                        Column {
                            id: toolText
                            x: 22
                            width: parent.width - classButton.width - 30
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                elide: Text.ElideRight
                                text: tool.modelData.tool
                                color: Theme.p.text
                                font.family: Theme.monoFont
                                font.pixelSize: 12
                            }
                            Text {
                                width: parent.width
                                visible: text !== ""
                                elide: Text.ElideRight
                                text: tool.modelData.description
                                color: Theme.p.faint
                                font.family: Theme.uiFont
                                font.pixelSize: 11
                            }
                        }
                        PlainButton {
                            id: classButton
                            objectName: "mcpClass_" + server.modelData.name + "_" + tool.modelData.tool
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            text: tool.modelData.clsText + (tool.modelData.changed ? qsTr(" · ваш выбор") : "")
                            enabled: section.unlocked && !Mcp.busy
                            onClicked: {
                                classMenu.server = server.modelData.name
                                classMenu.tool = tool.modelData.tool
                                classMenu.anchor = classButton
                                classMenu.open()
                            }
                        }
                    }
                }
                Note {
                    visible: !section.unlocked && server.modelData.tools.length > 0
                    color: Theme.p.faint
                    text: qsTr("Класс инструмента меняется по паролю подключений.")
                }
            }

            Flow {
                visible: section.unlocked
                width: parent.width
                spacing: 8
                PlainButton {
                    objectName: "mcpEnable_" + server.modelData.name
                    iconName: "power"
                    text: server.modelData.enabled ? qsTr("Выключить") : qsTr("Включить")
                    enabled: !Mcp.busy
                    onClicked: Mcp.setEnabled(server.modelData.name, !server.modelData.enabled)
                }
                PlainButton {
                    visible: server.modelData.enabled
                    iconName: "refresh"
                    text: qsTr("Перезапустить")
                    enabled: !Mcp.busy
                    onClicked: Mcp.restart(server.modelData.name)
                }
                PlainButton {
                    kind: "danger"
                    iconName: "trash"
                    text: qsTr("Удалить")
                    onClicked: Mcp.remove(server.modelData.name)
                }
            }
            Row {
                visible: section.unlocked && server.modelData.opened
                width: parent.width
                spacing: 8
                Field {
                    id: envField
                    width: parent.width - envSave.width - 8
                    echoMode: TextInput.Password
                    placeholderText: qsTr("Ключи заново: KEY=значение; KEY2=значение")
                    onAccepted: envSave.clicked()
                }
                PlainButton {
                    id: envSave
                    text: qsTr("Сохранить ключи")
                    enabled: envField.text.trim() !== "" && !Mcp.busy
                    onClicked: { Mcp.setEnv(server.modelData.name, envField.text); envField.text = "" }
                }
            }
            Item { width: 1; height: 4 }
        }
    }

    MenuPopup {
        id: classMenu
        property string server
        property string tool
        menuWidth: 360
        items: Mcp.classes
        onChosen: (id) => Mcp.setClass(server, tool, id)
        below: true
    }

    // ---------- adding one ----------
    Column {
        visible: section.unlocked
        width: parent.width
        spacing: 6
        Text {
            text: qsTr("Добавить сервер")
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        Field {
            id: newName
            objectName: "mcpNewName"
            width: parent.width
            placeholderText: qsTr("Имя: латиница, например files или github")
        }
        Field {
            id: newTarget
            objectName: "mcpNewTarget"
            width: parent.width
            font.family: Theme.monoFont
            placeholderText: qsTr("Команда (npx -y @modelcontextprotocol/server-filesystem D:/Docs) или адрес https://…/mcp")
        }
        Field {
            id: newEnv
            objectName: "mcpNewEnv"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: qsTr("Ключи, если нужны: KEY=значение; KEY2=значение")
        }
        PlainButton {
            objectName: "mcpAdd"
            kind: "primary"
            iconName: "plus"
            text: Mcp.busy ? qsTr("Запускаю…") : qsTr("Добавить и запустить")
            enabled: newName.text.trim() !== "" && newTarget.text.trim() !== "" && !Mcp.busy
            onClicked: {
                Mcp.add(newName.text, newTarget.text, newEnv.text)
                newName.text = ""; newTarget.text = ""; newEnv.text = ""
            }
        }
    }
    Text {
        objectName: "mcpError"
        visible: Mcp.error !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Mcp.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
}
