import QtQuick
import QtQuick.Controls.Basic

// The Agent mode's main area (D40): a new task's form - the goal, what "done" means, a template, the model and the
// planner, the projects, how much on its own, the limits - or the open task: its goal and state, the plan as it goes,
// the agent's question with an answer field, pause / go on / stop, the user's own plan, and the report.
// Its approvals come as the card over this view, as in a chat (D38).
Item {
    id: view
    objectName: "agentView"
    readonly property var task: Autonomy.task
    readonly property bool open: Autonomy.selectedId !== ""
    property bool editing: false
    property real reserveTop: 0  // the approval card's room over the view
    onOpenChanged: editing = false

    component Label: Text {
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Note: Text {
        width: parent ? parent.width : 400
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.25
    }
    component Area: Rectangle {
        id: box
        property alias text: input.text
        property string hint
        property string name
        signal edited(string text)
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
                onEditingFinished: box.edited(text)
            }
        }
    }

    Flickable {
        anchors.fill: parent
        contentHeight: column.height + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}

        Column {
            id: column
            y: 24 + view.reserveTop
            x: (view.width - width) / 2
            width: Math.min(780, view.width - 48)
            spacing: 14

            // ================= a new task =================
            Column {
                objectName: "agentForm"
                visible: !view.open
                width: parent.width
                spacing: 12

                Text {
                    text: "Новая задача для агента"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                }
                Note { text: "Агент сам составит план, выполнит шаги инструментами хаба, проверит каждый и в конце напишет отчёт. Вы видите план и каждый шаг, отвечаете на его вопросы и можете остановить его в любой момент." }
                Row {
                    spacing: 8
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
                            onClicked: Autonomy.applyTemplate(modelData.id)
                        }
                    }
                }
                Label { text: "Цель" }
                Area {
                    width: parent.width
                    name: "agentGoal"
                    hint: "Что агент должен сделать — так же, как вы сказали бы человеку"
                    text: Autonomy.draft.goal || ""
                    onEdited: (text) => Autonomy.setField("goal", text)
                }
                Label { text: "Готово, когда" }
                Field {
                    objectName: "agentDoneWhen"
                    width: parent.width
                    placeholderText: "Например: «все тесты проходят» — по этому агент проверит себя в конце"
                    text: Autonomy.draft.done_when || ""
                    onEditingFinished: Autonomy.setField("done_when", text)
                }
                Row {
                    spacing: 8
                    PlainButton {
                        id: modelButton
                        objectName: "agentModel"
                        iconName: "cpu"
                        text: "Работает: " + (Autonomy.draft.modelName || "")
                        onClicked: { modelMenu.anchor = modelButton; modelMenu.open() }
                    }
                    PlainButton {
                        id: plannerButton
                        iconName: "layers"
                        text: "Планирует: " + (Autonomy.draft.plannerName || "")
                        onClicked: { plannerMenu.anchor = plannerButton; plannerMenu.open() }
                    }
                }
                MenuPopup {
                    id: modelMenu
                    menuWidth: 430
                    items: Autonomy.modelChoices
                    onChosen: (id) => Autonomy.setField("model_id", id)
                }
                MenuPopup {
                    id: plannerMenu
                    menuWidth: 430
                    items: [{ id: "*same", name: "та же модель", icon: "layers" }].concat(Autonomy.modelChoices)
                    onChosen: (id) => Autonomy.setField("planner_model_id", id === "*same" ? "" : id)
                }
                Label { text: "Проекты — агент видит и меняет только их" }
                Flow {
                    width: parent.width
                    spacing: 6
                    Text {
                        visible: Autonomy.projectChoices.length === 0
                        text: "Проектов нет: агент будет работать без файлов проектов (сеть, расчёты)."
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Repeater {
                        model: Autonomy.projectChoices
                        delegate: Chip {
                            required property var modelData
                            iconName: "folder"
                            menu: false
                            text: modelData.name
                            active: modelData.selected
                            onClicked: Autonomy.toggleProject(modelData.id)
                        }
                    }
                }
                Row {
                    spacing: 10
                    Label { text: "Скриптам Python — в сеть"; anchors.verticalCenter: parent.verticalCenter }
                    Segmented {
                        objectName: "agentNetwork"
                        options: [{ id: "no", name: "нельзя" }, { id: "yes", name: "можно" }]
                        value: Autonomy.draft.network ? "yes" : "no"
                        onPicked: (id) => Autonomy.setFlag("network", id === "yes")
                    }
                }
                Label { text: "Сколько сам" }
                Segmented {
                    objectName: "agentAutonomy"
                    options: Autonomy.autonomyOptions
                    value: Autonomy.draft.autonomy || "plan"
                    onPicked: (id) => Autonomy.setField("autonomy", id)
                }
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: (Autonomy.autonomyOptions.find(o => o.id === Autonomy.draft.autonomy) || {}).hint || ""
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Flow {
                    width: parent.width
                    spacing: 12
                    Repeater {
                        model: [{ id: "max_steps", name: "Шагов, не больше" }, { id: "max_minutes", name: "Минут" },
                                { id: "budget_usd", name: "Бюджет, $ (платные)" }, { id: "max_replans", name: "Новых планов" }]
                        delegate: Column {
                            required property var modelData
                            width: 150
                            spacing: 4
                            Label { text: modelData.name }
                            Field {
                                width: parent.width
                                text: Autonomy.draft[modelData.id] || ""
                                onEditingFinished: Autonomy.setField(modelData.id, text)
                            }
                        }
                    }
                }
                SchedulePanel { width: parent.width }
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
                PlainButton {
                    objectName: "agentStart"
                    kind: "primary"
                    iconName: Schedules.draft.when === "now" ? "bolt" : "refresh"
                    text: Schedules.draft.when === "now" ? "Начать" : "Запланировать"
                    onClicked: Schedules.draft.when === "now" ? Autonomy.start() : Schedules.create()
                }
            }

            // ================= the open task =================
            Column {
                objectName: "agentTask"
                visible: view.open
                width: parent.width
                spacing: 12

                Text {
                    objectName: "agentTaskGoal"
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: view.task.goal || ""
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: [view.task.statusLabel, view.task.autonomy ? "сам: " + view.task.autonomy : "", view.task.meta]
                          .filter(s => !!s).join(" · ")
                    color: view.task.status === "waiting" ? Theme.p.warn : view.task.status === "error" ? Theme.p.error : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Row {
                    visible: !!view.task.live && !!view.task.level
                    spacing: 8
                    Text {
                        text: "Самостоятельность"
                        color: Theme.p.muted
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Segmented {
                        objectName: "agentLevel"
                        options: Autonomy.autonomyOptions
                        value: view.task.level || ""
                        onPicked: (id) => Autonomy.setLevel(id)
                    }
                }
                Text {
                    visible: !!view.task.doneWhen
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Готово, когда: " + (view.task.doneWhen || "")
                    color: Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
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
                Flow {
                    width: parent.width
                    spacing: 8
                    PlainButton {
                        objectName: "agentPause"
                        visible: !!view.task.live
                        iconName: view.task.paused ? "bolt" : "stop"
                        text: view.task.paused ? "Продолжить" : "Пауза"
                        onClicked: view.task.paused ? Autonomy.resume() : Autonomy.pause()
                    }
                    PlainButton {
                        objectName: "agentStop"
                        visible: !!view.task.live
                        kind: "danger"
                        iconName: "close"
                        text: "Остановить"
                        onClicked: Autonomy.stop()
                    }
                    PlainButton {
                        objectName: "agentEditPlan"
                        visible: !!view.task.live && view.task.planVersion > 0
                        iconName: "edit"
                        text: "Изменить план"
                        onClicked: { planEditor.text = view.task.planText; view.editing = true }
                    }
                    PlainButton {
                        objectName: "agentLog"
                        iconName: "chat"
                        text: "Весь ход работы"
                        onClicked: Autonomy.openLog()
                    }
                }

                // ---------- the agent's question ----------
                Rectangle {
                    objectName: "agentQuestion"
                    visible: !!(view.task.question && view.task.question.id)
                    width: parent.width
                    height: questionBody.height + 28
                    radius: 8
                    color: Theme.p.card
                    border.color: Theme.p.warn
                    Column {
                        id: questionBody
                        x: 14
                        y: 14
                        width: parent.width - 28
                        spacing: 10
                        Row {
                            spacing: 8
                            Icon { name: "chat"; size: 16; tint: Theme.p.warn; anchors.verticalCenter: parent.verticalCenter }
                            Text {
                                text: "Агент спрашивает"
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.Wrap
                            text: (view.task.question || {}).question || ""
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 14
                        }
                        Area { id: answerBox; width: parent.width; height: 72; name: "agentAnswer"; hint: "Ваш ответ" }
                        PlainButton {
                            objectName: "agentAnswerSend"
                            kind: "primary"
                            iconName: "send"
                            text: "Ответить"
                            onClicked: { Autonomy.answer(view.task.question.id, answerBox.text); answerBox.text = "" }
                        }
                    }
                }

                // ---------- the plan ----------
                Row {
                    spacing: 8
                    SectionTitle { text: view.task.planVersion ? "План · версия " + view.task.planVersion : "План ещё не составлен" }
                    Text {
                        visible: !!view.task.progress
                        text: "· " + (view.task.progress || "")
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Text {
                    visible: !!view.task.planReason
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Почему новый план: " + (view.task.planReason || "")
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Column {
                    objectName: "agentSteps"
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: view.task.steps || []
                        delegate: Item {
                            required property var modelData
                            width: parent.width
                            height: stepBody.height + 12
                            Rectangle {
                                anchors.fill: parent
                                radius: 6
                                color: modelData.status === "running" ? Theme.p.item_selected : "transparent"
                            }
                            Icon {
                                x: 8
                                y: 8
                                name: modelData.icon
                                size: 15
                                tint: modelData.status === "done" ? Theme.p.ok : modelData.status === "failed" ? Theme.p.error
                                      : modelData.status === "running" ? Theme.p.accent : Theme.p.faint
                            }
                            Column {
                                id: stepBody
                                x: 32
                                y: 6
                                width: parent.width - 40
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
                Rectangle {
                    objectName: "agentReport"
                    visible: !!(view.task.report && view.task.report.summary)
                    width: parent.width
                    height: reportBody.height + 28
                    radius: 8
                    color: Theme.p.card
                    border.color: (view.task.report || {}).goalMet ? Theme.p.ok : Theme.p.hairline_strong
                    Column {
                        id: reportBody
                        x: 14
                        y: 14
                        width: parent.width - 28
                        spacing: 8
                        Text {
                            text: "Отчёт · " + ((view.task.report || {}).verdict || "")
                            color: (view.task.report || {}).goalMet ? Theme.p.ok : Theme.p.warn
                            font.family: Theme.uiFont
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.Wrap
                            text: (view.task.report || {}).summary || ""
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                            lineHeight: 1.3
                        }
                        PlainButton {
                            objectName: "agentKeepSkill"
                            visible: !!(view.task.report || {}).goalMet
                            iconName: "check"
                            text: "Сохранить как навык"
                            onClicked: Memory.keepSkill(view.task.id, view.task.goal)
                        }
                        Repeater {
                            model: [{ name: "Сделано", items: (view.task.report || {}).done || [] },
                                    { name: "Не сделано", items: (view.task.report || {}).notDone || [] },
                                    { name: "Файлы", items: (view.task.report || {}).files || [] }]
                            delegate: Text {
                                required property var modelData
                                visible: modelData.items.length > 0
                                width: reportBody.width
                                wrapMode: Text.Wrap
                                text: modelData.name + ": " + modelData.items.join("; ")
                                color: Theme.p.text_soft
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                    }
                }
            }
        }
    }
}
