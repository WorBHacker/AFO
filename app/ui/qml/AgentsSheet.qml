import QtQuick
import QtQuick.Controls.Basic

// The hub's own agents (D16): profiles - who the agent is and what it may do - a task for the chosen one, and the
// latest runs. A run is a conversation: "Открыть" shows it in the chat view; its approvals come to the queue.
Sheet {
    id: sheet
    objectName: "agentsSheet"
    title: qsTr("Агенты")
    iconName: "agent"
    width: Math.min(760, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Agents.load()

    component Check: AbstractButton {
        id: check
        property bool on: false
        implicitHeight: 28
        implicitWidth: checkLabel.implicitWidth + 30
        hoverEnabled: true
        background: Rectangle { radius: 6; color: check.hovered ? Theme.p.item_hover : "transparent" }
        contentItem: Item {
            Rectangle {
                x: 6
                width: 14
                height: 14
                radius: 3
                anchors.verticalCenter: parent.verticalCenter
                color: check.on ? Theme.p.accent : "transparent"
                border.color: check.on ? Theme.p.accent : Theme.p.hairline_strong
                Icon { anchors.centerIn: parent; visible: check.on; name: "check"; size: 11; tint: Theme.p.on_accent }
            }
            Text {
                id: checkLabel
                x: 26
                anchors.verticalCenter: parent.verticalCenter
                text: check.text
                color: check.on ? Theme.p.text : Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
    }

    component Label: Text {
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }

    // ---------- the profiles ----------
    Flow {
        width: parent.width
        spacing: 6
        Repeater {
            model: Agents.profiles
            delegate: Chip {
                required property var modelData
                iconName: "agent"
                menu: false
                text: modelData.name
                active: modelData.selected
                onClicked: Agents.editProfile(modelData.id)
            }
        }
        PlainButton { objectName: "newAgent"; iconName: "plus"; text: qsTr("Новый агент"); onClicked: Agents.newProfile() }
    }

    // ---------- the draft ----------
    Label { text: Agents.draft.saved ? qsTr("Агент · версия %1").arg(Agents.draft.version) : qsTr("Новый агент") }
    Field {
        objectName: "agentName"
        width: parent.width
        placeholderText: qsTr("Имя: например, «Ревьюер» или «Чинильщик тестов»")
        text: Agents.draft.name || ""
        onEditingFinished: Agents.setField("name", text)
    }
    Rectangle {
        width: parent.width
        height: 120
        radius: 6
        color: Theme.p.card
        border.color: instructions.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: instructions
                objectName: "agentInstructions"
                placeholderText: qsTr("Роль: что агент делает и как — например, «читаешь код, правишь по одному файлу, после правки запускаешь тесты»")
                placeholderTextColor: Theme.p.faint
                text: Agents.draft.instructions || ""
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                selectionColor: Theme.p.selection
                font.family: Theme.uiFont
                font.pixelSize: 13
                padding: 10
                background: null
                onEditingFinished: Agents.setField("instructions", text)
            }
        }
    }
    Row {
        spacing: 8
        PlainButton {
            id: modelButton
            objectName: "agentModel"
            maxWidth: parent.width
            iconName: "cpu"
            text: Agents.draft.modelName
            onClicked: { modelMenu.anchor = modelButton; modelMenu.open() }
        }
        Segmented {
            options: [{ id: "read_only", name: qsTr("Только чтение") }, { id: "ask", name: qsTr("Спрашивать") },
                      { id: "trusted", name: qsTr("Доверенный") }]
            value: Agents.draft.mode || "ask"
            onPicked: (id) => Agents.setField("mode", id)
        }
    }
    MenuPopup {
        id: modelMenu
        menuWidth: 430
        items: Agents.modelChoices
        onChosen: (id) => Agents.setField("model_id", id)
    }

    Label { text: qsTr("Инструменты") }
    Flow {
        width: parent.width
        spacing: 2
        Repeater {
            model: Agents.toolChoices
            delegate: Check {
                required property var modelData
                text: modelData.name
                on: modelData.selected
                onClicked: Agents.toggleTool(modelData.id)
            }
        }
    }
    Label { text: qsTr("Проекты") }
    Flow {
        width: parent.width
        spacing: 2
        Repeater {
            model: Agents.projectChoices
            delegate: Check {
                required property var modelData
                text: modelData.name
                on: modelData.selected
                onClicked: Agents.toggleProject(modelData.id)
            }
        }
    }
    Row {
        spacing: 8
        Field {
            width: 150
            placeholderText: qsTr("Шагов (1–200)")
            text: Agents.draft.max_steps !== undefined ? String(Agents.draft.max_steps) : ""
            onEditingFinished: Agents.setField("max_steps", text)
        }
        Field {
            width: 150
            placeholderText: qsTr("Минут")
            text: Agents.draft.max_minutes !== undefined ? String(Agents.draft.max_minutes) : ""
            onEditingFinished: Agents.setField("max_minutes", text)
        }
        Field {
            width: 190
            placeholderText: qsTr("Бюджет задачи, $ (платные)")
            text: Agents.draft.budget || ""
            onEditingFinished: Agents.setField("budget", text)
        }
    }
    Row {
        spacing: 8
        PlainButton { objectName: "saveAgent"; kind: "primary"; iconName: "check"; text: qsTr("Сохранить"); onClicked: Agents.save() }
        PlainButton {
            visible: Agents.draft.saved
            kind: "danger"
            iconName: "trash"
            text: qsTr("Удалить")
            onClicked: Agents.deleteProfile()
        }
    }

    // ---------- a task ----------
    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
    Label { text: Agents.draft.saved ? qsTr("Задача для «%1»").arg(Agents.draft.name) : qsTr("Сохраните агента, чтобы дать ему задачу") }
    Text {
        objectName: "agentEstimate"
        visible: Agents.draft.saved && text !== ""
        width: parent.width
        text: qsTr("Цена задачи: %1").arg(Agents.estimate)
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Row {
        width: parent.width
        spacing: 8
        Field {
            id: task
            objectName: "agentTask"
            enabled: Agents.draft.saved
            width: parent.width - go.width - 8
            placeholderText: qsTr("Что сделать — Enter")
            onAccepted: { Agents.startRun(text); text = "" }
        }
        PlainButton {
            id: go
            kind: "primary"
            iconName: "send"
            text: qsTr("Запустить")
            enabled: Agents.draft.saved && task.text.trim() !== ""
            onClicked: { Agents.startRun(task.text); task.text = "" }
        }
    }

    // ---------- runs ----------
    Label { visible: Agents.runs.length > 0; text: qsTr("Прогоны") }
    Column {
        objectName: "agentRuns"
        width: parent.width
        spacing: 2
        Repeater {
            model: Agents.runs
            delegate: Item {
                id: run
                required property var modelData
                width: parent.width
                height: 46
                HoverHandler { id: runHover }
                Rectangle { anchors.fill: parent; radius: 6; color: runHover.hovered ? Theme.p.item_hover : "transparent" }
                Icon {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    name: run.modelData.icon
                    size: 15
                    tint: run.modelData.level === "error" ? Theme.p.error : run.modelData.level === "warn" ? Theme.p.warn : Theme.p.text_soft
                }
                Column {
                    x: 32
                    width: parent.width - x - actions.width - 12
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        width: parent.width
                        text: run.modelData.task
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        width: parent.width
                        text: run.modelData.status + " · " + run.modelData.note
                        elide: Text.ElideRight
                        color: run.modelData.level === "error" ? Theme.p.error : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
                Row {
                    id: actions
                    anchors.right: parent.right
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4
                    PlainButton {
                        visible: run.modelData.active
                        implicitHeight: 28
                        text: qsTr("Остановить")
                        onClicked: Agents.cancelRun(run.modelData.id)
                    }
                    PlainButton {
                        implicitHeight: 28
                        text: qsTr("Открыть")
                        onClicked: { Agents.openRun(run.modelData.id); sheet.close() }
                    }
                }
            }
        }
    }
}
