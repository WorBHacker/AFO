import QtQuick
import QtQuick.Controls.Basic

// Connections (D13): the hub's API keys, tokens and addresses behind the user's password. Keys go in and never
// come back out - the sheet shows whether one is set and, while open, its last four characters. Closing the sheet
// locks it; ten minutes without a change lock it too.
Sheet {
    id: sheet
    objectName: "connectionsSheet"
    title: "Подключения"
    iconName: "key"
    width: Math.min(600, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Services.load()
    onClosed: Services.lock()

    component Note: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Note {
        text: "Ключи и токены хранятся в диспетчере учётных данных Windows и не выходят из хаба: окно видит только, "
              + "задан ли ключ. Менять их можно по паролю подключений."
    }

    // ---------- the first time: a password ----------
    Column {
        visible: Services.state === "new"
        width: parent.width
        spacing: 8
        SectionTitle { text: "Придумайте пароль подключений" }
        Note { text: "Он нужен, чтобы добавить, заменить или удалить ключ и привязать Telegram. Не короче 6 символов." }
        Field {
            id: first
            objectName: "newPassword"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: "Пароль"
        }
        Field {
            id: again
            objectName: "newPasswordAgain"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: "Ещё раз"
            onAccepted: create.clicked()
        }
        PlainButton {
            id: create
            objectName: "createPassword"
            kind: "primary"
            iconName: "lock"
            text: "Задать пароль"
            enabled: first.text !== "" && !Services.busy
            onClicked: { Services.createPassword(first.text, again.text); first.text = ""; again.text = "" }
        }
    }

    // ---------- locked ----------
    Row {
        visible: Services.state === "locked"
        width: parent.width
        spacing: 8
        Field {
            id: password
            objectName: "unlockPassword"
            width: parent.width - open.width - 8
            echoMode: TextInput.Password
            placeholderText: "Пароль подключений"
            onAccepted: open.clicked()
        }
        PlainButton {
            id: open
            objectName: "unlock"
            kind: "primary"
            iconName: "lock"
            text: "Открыть"
            enabled: password.text !== "" && !Services.busy
            onClicked: { Services.unlock(password.text); password.text = "" }
        }
    }

    // ---------- open ----------
    Row {
        visible: Services.state === "open"
        spacing: 10
        Rectangle { width: 7; height: 7; radius: 3.5; color: Theme.p.ok; anchors.verticalCenter: parent.verticalCenter }
        Text {
            text: "Открыто · закроется через 10 минут без изменений"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        PlainButton { iconName: "lock"; text: "Закрыть"; onClicked: Services.lock() }
    }

    Text {
        objectName: "connectionsError"
        visible: Services.error !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Services.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    // ---------- the connections ----------
    Repeater {
        model: Services.items
        delegate: Column {
            id: entry
            required property var modelData
            width: parent ? parent.width : 400
            spacing: 6
            topPadding: 8

            Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
            Row {
                spacing: 8
                Text {
                    text: entry.modelData.title
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }
                Rectangle {
                    width: 7; height: 7; radius: 3.5
                    color: entry.modelData.set ? Theme.p.ok : Theme.p.faint
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: entry.modelData.status
                    color: Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Note { text: entry.modelData.purpose }
            Text {
                text: "Где взять →"
                color: Theme.p.accent
                font.family: Theme.uiFont
                font.pixelSize: 12
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Services.openSite(entry.modelData.name)
                }
            }
            Row {
                visible: Services.state === "open"
                width: parent.width
                spacing: 8
                Field {
                    id: value
                    objectName: "value_" + entry.modelData.name
                    width: parent.width - save.width - 8
                    echoMode: entry.modelData.secret ? TextInput.Password : TextInput.Normal
                    placeholderText: entry.modelData.set ? "Новое значение, чтобы заменить" : entry.modelData.hint
                    onAccepted: save.clicked()
                }
                PlainButton {
                    id: save
                    objectName: "save_" + entry.modelData.name
                    kind: "primary"
                    text: "Сохранить"
                    enabled: value.text.trim() !== "" && !Services.busy
                    onClicked: { Services.save(entry.modelData.name, value.text); value.text = "" }
                }
            }
            Flow {
                visible: Services.state === "open" && entry.modelData.set
                width: parent.width
                spacing: 8
                PlainButton {
                    iconName: "refresh"
                    text: "Проверить"
                    enabled: !Services.busy
                    onClicked: Services.check(entry.modelData.name)
                }
                PlainButton {
                    kind: "danger"
                    iconName: "trash"
                    text: "Удалить"
                    onClicked: Services.remove(entry.modelData.name)
                }
                PlainButton {
                    objectName: "chooseModels"
                    visible: entry.modelData.choose
                    iconName: "cpu"
                    text: "Модели…"
                    onClicked: Services.openModels(entry.modelData.name)
                }
                PlainButton {
                    visible: entry.modelData.telegram
                    iconName: "chat"
                    text: Services.telegramLinked ? "Привязать другой чат" : "Привязать чат"
                    onClicked: Services.linkTelegram()
                }
                PlainButton {
                    visible: entry.modelData.telegram && Services.telegramLinked
                    text: "Отвязать"
                    onClicked: Services.unlinkTelegram()
                }
            }
            Column {
                objectName: "modelChooser"
                visible: Services.state === "open" && Services.choosing === entry.modelData.name
                width: parent.width
                spacing: 6
                Field {
                    width: parent.width
                    placeholderText: "Поиск: например, deepseek, gpt, qwen coder"
                    onTextChanged: Services.filterModels(text)
                }
                Note { text: Services.choicesNote }
                Repeater {
                    model: Services.choosing === entry.modelData.name ? Services.choices : []
                    delegate: AbstractButton {
                        id: choice
                        required property var modelData
                        width: parent ? parent.width : 400
                        implicitHeight: 40
                        hoverEnabled: true
                        onClicked: Services.toggleModel(modelData.id)
                        background: Rectangle { radius: 6; color: choice.hovered ? Theme.p.item_hover : "transparent" }
                        contentItem: Item {
                            Rectangle {
                                x: 6
                                width: 14
                                height: 14
                                radius: 3
                                anchors.verticalCenter: parent.verticalCenter
                                color: choice.modelData.picked ? Theme.p.accent : "transparent"
                                border.color: choice.modelData.picked ? Theme.p.accent : Theme.p.hairline_strong
                                Icon {
                                    anchors.centerIn: parent
                                    visible: choice.modelData.picked
                                    name: "check"
                                    size: 11
                                    tint: Theme.p.on_accent
                                }
                            }
                            Column {
                                x: 28
                                width: parent.width - 32
                                anchors.verticalCenter: parent.verticalCenter
                                Text {
                                    width: parent.width
                                    elide: Text.ElideRight
                                    text: choice.modelData.name
                                    color: Theme.p.text
                                    font.family: Theme.uiFont
                                    font.pixelSize: 13
                                }
                                Text {
                                    width: parent.width
                                    elide: Text.ElideRight
                                    text: choice.modelData.note
                                    color: Theme.p.faint
                                    font.family: Theme.uiFont
                                    font.pixelSize: 11
                                }
                            }
                        }
                    }
                }
                Row {
                    spacing: 8
                    PlainButton {
                        objectName: "saveModels"
                        kind: "primary"
                        text: "Сохранить выбор (" + Services.pickedCount + ")"
                        enabled: !Services.busy
                        onClicked: Services.saveModels()
                    }
                    PlainButton { text: "Отмена"; onClicked: Services.closeModels() }
                }
            }
            Text {
                visible: entry.modelData.note !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: entry.modelData.note
                color: entry.modelData.noteOk ? Theme.p.ok : Theme.p.error
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Text {
                visible: entry.modelData.telegram && (Services.linkCode !== "" || entry.modelData.set)
                width: parent.width
                wrapMode: Text.Wrap
                textFormat: Text.StyledText
                text: Services.linkCode !== ""
                      ? "Отправьте своему боту в Telegram: <b>/link " + Services.linkCode + "</b> — код действует 10 минут"
                      : Services.telegramLinked ? "Чат привязан: подтверждения приходят и на телефон"
                                                   : "Чат не привязан"
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }

    // ---------- the password itself ----------
    Column {
        visible: Services.state === "open"
        width: parent.width
        spacing: 8
        Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
        SectionTitle { text: "Сменить пароль" }
        Field { id: oldPassword; width: parent.width; echoMode: TextInput.Password; placeholderText: "Текущий пароль" }
        Field { id: newPassword; width: parent.width; echoMode: TextInput.Password; placeholderText: "Новый пароль" }
        Field { id: newAgain; width: parent.width; echoMode: TextInput.Password; placeholderText: "Новый ещё раз" }
        PlainButton {
            text: "Сменить пароль"
            enabled: oldPassword.text !== "" && newPassword.text !== "" && !Services.busy
            onClicked: {
                Services.changePassword(oldPassword.text, newPassword.text, newAgain.text)
                oldPassword.text = ""; newPassword.text = ""; newAgain.text = ""
            }
        }
    }
    Note {
        visible: Services.state !== "new"
        color: Theme.p.faint
        text: "Забыли пароль — в консоли python -m hub connections reset: он сбросится вместе со всеми сохранёнными "
              + "подключениями, их нужно будет ввести заново."
    }
}
