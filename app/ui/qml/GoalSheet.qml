import QtQuick
import QtQuick.Controls.Basic

// The goal's contract (D44, 11.3): the wish in the user's words, a draft of the contract by a model (the goal
// compiler), then what is wanted, the criteria the hub checks after each turn, the constraints, the limits with a
// guess of the price, who checks. «Подтвердить и запустить» sets it - and lets the hub run the criteria's commands.
Sheet {
    id: sheet
    objectName: "goalSheet"
    title: "Цель"
    iconName: "target"
    width: Math.min(600, (parent ? parent.width : 960) * 0.92)
    readonly property var ed: Goal.editor
    readonly property bool filled: ed.drafted || (ed.criteria || []).length > 0 || (ed.objective || "") !== ""

    function save() {  // what is typed and not yet given back
        Goal.setField("wish", wishBox.area.text)
        if (filled) {
            Goal.setField("objective", objective.text)
            Goal.setField("constraints", constraintsBox.area.text)
        }
    }
    onClosed: if (!Goal.starting) save()  // the card keeps what was typed for the next time

    component Hint: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    component Box: Rectangle {
        property alias area: inner
        width: parent.width
        height: Math.min(160, Math.max(64, inner.implicitHeight + 4))
        radius: 6
        color: Theme.p.card
        border.color: inner.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            anchors.margins: 2
            ScrollBar.vertical: ThinScrollBar {}
            TextArea {
                id: inner
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                placeholderTextColor: Theme.p.faint
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
                padding: 8
                background: null
            }
        }
    }

    SectionTitle { text: "Чего добиться" }
    Box {
        id: wishBox
        objectName: "goalWish"
        area.text: sheet.ed.wish || ""
        area.placeholderText: "Своими словами: например, «пусть тесты в src проходят» или «вынеси расчёт цен в отдельный модуль»"
    }
    Row {
        spacing: 10
        PlainButton {
            objectName: "goalDraft"
            kind: sheet.filled ? "plain" : "primary"
            iconName: "sparkle"
            enabled: !Goal.drafting
            text: Goal.drafting ? "Составляю…" : (sheet.filled ? "Составить заново" : "Составить договор")
            onClicked: { Goal.setField("wish", wishBox.area.text); Goal.draft() }
        }
        PlainButton {
            objectName: "goalManual"
            visible: !sheet.filled
            text: "Заполнить самому"
            onClicked: { Goal.setField("wish", wishBox.area.text); Goal.setField("objective", wishBox.area.text);
                         Goal.addCriterion("command") }
        }
    }
    Hint {
        text: "Модель предложит, что проверять командой (тесты, сборка, файл на месте) и что словами. Составляет: «"
              + Goal.drafterName + "»"
    }

    Column {
        objectName: "goalContract"
        visible: sheet.filled
        width: parent.width
        spacing: 10

        SectionTitle { text: "Цель" }
        Field {
            id: objective
            objectName: "goalObjective"
            width: parent.width
            text: sheet.ed.objective || ""
            onEditingFinished: Goal.setField("objective", text)
        }

        SectionTitle { text: "Готово, когда — хаб проверяет после каждого хода" }
        Repeater {
            model: (sheet.ed.criteria || []).length  // by count: a change of one row does not remake the others
            delegate: Item {
                id: crit
                required property int index
                readonly property var c: (sheet.ed.criteria || [])[index] || ({})
                width: parent.width
                height: 34
                Field {
                    objectName: "goalCriterion_" + crit.index
                    width: parent.width - (crit.c.kind === "command" ? project.width + code.width + 16 : 0) - 38
                    font.family: crit.c.kind === "command" ? Theme.monoFont : Theme.uiFont
                    placeholderText: crit.c.kind === "command" ? "команда PowerShell, например: pytest -q"
                                                               : "что должно быть правдой — проверит модель"
                    text: crit.c.kind === "command" ? (crit.c.command || "") : (crit.c.rule || "")
                    onEditingFinished: Goal.setCriterion(crit.index, crit.c.kind === "command" ? "command" : "rule", text)
                }
                PlainButton {
                    id: project
                    visible: crit.c.kind === "command"
                    x: parent.width - code.width - width - 46
                    implicitHeight: 34
                    text: crit.c.project || "проект?"
                    onClicked: {
                        const names = Goal.projectNames
                        if (names.length)
                            Goal.setCriterion(crit.index, "project", names[(names.indexOf(crit.c.project) + 1) % names.length])
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: Goal.projectNames.length > 1 ? "Где запускать. Щелчок — другой проект чата"
                                                               : "Где запускать: проект чата"
                }
                Field {
                    id: code
                    objectName: "goalCode_" + crit.index
                    visible: crit.c.kind === "command"
                    x: parent.width - width - 38
                    width: 56
                    horizontalAlignment: TextInput.AlignHCenter
                    text: crit.c.code || "0"
                    onEditingFinished: Goal.setCriterion(crit.index, "code", text)
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: "Код выхода, который значит «готово» (обычно 0)"
                }
                IconButton {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "trash"
                    tip: "Убрать критерий"
                    onClicked: Goal.removeCriterion(crit.index)
                }
            }
        }
        Row {
            spacing: 8
            PlainButton { objectName: "goalAddCommand"; iconName: "terminal"; text: "Команда"; onClicked: Goal.addCriterion("command") }
            PlainButton { objectName: "goalAddRule"; iconName: "chat"; text: "Правило словами"; onClicked: Goal.addCriterion("semantic") }
        }
        Hint { text: "Команда только смотрит: устанавливать, скачивать, отправлять и удалять ею нельзя. Правило словами проверит модель «Проверка цели»." }

        SectionTitle { text: "Нельзя — по правилу на строку" }
        Box {
            id: constraintsBox
            area.text: sheet.ed.constraints || ""
            area.placeholderText: "например: не трогать папку migrations"
        }

        SectionTitle { text: "Пределы" }
        Row {
            spacing: 10
            Repeater {
                model: [{ id: "turns", label: "ходов" }, { id: "tokens", label: "тыс. токенов" }, { id: "minutes", label: "минут" }]
                delegate: Row {
                    required property var modelData
                    spacing: 6
                    Field {
                        objectName: "goalLimit_" + modelData.id
                        width: 70
                        horizontalAlignment: TextInput.AlignHCenter
                        text: sheet.ed[modelData.id] || ""
                        onEditingFinished: Goal.setField(modelData.id, text)
                    }
                    Text {
                        text: modelData.label
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
        }
        Hint { text: (Goal.cost ? Goal.cost + ". " : "") + "На 88 % любого предела модель получит последний ход: довести файлы до рабочего состояния и написать, что осталось." }

        SectionTitle { text: "Проверяет" }
        Chip {
            id: judgeChip
            objectName: "goalJudge"
            iconName: "shield"
            text: Goal.judgeName
            onClicked: { judgeMenu.anchor = judgeChip; judgeMenu.open() }
        }
        Hint { text: "Работает «" + Goal.workerName + "» с правами этого чата. Проверяющая модель только читает: сверяет сделанное с договором и словам работающей не верит." }

        Text {
            visible: (sheet.ed.notes || []).length > 0
            width: parent.width
            wrapMode: Text.Wrap
            text: (sheet.ed.notes || []).join("\n")
            color: Theme.p.warn
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }

    Text {
        objectName: "goalError"
        visible: Goal.error !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Goal.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Row {
        spacing: 10
        visible: sheet.filled
        PlainButton {
            id: startButton
            objectName: "goalStart"
            kind: "primary"
            iconName: "play"
            enabled: !Goal.starting && !Goal.drafting
            text: Goal.starting ? "Запускаю…" : "Подтвердить и запустить"
            onClicked: { startButton.forceActiveFocus(); sheet.save(); Goal.start() }
        }
        PlainButton { text: "Отмена"; onClicked: sheet.close() }
    }
    Hint {
        visible: sheet.filled
        text: "Запуск разрешает хабу выполнять команды критериев после каждого хода. Писать модели можно и пока она работает: уточнение она прочтёт в начале следующего хода."
    }

    MenuPopup {
        id: judgeMenu
        menuWidth: 380
        wrapNotes: true
        items: Goal.judgeItems
        onChosen: (id) => Goal.setField("judge", id)
    }

    Connections {
        target: Goal
        function onStarted() { sheet.close() }
    }
}
