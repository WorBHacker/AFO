import QtQuick
import QtQuick.Controls.Basic

// Budgets (D12) as cards with a bar and where the warning starts on it; a form to add or change one.
Sheet {
    id: sheet
    objectName: "budgetsSheet"
    title: qsTr("Бюджеты")
    iconName: "wallet"

    property bool saving: false

    function startNew() {
        form.budgetId = -1
        scopes.value = "total:"
        periods.value = "month"
        soft.text = ""
        hard.text = ""
        actions.value = "ask"
        form.error = ""
        form.visible = true
    }
    function edit(card) {
        form.budgetId = card.id
        scopes.value = card.scopeKey
        periods.value = card.period
        soft.text = card.soft
        hard.text = card.hard
        actions.value = card.action
        form.error = ""
        form.visible = true
    }

    Connections {
        target: App
        function onBudgetError(message) { sheet.saving = false; form.error = message; form.visible = true }
        function onBudgetsChanged() { if (sheet.saving) { sheet.saving = false; form.visible = false } }
    }
    onClosed: form.visible = false
    onAboutToShow: App.loadStats()

    Text {
        width: parent.width
        text: qsTr("Считаются только платные модели — локальные бесплатны. Порог даёт уведомление; после лимита платный запрос уходит только с вашего подтверждения или не уходит вовсе.")
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.35
    }

    Text {
        objectName: "forecast"
        width: parent.width
        visible: text !== ""
        text: App.forecastText
        wrapMode: Text.Wrap
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.35
    }

    Repeater {
        model: App.budgetItems
        delegate: Rectangle {
            id: card
            required property var modelData
            width: parent.width
            height: cardColumn.height + 28
            radius: 8
            color: Theme.p.card
            border.color: modelData.state === "hard" ? Theme.p.error_line
                          : modelData.state === "soft" ? Theme.p.warn : Theme.p.hairline
            Column {
                id: cardColumn
                x: 16
                y: 14
                width: parent.width - 32
                spacing: 10
                Item {
                    width: parent.width
                    height: 22
                    Text {
                        text: card.modelData.title
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Rectangle {
                            visible: card.modelData.state !== "ok"
                            height: 20
                            width: badge.implicitWidth + 12
                            radius: 4
                            color: "transparent"
                            border.color: card.modelData.state === "hard" ? Theme.p.error : Theme.p.warn
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                id: badge
                                anchors.centerIn: parent
                                text: card.modelData.state === "hard" ? qsTr("лимит исчерпан") : qsTr("порог пройден")
                                color: card.modelData.state === "hard" ? Theme.p.error : Theme.p.warn
                                font.family: Theme.uiFont
                                font.pixelSize: 11
                            }
                        }
                        IconButton { iconName: "edit"; tip: qsTr("Изменить"); onClicked: sheet.edit(card.modelData) }
                        IconButton { iconName: "trash"; tip: qsTr("Удалить"); onClicked: App.deleteBudget(card.modelData.id) }
                    }
                }
                Row {
                    spacing: 6
                    Text {
                        text: card.modelData.spent
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: card.modelData.limit
                        color: Theme.p.muted
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        anchors.baseline: parent.children[0].baseline
                    }
                }
                Item {
                    width: parent.width
                    height: 6
                    Rectangle { width: parent.width; height: 6; radius: 3; color: Theme.p.hairline }
                    Rectangle {
                        width: parent.width * card.modelData.ratio
                        height: 6
                        radius: 3
                        color: card.modelData.state === "hard" ? Theme.p.error
                               : card.modelData.state === "soft" ? Theme.p.warn : Theme.p.accent
                    }
                    Rectangle {
                        visible: card.modelData.softAt >= 0
                        x: parent.width * card.modelData.softAt - 1
                        y: -3
                        width: 2
                        height: 12
                        radius: 1
                        color: Theme.p.muted
                    }
                }
                Text {
                    text: card.modelData.note
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
        }
    }

    Text {
        visible: App.budgetItems.length === 0 && !form.visible
        width: parent.width
        topPadding: 8
        text: qsTr("Бюджетов пока нет — платные модели ничем не ограничены.")
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    PlainButton {
        visible: !form.visible
        width: parent.width
        iconName: "plus"
        text: qsTr("Добавить бюджет")
        onClicked: sheet.startNew()
    }

    // the form: what it covers, the period, the two limits, what happens after the hard one
    Rectangle {
        id: form
        property int budgetId: -1
        property string error
        visible: false
        width: parent.width
        height: formColumn.height + 32
        radius: 8
        color: Theme.p.card
        border.color: Theme.p.hairline_strong
        Column {
            id: formColumn
            x: 16
            y: 16
            width: parent.width - 32
            spacing: 8
            Text {
                text: form.budgetId >= 0 ? qsTr("Изменить бюджет") : qsTr("Новый бюджет")
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            SectionTitle { text: qsTr("Для чего") }
            Segmented { id: scopes; width: parent.width; options: App.budgetScopes }
            SectionTitle { text: qsTr("Период") }
            Segmented { id: periods; width: parent.width; options: App.budgetPeriods }
            Row {
                width: parent.width
                spacing: 12
                Column {
                    width: (parent.width - 12) / 2
                    spacing: 6
                    SectionTitle { text: qsTr("Предупредить с, $") }
                    Field { id: soft; width: parent.width; placeholderText: qsTr("нет", "no limit"); validator: DoubleValidator { bottom: 0 } }
                }
                Column {
                    width: (parent.width - 12) / 2
                    spacing: 6
                    SectionTitle { text: qsTr("Лимит, $") }
                    Field { id: hard; width: parent.width; placeholderText: qsTr("нет", "no limit"); validator: DoubleValidator { bottom: 0 } }
                }
            }
            SectionTitle { text: qsTr("После лимита"); visible: hard.text !== "" }
            Segmented { id: actions; width: parent.width; options: App.budgetActions; visible: hard.text !== "" }
            Text {
                visible: form.error !== ""
                width: parent.width
                text: form.error
                wrapMode: Text.Wrap
                color: Theme.p.error
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Row {
                topPadding: 6
                spacing: 8
                PlainButton { text: qsTr("Отмена"); onClicked: form.visible = false }
                PlainButton {
                    kind: "primary"
                    text: qsTr("Сохранить")
                    enabled: soft.text !== "" || hard.text !== ""
                    onClicked: {
                        sheet.saving = true
                        form.error = ""
                        App.saveBudget(scopes.value, periods.value, soft.text, hard.text, actions.value)
                    }
                }
            }
        }
    }

    // what the hub saw of each model in 30 days (D11): speed, errors, money, agent tasks
    SectionTitle { visible: App.statsRows.length > 0; text: qsTr("Модели за 30 дней") }
    Column {
        objectName: "modelStats"
        width: parent.width
        spacing: 6
        Repeater {
            model: App.statsRows
            delegate: Column {
                required property var modelData
                width: parent.width
                spacing: 1
                Text {
                    width: parent.width
                    text: modelData.name
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: modelData.speed
                    elide: Text.ElideRight
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Text {
                    width: parent.width
                    text: modelData.more
                    elide: Text.ElideRight
                    color: modelData.warn ? Theme.p.warn : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
        }
    }
}
