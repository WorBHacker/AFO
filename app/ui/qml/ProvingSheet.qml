import QtQuick
import QtQuick.Controls.Basic

// The proving ground and the models' exam (D45, 12.2): the tasks - «Попробовать» with the chat's model opens the
// run's chat; the tries; the exam - models and tasks chosen, the judge or not, its progress and its table
// «tasks × models» (a cell opens that run's chat).
Sheet {
    id: sheet
    objectName: "provingSheet"
    title: qsTr("Полигон")
    iconName: "flask"
    width: Math.min(820, (parent ? parent.width : 960) * 0.94)

    component Hint: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    component Line: Text {
        color: Theme.p.text
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    component Tick: AbstractButton {
        id: tick
        property bool on: false
        implicitHeight: 28
        readonly property real room: parent && parent.width > 0 ? parent.width : Infinity
        implicitWidth: tickRow.implicitWidth + 8
        hoverEnabled: true
        contentItem: Row {
            id: tickRow
            spacing: 8
            Rectangle {
                width: 16
                height: 16
                radius: 4
                anchors.verticalCenter: parent.verticalCenter
                color: tick.on ? Theme.p.accent : "transparent"
                border.color: tick.on ? Theme.p.accent : Theme.p.hairline_strong
                Icon { anchors.centerIn: parent; visible: tick.on; name: "check"; size: 12; tint: Theme.p.on_accent }
            }
            Text {
                width: Math.max(0, Math.min(implicitWidth, tick.room - 32))
                elide: Text.ElideRight
                text: tick.text
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    Hint {
        text: qsTr("Готовые задания на свежей копии учебного проекта: модель работает в режиме «Цель» и может там всё, а хаб проверяет результат — в том числе скрытыми проверками, которых модель не видит. Свои проекты не трогаются.")
    }

    // ---------- the tasks ----------
    SectionTitle { width: parent.width; elide: Text.ElideRight; text: qsTr("Задания — пробует «%1» (модель чата)").arg(Proving.workerName) }
    Repeater {
        model: Proving.tasks
        delegate: Rectangle {
            required property var modelData
            required property int index
            width: parent.width
            height: taskBody.height + 20
            radius: 8
            color: Theme.p.card
            border.color: Theme.p.hairline
            Column {
                id: taskBody
                x: 14
                y: 10
                width: parent.width - tryButton.width - 40
                spacing: 4
                Row {
                    spacing: 8
                    Line { text: modelData.title; font.weight: Font.DemiBold }
                    Text {
                        text: modelData.level
                        color: modelData.grade === "hard" ? Theme.p.warn : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Hint { text: modelData.about }
                Hint { text: modelData.limit + " · " + modelData.checks }
            }
            PlainButton {
                id: tryButton
                objectName: "provingTry_" + modelData.id
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                iconName: "play"
                text: qsTr("Попробовать")
                enabled: !Proving.busy
                onClicked: { sheet.close(); Proving.tryTask(modelData.id) }
            }
        }
    }

    // ---------- the tries ----------
    SectionTitle { visible: Proving.tries.length > 0; text: qsTr("Пробы") }
    Repeater {
        model: Proving.tries
        delegate: Item {
            required property var modelData
            width: parent.width
            height: 30
            Line {
                width: parent.width - tryButtons.width - 8
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: modelData.title + " · " + modelData.model + " — " + modelData.state
                color: modelData.status === "passed" ? Theme.p.ok : modelData.status === "failed" ? Theme.p.warn : Theme.p.text
            }
            Row {
                id: tryButtons
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                PlainButton { implicitHeight: 26; text: qsTr("Ход работы"); onClicked: { sheet.close(); Proving.openRun(modelData.conversation) } }
                IconButton { iconName: "trash"; tip: qsTr("Удалить пробу вместе с копией"); onClicked: Proving.deleteRun(modelData.id) }
            }
        }
    }

    // ---------- the exam ----------
    SectionTitle { text: qsTr("Экзамен моделей") }
    Hint { text: qsTr("Отмеченные задания на отмеченных моделях, по очереди, каждое на своей свежей копии. Сдано — цель достигнута и все скрытые проверки прошли.") }
    Flow {
        width: parent.width
        spacing: 10
        Repeater {
            model: Proving.models
            delegate: Tick {
                required property var modelData
                objectName: "examModel_" + index
                required property int index
                on: modelData.chosen
                text: modelData.name
                onClicked: Proving.toggleModel(modelData.id)
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: modelData.note
            }
        }
    }
    Flow {
        width: parent.width
        spacing: 10
        Repeater {
            model: Proving.tasks
            delegate: Tick {
                required property var modelData
                on: modelData.chosen
                text: modelData.title
                onClicked: Proving.toggleTask(modelData.id)
            }
        }
    }
    Tick {
        objectName: "examJudge"
        on: Proving.judge
        text: qsTr("Проверяющая модель тоже (роль «Проверка цели»)")
        onClicked: Proving.setJudge(!Proving.judge)
    }
    Row {
        spacing: 10
        PlainButton {
            objectName: "examStart"
            kind: "primary"
            iconName: "play"
            text: qsTr("Начать экзамен")
            enabled: !Proving.busy && !Proving.examGoing
            onClicked: Proving.startExam()
        }
        PlainButton {
            objectName: "examStop"
            visible: Proving.examGoing
            kind: "danger"
            text: qsTr("Остановить")
            onClicked: Proving.stopExam()
        }
    }

    // ---------- the table ----------
    Column {
        id: examTable
        objectName: "examTable"
        visible: Proving.exam.id !== undefined
        width: parent.width
        spacing: 8
        Row {
            spacing: 10
            Line { text: qsTr("Экзамен %1").arg(Proving.exam.when || ""); font.weight: Font.DemiBold }
            Text {
                text: Proving.exam.state || ""
                color: Proving.exam.status === "running" ? Theme.p.accent : Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Flickable {
            width: parent.width
            height: grid.height
            contentWidth: grid.width
            flickableDirection: Flickable.HorizontalFlick
            clip: true
            ScrollBar.horizontal: ThinScrollBar {}
            Column {
                id: grid
                spacing: 2
                Row {
                    spacing: 2
                    Item { width: 190; height: 44 }
                    Repeater {
                        model: Proving.exam.heads || []
                        delegate: Rectangle {
                            required property var modelData
                            width: 170
                            height: 44
                            radius: 4
                            color: Theme.p.item_hover
                            Column {
                                x: 8
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 16
                                Text { width: parent.width; elide: Text.ElideRight; text: modelData.name; color: Theme.p.text; font.family: Theme.uiFont; font.pixelSize: 12; font.weight: Font.DemiBold }
                                Text { text: modelData.score + " · " + modelData.spent; color: Theme.p.text_soft; font.family: Theme.uiFont; font.pixelSize: 11 }
                            }
                        }
                    }
                }
                Repeater {
                    model: Proving.exam.rows || []
                    delegate: Row {
                        required property var modelData
                        spacing: 2
                        Rectangle {
                            width: 190
                            height: 44
                            radius: 4
                            color: Theme.p.card
                            Text { x: 8; width: parent.width - 16; anchors.verticalCenter: parent.verticalCenter; elide: Text.ElideRight; text: modelData.title; color: Theme.p.text; font.family: Theme.uiFont; font.pixelSize: 12 }
                        }
                        Repeater {
                            model: modelData.cells
                            delegate: AbstractButton {
                                id: box
                                required property var modelData
                                width: 170
                                height: 44
                                hoverEnabled: true
                                enabled: modelData.conversation !== ""
                                onClicked: { sheet.close(); Proving.openRun(modelData.conversation) }
                                background: Rectangle {
                                    radius: 4
                                    color: box.hovered ? Theme.p.item_hover : Theme.p.card
                                    border.color: box.modelData.status === "passed" ? Theme.p.ok
                                                  : box.modelData.status === "failed" ? Theme.p.warn : Theme.p.hairline
                                }
                                contentItem: Row {
                                    leftPadding: 8
                                    spacing: 6
                                    Text {
                                        text: box.modelData.mark
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: box.modelData.status === "passed" ? Theme.p.ok : Theme.p.warn
                                        font.family: Theme.uiFont
                                        font.pixelSize: 15
                                    }
                                    Text {
                                        width: 136
                                        anchors.verticalCenter: parent.verticalCenter
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                        text: box.modelData.text
                                        color: Theme.p.text_soft
                                        font.family: Theme.uiFont
                                        font.pixelSize: 11
                                    }
                                }
                                ToolTip.visible: hovered && box.modelData.why !== ""
                                ToolTip.delay: 300
                                ToolTip.text: qsTr("%1\nЩелчок — ход работы").arg(box.modelData.why)
                            }
                        }
                    }
                }
            }
        }
        // why a run did not pass, without hovering a cell; a line opens that run's chat
        Repeater {
            model: Proving.exam.fails || []
            delegate: AbstractButton {
                id: fail
                required property var modelData
                width: parent.width
                implicitHeight: failText.implicitHeight + 6
                hoverEnabled: true
                enabled: modelData.conversation !== ""
                onClicked: { sheet.close(); Proving.openRun(modelData.conversation) }
                contentItem: Text {
                    id: failText
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    text: "✗ " + fail.modelData.text + " — " + fail.modelData.why
                    color: fail.hovered ? Theme.p.text : Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
        }
        Row {
            spacing: 10
            Repeater {
                model: Proving.exams.length > 1 ? Proving.exams : []
                delegate: PlainButton {
                    required property var modelData
                    implicitHeight: 26
                    maxWidth: 220
                    text: modelData.name
                    kind: modelData.selected ? "primary" : "plain"
                    onClicked: Proving.showExam(modelData.id)
                }
            }
        }
        PlainButton {
            visible: Proving.exam.status !== "running"
            kind: "danger"
            iconName: "trash"
            text: qsTr("Удалить этот экзамен")
            onClicked: Proving.deleteExam()
        }
    }

    Connections {
        target: Proving
        function onOpenRequested() { sheet.open() }
        function onExamShown() { Qt.callLater(() => sheet.reveal(examTable)) }
    }
}
