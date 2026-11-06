import QtQuick
import QtQuick.Controls.Basic

// The Agent mode's input, as in a chat (2026-10-28): the goal of a new task is typed here, and its mode, model,
// projects, «when» and limits are chips under it - each choice explained in its menu. While a task runs, the same
// input writes to the agent: the answer to its question, or another limit it reads before its next step; the mode
// chip changes the running task's mode, and pause / the plan / the whole course of work / stop sit beside it.
Item {
    id: composer
    objectName: "agentComposer"
    signal editPlanRequested()
    readonly property int column: Math.min(780, width - 48)
    readonly property bool running: Autonomy.running
    readonly property var task: Autonomy.task
    readonly property bool now: Schedules.draft.when === "now"
    property alias text: area.text
    implicitHeight: card.y + card.height + hint.height + 18

    readonly property var whenNotes: ({
        now: qsTr("Агент начнёт сразу, как вы нажмёте Enter"),
        time: qsTr("Каждый день, по дням недели или каждые N часов — настройка появится над полем"),
        event: qsTr("Новый файл в папке, сообщение боту, новое письмо — настройка появится над полем")
    })

    function focusInput() { area.forceActiveFocus() }
    function submit() {
        if (area.text.trim() !== "" && Autonomy.submit(area.text)) area.text = ""
    }
    function useTemplate(id) {
        Autonomy.applyTemplate(id)
        area.text = Autonomy.draft.goal || ""
        area.cursorPosition = area.length
        area.forceActiveFocus()
    }

    Connections {
        target: Autonomy
        function onGoalBack(text) { if (area.text === "") area.text = text }  // the task did not start
    }
    Connections {
        target: Schedules
        function onDraftChanged() { if (Schedules.error && area.text === "") area.text = Autonomy.draft.goal || "" }
    }

    // ---------- the menus ----------
    MenuPopup {
        id: modeMenu
        objectName: "agentModeMenu"
        menuWidth: 440
        wrapNotes: true
        items: Autonomy.modes
        onChosen: (id) => Autonomy.chooseMode(id)
    }
    MenuPopup {
        id: modelMenu
        menuWidth: 430
        items: Autonomy.modelChoices.map(m => m.section ? m : Object.assign({}, m, { selected: m.id === Autonomy.draft.model_id }))
        onChosen: (id) => Autonomy.setField("model_id", id)
    }
    MenuPopup {
        id: plannerMenu
        menuWidth: 430
        items: [{ section: qsTr("Кто составляет план"), id: "" }, { id: "*same", name: qsTr("та же модель"), icon: "layers",
                 selected: !Autonomy.draft.planner_model_id }]
               .concat(Autonomy.modelChoices.map(m => m.section ? m : Object.assign({}, m, { selected: m.id === Autonomy.draft.planner_model_id })))
        onChosen: (id) => Autonomy.setField("planner_model_id", id === "*same" ? "" : id)
    }
    MenuPopup {
        id: projectMenu
        menuWidth: 360
        items: Autonomy.projectItems
        onChosen: (id) => { if (id) Autonomy.toggleProject(id) }
    }
    MenuPopup {
        id: refusalMenu
        objectName: "agentRefusalMenu"
        menuWidth: 420
        wrapNotes: true
        items: Autonomy.refusalItems
        onChosen: (id) => Autonomy.chooseRefusal(id)
    }
    MenuPopup {
        id: whenMenu
        menuWidth: 400
        wrapNotes: true
        items: [{ section: qsTr("Когда начать"), id: "" }].concat(Schedules.whenOptions.map(o => ({
            id: o.id, name: o.name, note: composer.whenNotes[o.id] || "", selected: o.id === Schedules.draft.when,
            icon: o.id === "now" ? "bolt" : o.id === "time" ? "clock" : "activity" })))
        onChosen: (id) => Schedules.setField("when", id)
    }

    // the limits, each explained: where the agent stops, what it checks itself by, who plans, the scripts' network
    Popup {
        id: limits
        objectName: "agentLimitsPanel"
        // above the card, no taller than the room up to the window's top: a low window scrolls its content
        // (2026-11-05: in a window 600 high it went 236 px past the top)
        property real room: 600
        parent: card
        x: 0
        y: -height - 8
        width: Math.min(480, card.width)
        height: Math.min(implicitHeight, room)
        padding: 16
        onAboutToShow: room = Math.max(160, card.mapToItem(null, 0, 0).y - 16)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        background: Rectangle {
            radius: 8
            color: Theme.p.popup
            border.color: Theme.p.hairline_strong
            SoftShadow {}
        }
        component Hint: Text {
            width: parent ? parent.width : 300
            wrapMode: Text.Wrap
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        component Name: Text {
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        contentItem: Flickable {
            implicitHeight: limitRows.height
            contentHeight: limitRows.height
            interactive: contentHeight > height
            clip: interactive
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ThinScrollBar {}
            Column {
                id: limitRows
                width: parent.width
                spacing: 12
                Text {
                    text: qsTr("Ограничения задачи")
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }
                Hint { text: qsTr("Упрётся в любое — остановится и напишет, что успел. Можно оставить как есть.") }
                Column {
                    width: parent.width
                    spacing: 4
                    Name { text: Autonomy.limitFields[0].name }
                    Field {
                        objectName: "agentLimit_done_when"
                        width: parent.width
                        placeholderText: qsTr("например: все тесты проходят")
                        text: Autonomy.draft.done_when || ""
                        onEditingFinished: Autonomy.setField("done_when", text)
                    }
                    Hint { text: Autonomy.limitFields[0].hint }
                }
                Grid {
                    id: numbers
                    width: parent.width
                    columns: 2
                    columnSpacing: 16
                    rowSpacing: 10
                    Repeater {
                        model: Autonomy.limitFields.slice(1)
                        delegate: Column {
                            required property var modelData
                            width: (numbers.width - numbers.columnSpacing) / 2
                            spacing: 4
                            Name { text: modelData.name }
                            Field {
                                objectName: "agentLimit_" + modelData.id
                                width: 110
                                text: Autonomy.draft[modelData.id] || ""
                                onEditingFinished: Autonomy.setField(modelData.id, text)
                            }
                            Hint { text: modelData.hint }
                        }
                    }
                }
                Column {
                    width: parent.width
                    spacing: 4
                    Name { text: qsTr("Кто планирует") }
                    PlainButton {
                        id: plannerButton
                        objectName: "agentPlanner"
                        iconName: "layers"
                        text: Autonomy.draft.plannerName || ""
                        onClicked: { plannerMenu.anchor = plannerButton; plannerMenu.open() }
                    }
                    Hint { text: qsTr("Модель, которая составляет и пересматривает план. Может быть сильнее той, что выполняет шаги.") }
                }
                Column {
                    width: parent.width
                    spacing: 4
                    Name { text: qsTr("Скриптам Python — в сеть") }
                    Segmented {
                        objectName: "agentNetwork"
                        options: [{ id: "no", name: qsTr("нельзя") }, { id: "yes", name: qsTr("можно") }]
                        value: Autonomy.draft.network ? "yes" : "no"
                        onPicked: (id) => Autonomy.setFlag("network", id === "yes")
                    }
                    Hint { text: qsTr("Скрипты, которые агент запускает в песочнице, получат доступ в интернет. Обычно не нужно: сайты агент читает и без этого.") }
                }
                Column {
                    width: parent.width
                    spacing: 4
                    Name { text: qsTr("Облачной модели — окна программ") }
                    Segmented {
                        objectName: "agentScreenCloud"
                        options: [{ id: "no", name: qsTr("нельзя") }, { id: "yes", name: qsTr("можно") }]
                        value: Autonomy.draft.screenCloud ? "yes" : "no"
                        onPicked: (id) => Autonomy.setFlag("screen_cloud", id === "yes")
                    }
                    Hint { text: qsTr("Облачная модель прочтёт надписи и поля в окнах разрешённых программ — только в этой задаче. Снимки экрана видит только локальная модель. Сначала — как в «Папках и сайтах для агента».") }
                }
            }
        }
    }

    Rectangle {
        id: card
        x: (parent.width - composer.column) / 2
        y: 4
        width: composer.column
        height: input.height + controls.height + 22
        radius: 8
        color: Theme.p.card
        border.color: area.activeFocus ? Theme.p.hairline_strong : Theme.p.hairline

        ScrollView {
            id: input
            x: 6
            y: 6
            width: parent.width - 12
            height: Math.min(200, Math.max(44, area.implicitHeight))
            ScrollBar.vertical: ThinScrollBar {}
            TextArea {
                id: area
                objectName: "agentInput"
                wrapMode: TextArea.Wrap
                placeholderText: Autonomy.placeholder
                placeholderTextColor: Theme.p.faint
                color: Theme.p.text
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                leftPadding: 10
                rightPadding: 10
                topPadding: 8
                bottomPadding: 8
                background: null
                Keys.onPressed: (event) => {
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !(event.modifiers & Qt.ShiftModifier)) {
                        event.accepted = true
                        composer.submit()
                    }
                }
            }
        }

        Item {
            id: controls
            x: 10
            y: input.y + input.height + 4
            width: parent.width - 20
            height: chips.height + 6
            Flow {
                id: chips
                width: parent.width - buttons.width - 12
                spacing: 6
                anchors.verticalCenter: parent.verticalCenter
                Chip {
                    id: modeChip
                    objectName: "agentMode"
                    iconName: "shield"
                    text: Autonomy.modeName
                    onClicked: { modeMenu.anchor = modeChip; modeMenu.open() }
                    ToolTip.visible: hovered && !modeMenu.visible
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Режим — кто что решает: %1").arg(Autonomy.modeHint)
                }
                // ---------- a new task ----------
                Chip {
                    id: modelChip
                    objectName: "agentModel"
                    visible: !composer.running
                    iconName: "cpu"
                    text: Autonomy.draft.modelName || ""
                    onClicked: { modelMenu.anchor = modelChip; modelMenu.open() }
                    ToolTip.visible: hovered && !modelMenu.visible
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Модель, которая выполняет шаги задачи")
                }
                Chip {
                    id: projectChip
                    objectName: "agentProjects"
                    visible: !composer.running
                    iconName: Autonomy.draft.project_ids && Autonomy.draft.project_ids.length ? "folder" : "folder-plus"
                    active: !!(Autonomy.draft.project_ids && Autonomy.draft.project_ids.length)
                    text: Autonomy.projectsText
                    onClicked: { projectMenu.anchor = projectChip; projectMenu.open() }
                    ToolTip.visible: hovered && !projectMenu.visible
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Проекты, файлы которых агент видит и может менять. Без проектов — работает с сетью, расчётами и программами")
                }
                Chip {
                    id: refusalChip
                    objectName: "agentRefusal"
                    visible: !composer.running && Autonomy.refusalShown
                    iconName: "key"
                    text: Autonomy.refusalName
                    onClicked: { refusalMenu.anchor = refusalChip; refusalMenu.open() }
                    ToolTip.visible: hovered && !refusalMenu.visible
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Вектор Strata, который убирает у модели отказы отвечать, — для этой задачи")
                }
                Chip {
                    id: whenChip
                    objectName: "agentWhen"
                    visible: !composer.running
                    iconName: composer.now ? "bolt" : "clock"
                    active: !composer.now
                    text: (Schedules.whenOptions.find(o => o.id === Schedules.draft.when) || {}).name || ""
                    onClicked: { whenMenu.anchor = whenChip; whenMenu.open() }
                }
                Chip {
                    objectName: "agentLimits"
                    visible: !composer.running
                    iconName: "sliders"
                    text: Autonomy.limitsText
                    onClicked: limits.visible ? limits.close() : limits.open()
                    ToolTip.visible: hovered && !limits.visible
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Ограничения: шаги, время, бюджет, «готово, когда», кто планирует")
                }
                // ---------- a running task ----------
                Chip {
                    objectName: "agentPause"
                    visible: composer.running
                    menu: false
                    iconName: composer.task.paused ? "play" : "pause"
                    text: composer.task.paused ? qsTr("Продолжить") : qsTr("Пауза")
                    onClicked: composer.task.paused ? Autonomy.resume() : Autonomy.pause()
                    ToolTip.visible: hovered
                    ToolTip.delay: 500
                    ToolTip.text: composer.task.paused ? qsTr("Агент продолжит с того места, где остановился")
                                                       : qsTr("Агент доделает текущее действие и подождёт")
                }
                Chip {
                    objectName: "agentEditPlan"
                    visible: composer.running && composer.task.planVersion > 0
                    menu: false
                    iconName: "edit"
                    text: qsTr("Изменить план")
                    onClicked: composer.editPlanRequested()
                }
                Chip {
                    objectName: "agentLog"
                    visible: !!composer.task.id
                    menu: false
                    iconName: "chat"
                    text: qsTr("Весь ход работы")
                    onClicked: Autonomy.openLog()
                    ToolTip.visible: hovered
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Каждый ход модели и каждый вызов инструмента — как чат")
                }
            }
            Row {
                id: buttons
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 3
                spacing: 8
                AbstractButton {
                    id: stop
                    objectName: "agentStop"
                    visible: composer.running
                    width: 32
                    height: 32
                    hoverEnabled: true
                    onClicked: Autonomy.stop()
                    background: Rectangle {
                        radius: 6
                        color: stop.hovered ? Theme.p.item_hover : "transparent"
                        border.color: Theme.p.error_line
                    }
                    contentItem: Item { Icon { anchors.centerIn: parent; name: "stop"; tint: Theme.p.error } }
                    ToolTip.visible: hovered
                    ToolTip.delay: 500
                    ToolTip.text: qsTr("Остановить задачу")
                }
                AbstractButton {
                    id: send
                    objectName: "agentSend"
                    width: 32
                    height: 32
                    hoverEnabled: true
                    enabled: area.text.trim() !== ""
                    onClicked: composer.submit()
                    background: Rectangle {
                        radius: 6
                        color: send.enabled ? (send.hovered ? Theme.p.accent_hover : Theme.p.accent) : Theme.p.item_hover
                    }
                    contentItem: Item {
                        Icon { anchors.centerIn: parent; name: "send"; tint: send.enabled ? Theme.p.on_accent : Theme.p.faint }
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 500
                    ToolTip.text: composer.running ? qsTr("Написать агенту (Enter)") : composer.now ? qsTr("Начать задачу (Enter)")
                                                                                                 : qsTr("Запланировать (Enter)")
                }
            }
        }
    }

    // what the chosen mode means, always in sight
    Text {
        id: hint
        objectName: "agentModeHint"
        x: card.x + 4
        y: card.y + card.height + 6
        width: card.width - 8
        wrapMode: Text.Wrap
        text: qsTr("«%1» — %2").arg(Autonomy.modeName).arg(Autonomy.modeHint)
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
