import QtQuick
import QtQuick.Controls.Basic

// The Agent mode's main area (D40), as a chat (2026-10-28): the input at the bottom (AgentComposer) takes a new
// task's goal or, while a task runs, a word to the agent. Above it: with no task open, «what should the agent do?»
// with templates; the open task - its goal as the user's message, the plan as it goes, the agent's questions and the
// user's answers, the user's own plan, the report. Its approvals come as the card over this view, as in a chat (D38).
Item {
    id: view
    objectName: "agentView"
    readonly property var task: Autonomy.task
    readonly property bool open: Autonomy.selectedId !== ""
    property bool editing: false
    property bool follow: true  // the thread keeps its end in sight until the user scrolls up
    property real reserveTop: 0  // the approval card's room over the view
    onOpenChanged: { editing = false; follow = true }

    component Label: Text {
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Area: Rectangle {
        id: box
        property alias text: input.text
        property string hint
        property string name
        height: 96
        radius: 6
        color: Theme.p.card
        border.color: input.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: input
                objectName: box.name
                placeholderText: box.hint
                placeholderTextColor: Theme.p.faint
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                selectionColor: Theme.p.selection
                font.family: Theme.uiFont
                font.pixelSize: 13
                padding: 10
                background: null
            }
        }
    }
    // the user's words: a card on the right, as in a chat
    component Said: Item {
        id: said
        property string text
        property string note
        width: parent ? parent.width : 400
        height: bubble.height
        Rectangle {
            id: bubble
            anchors.right: parent.right
            width: Math.min(said.width * 0.8, Math.max(saidText.implicitWidth, saidNote.implicitWidth) + 32)
            height: saidBody.height + 24
            radius: 8
            color: Theme.p.card
            border.color: Theme.p.hairline
            Column {
                id: saidBody
                x: 16
                y: 12
                width: parent.width - 32
                spacing: 4
                TextEdit {
                    id: saidText
                    width: Math.min(said.width * 0.8 - 32, implicitWidth)
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    text: said.text
                    color: Theme.p.text
                    selectionColor: Theme.p.selection
                    selectedTextColor: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                }
                Text {
                    id: saidNote
                    visible: said.note !== ""
                    width: Math.min(said.width * 0.8 - 32, implicitWidth)
                    wrapMode: Text.Wrap
                    text: said.note
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
        }
    }
    // the agent's words: on the left, after its mark; a framed card when it needs the user (a question, the report)
    component Told: Item {
        id: told
        property string text
        property string title
        property bool framed: false
        property color line: Theme.p.hairline_strong
        default property alias extra: toldExtra.data
        width: parent ? parent.width : 400
        height: Math.max(26, toldBody.height + (framed ? 24 : 0))
        Rectangle {
            anchors.fill: parent
            radius: 8
            visible: told.framed
            color: Theme.p.card
            border.color: told.line
        }
        Icon {
            x: told.framed ? 12 : 0
            y: told.framed ? 13 : 1
            name: "agent"
            size: 17
            tint: Theme.p.accent
        }
        Column {
            id: toldBody
            x: told.framed ? 40 : 28
            y: told.framed ? 12 : 0
            width: parent.width - x - (told.framed ? 14 : 0)
            spacing: 6
            Text {
                visible: told.title !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: told.title
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            TextEdit {
                visible: told.text !== ""
                width: parent.width
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                text: told.text
                color: Theme.p.text
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
            }
            Column { id: toldExtra; width: parent.width; spacing: 6 }
        }
    }

    Flickable {
        id: flick
        width: parent.width
        height: parent.height - composer.height
        contentHeight: column.height + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        onMovementEnded: view.follow = atYEnd
        onContentHeightChanged: if (view.open && view.follow) contentY = Math.max(0, contentHeight - height)

        Column {
            id: column
            y: 24 + view.reserveTop
            x: (view.width - width) / 2
            width: Math.min(780, view.width - 48)
            spacing: 16

            // ================= no task open: what should the agent do? =================
            Column {
                objectName: "agentForm"
                visible: !view.open
                width: parent.width
                spacing: 14

                Item { width: 1; height: Math.max(0, (flick.height - 360) / 3) }
                Icon { name: "agent"; size: 30; tint: Theme.p.accent; anchors.horizontalCenter: parent.horizontalCenter }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Что сделать агенту?"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                }
                Text {
                    width: Math.min(parent.width, 600)
                    anchors.horizontalCenter: parent.horizontalCenter
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: "Напишите задачу в поле внизу — как человеку. Агент составит план, выполнит шаги, проверит себя и напишет отчёт. Под полем — режим, модель, проекты, время запуска и ограничения: нажмите любое, чтобы увидеть, что оно значит."
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    lineHeight: 1.3
                }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: Autonomy.templates
                        delegate: Chip {
                            required property var modelData
                            iconName: "sparkle"
                            menu: false
                            text: modelData.name
                            onClicked: composer.useTemplate(modelData.id)
                        }
                    }
                }
                Row {
                    spacing: 8
                    anchors.horizontalCenter: parent.horizontalCenter
                    PlainButton {
                        objectName: "agentMemory"
                        iconName: "layers"
                        text: "Память агента"
                        onClicked: App.openUi("memory")
                    }
                    PlainButton {
                        objectName: "agentAccess"
                        iconName: "folder"
                        text: "Папки и сайты"
                        onClicked: App.openUi("agent-access")
                    }
                }
            }

            // ================= the open task, as a chat =================
            Column {
                objectName: "agentTask"
                visible: view.open
                width: parent.width
                spacing: 14

                Said {
                    objectName: "agentTaskGoal"
                    text: view.task.goal || ""
                    note: view.task.doneWhen ? "Готово, когда: " + view.task.doneWhen : ""
                }
                Text {
                    objectName: "agentStatus"
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: [view.task.statusLabel, view.task.autonomy ? "режим «" + view.task.autonomy + "»" : "", view.task.refusal, view.task.meta]
                          .filter(s => !!s).join(" · ")
                    color: view.task.status === "waiting" ? Theme.p.warn : view.task.status === "error" ? Theme.p.error : Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }

                // ---------- the plan ----------
                Told {
                    title: view.task.planVersion ? "План · версия " + view.task.planVersion + (view.task.progress ? " · сделано " + view.task.progress : "")
                                                 : (view.task.live ? "Составляю план…" : "Плана нет")
                    text: view.task.planReason ? "Почему новый план: " + view.task.planReason : ""
                    Column {
                        objectName: "agentSteps"
                        width: parent.width
                        spacing: 4
                        Repeater {
                            model: view.task.steps || []
                            delegate: Item {
                                required property var modelData
                                width: parent.width
                                height: stepBody.height + 10
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 6
                                    color: modelData.status === "running" ? Theme.p.item_selected : "transparent"
                                }
                                Icon {
                                    x: 6
                                    y: 7
                                    name: modelData.icon
                                    size: 15
                                    tint: modelData.status === "done" ? Theme.p.ok : modelData.status === "failed" ? Theme.p.error
                                          : modelData.status === "running" ? Theme.p.accent : Theme.p.faint
                                }
                                Column {
                                    id: stepBody
                                    x: 30
                                    y: 5
                                    width: parent.width - 38
                                    spacing: 2
                                    Text {
                                        width: parent.width
                                        wrapMode: Text.Wrap
                                        text: modelData.n + ". " + modelData.title + "  ·  " + modelData.label
                                        color: modelData.status === "pending" ? Theme.p.text_soft : Theme.p.text
                                        font.family: Theme.uiFont
                                        font.pixelSize: 13
                                    }
                                    Text {
                                        visible: modelData.check !== "" && modelData.note === ""
                                        width: parent.width
                                        wrapMode: Text.Wrap
                                        text: "проверка: " + modelData.check
                                        color: Theme.p.faint
                                        font.family: Theme.uiFont
                                        font.pixelSize: 12
                                    }
                                    Text {
                                        visible: modelData.note !== ""
                                        width: parent.width
                                        wrapMode: Text.Wrap
                                        text: modelData.note
                                        color: modelData.status === "failed" ? Theme.p.error : Theme.p.muted
                                        font.family: Theme.uiFont
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }
                    }
                }

                // ---------- the talk: its questions, the user's answers and words on the way ----------
                Repeater {
                    objectName: "agentThread"
                    model: Autonomy.thread
                    delegate: Item {
                        required property var modelData
                        width: parent.width
                        height: modelData.who === "you" ? youRow.height : agentRow.height
                        Said { id: youRow; visible: modelData.who === "you"; text: modelData.text }
                        Told {
                            id: agentRow
                            visible: modelData.who !== "you"
                            text: modelData.text + (modelData.expired ? "\n(вопрос снят — ответа не было)" : "")
                        }
                    }
                }
                Told {
                    objectName: "agentQuestion"
                    visible: !!(view.task.question && view.task.question.id)
                    framed: true
                    line: Theme.p.warn
                    title: "Агент спрашивает"
                    text: (view.task.question || {}).question || ""
                    Flow {
                        width: parent.width
                        spacing: 6
                        visible: ((view.task.question || {}).options || []).length > 0
                        Repeater {
                            model: (view.task.question || {}).options || []
                            delegate: PlainButton {
                                required property var modelData
                                required property int index
                                objectName: "agentAnswer_" + index
                                text: modelData.label
                                onClicked: Autonomy.answer(view.task.question.id, modelData.label)
                                ToolTip.visible: hovered && !!modelData.description
                                ToolTip.delay: 400
                                ToolTip.text: modelData.description || ""
                            }
                        }
                    }
                    Text {
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: ((view.task.question || {}).options || []).length
                              ? "Выберите ответ или напишите свой в поле внизу — агент ждёт и продолжит после ответа."
                              : "Ответьте в поле внизу — агент ждёт и продолжит после ответа."
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
                Text {
                    visible: !!view.task.detail
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: view.task.detail || ""
                    color: Theme.p.error
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }

                // ---------- the user's own plan ----------
                Column {
                    visible: view.editing
                    width: parent.width
                    spacing: 8
                    Label { text: "Свой план: шаг на строку, «что сделать — как проверить»" }
                    Area { id: planEditor; width: parent.width; height: 140; name: "agentPlanEditor" }
                    Field { id: planReason; width: parent.width; placeholderText: "Почему (агент прочтёт)" }
                    Row {
                        spacing: 8
                        PlainButton {
                            objectName: "agentPlanSave"
                            kind: "primary"
                            iconName: "check"
                            text: "Отдать агенту"
                            onClicked: { Autonomy.editPlan(planEditor.text, planReason.text); view.editing = false }
                        }
                        PlainButton { text: "Отмена"; onClicked: view.editing = false }
                    }
                }

                // ---------- the report ----------
                Told {
                    objectName: "agentReport"
                    visible: !!(view.task.report && view.task.report.summary)
                    framed: true
                    line: (view.task.report || {}).goalMet ? Theme.p.ok : Theme.p.hairline_strong
                    title: "Отчёт · " + ((view.task.report || {}).verdict || "")
                    text: (view.task.report || {}).summary || ""
                    Repeater {
                        model: [{ name: "Сделано", items: (view.task.report || {}).done || [] },
                                { name: "Не сделано", items: (view.task.report || {}).notDone || [] },
                                { name: "Файлы", items: (view.task.report || {}).files || [] }]
                        delegate: Text {
                            required property var modelData
                            visible: modelData.items.length > 0
                            width: parent.width
                            wrapMode: Text.Wrap
                            text: modelData.name + ": " + modelData.items.join("; ")
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }
                    PlainButton {
                        objectName: "agentKeepSkill"
                        visible: !!(view.task.report || {}).goalMet
                        iconName: "check"
                        text: "Сохранить как навык"
                        onClicked: Memory.keepSkill(view.task.id, view.task.goal)
                    }
                }
                Text {
                    visible: !view.task.live
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: "Задача закончена. Новую можно написать в поле внизу."
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }

            // ---------- a new task's «when», by time or on an event ----------
            Rectangle {
                visible: !Autonomy.running && Schedules.draft.when !== "now"
                width: parent.width
                height: schedule.height + 28
                radius: 8
                color: Theme.p.card
                border.color: Theme.p.hairline
                SchedulePanel {
                    id: schedule
                    x: 14
                    y: 14
                    width: parent.width - 28
                    chooser: false
                }
            }
            Text {
                objectName: "agentError"
                visible: text !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: Autonomy.error
                color: Theme.p.error
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
    }

    AgentComposer {
        id: composer
        anchors.bottom: parent.bottom
        width: parent.width
        height: implicitHeight
        onEditPlanRequested: { planEditor.text = view.task.planText; view.editing = true }
    }
}
