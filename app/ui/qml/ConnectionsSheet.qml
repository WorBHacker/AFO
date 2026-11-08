import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// Connections (D13): the hub's API keys, tokens and addresses behind the user's password. Keys go in and never
// come back out - the sheet shows whether one is set and, while open, its last four characters. A key file (Vertex
// AI's service account, stage 30) is chosen on disk. Closing the sheet locks it; ten minutes without a change lock
// it too.
Sheet {
    id: sheet
    objectName: "connectionsSheet"
    title: qsTr("Подключения")
    iconName: "key"
    width: Math.min(600, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: { Services.load(); Mcp.load(); Mail.load(); AgentKeys.load() }
    onClosed: Services.lock()

    component Note: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Note {
        text: qsTr("Ключи и токены хранятся в диспетчере учётных данных Windows и не выходят из хаба: окно видит только, задан ли ключ. Менять их можно по паролю подключений.")
    }

    // ---------- the first time: a password ----------
    Column {
        visible: Services.state === "new"
        width: parent.width
        spacing: 8
        SectionTitle { text: qsTr("Придумайте пароль подключений") }
        Note { text: qsTr("Он нужен, чтобы добавить, заменить или удалить ключ и привязать Telegram. Не короче 6 символов.") }
        Field {
            id: first
            objectName: "newPassword"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: qsTr("Пароль")
        }
        Field {
            id: again
            objectName: "newPasswordAgain"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: qsTr("Ещё раз")
            onAccepted: create.clicked()
        }
        PlainButton {
            id: create
            objectName: "createPassword"
            kind: "primary"
            iconName: "lock"
            text: qsTr("Задать пароль")
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
            placeholderText: qsTr("Пароль подключений")
            onAccepted: open.clicked()
        }
        PlainButton {
            id: open
            objectName: "unlock"
            kind: "primary"
            iconName: "lock"
            text: qsTr("Открыть")
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
            text: qsTr("Открыто · закроется через 10 минут без изменений")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        PlainButton { iconName: "lock"; text: qsTr("Закрыть"); onClicked: Services.lock() }
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
                    id: entryTitle
                    width: Math.max(0, Math.min(implicitWidth, entry.width * 0.7))
                    elide: Text.ElideRight
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
                    width: Math.max(0, Math.min(implicitWidth, entry.width - entryTitle.width - 23))
                    elide: Text.ElideRight
                    text: entry.modelData.status
                    color: Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Note { text: entry.modelData.purpose }
            Text {
                text: qsTr("Где взять →")
                color: Theme.p.accent
                font.family: Theme.uiFont
                font.pixelSize: 12
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Services.openSite(entry.modelData.name)
                }
            }
            PlainButton {
                objectName: "pickFile_" + entry.modelData.name
                visible: Services.state === "open" && entry.modelData.file
                kind: "primary"
                iconName: "folder"
                text: entry.modelData.set ? qsTr("Заменить файл ключа…") : qsTr("Выбрать файл ключа…")
                enabled: !Services.busy
                onClicked: { keyFile.connection = entry.modelData.name; keyFile.open() }
            }
            Row {
                visible: Services.state === "open" && !entry.modelData.file
                width: parent.width
                spacing: 8
                Field {
                    id: value
                    objectName: "value_" + entry.modelData.name
                    width: parent.width - save.width - 8
                    echoMode: entry.modelData.secret ? TextInput.Password : TextInput.Normal
                    placeholderText: entry.modelData.set ? qsTr("Новое значение, чтобы заменить") : entry.modelData.hint
                    onAccepted: save.clicked()
                }
                PlainButton {
                    id: save
                    objectName: "save_" + entry.modelData.name
                    kind: "primary"
                    text: qsTr("Сохранить")
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
                    text: qsTr("Проверить")
                    enabled: !Services.busy
                    onClicked: Services.check(entry.modelData.name)
                }
                PlainButton {
                    kind: "danger"
                    iconName: "trash"
                    text: qsTr("Удалить")
                    onClicked: Services.remove(entry.modelData.name)
                }
                PlainButton {
                    objectName: "chooseModels"
                    visible: entry.modelData.choose
                    iconName: "cpu"
                    text: qsTr("Модели…")
                    onClicked: Services.openModels(entry.modelData.name)
                }
                PlainButton {
                    visible: entry.modelData.telegram
                    iconName: "chat"
                    text: Services.telegramLinked ? qsTr("Привязать другой чат") : qsTr("Привязать чат")
                    onClicked: Services.linkTelegram()
                }
                PlainButton {
                    visible: entry.modelData.telegram && Services.telegramLinked
                    text: qsTr("Отвязать")
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
                    placeholderText: qsTr("Поиск: например, deepseek, gpt, qwen coder")
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
                        text: qsTr("Сохранить выбор (%1)").arg(Services.pickedCount)
                        enabled: !Services.busy
                        onClicked: Services.saveModels()
                    }
                    PlainButton { text: qsTr("Отмена"); onClicked: Services.closeModels() }
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
                      ? qsTr("Отправьте своему боту в Telegram: <b>/link %1</b> — код действует 10 минут").arg(Services.linkCode)
                      : Services.telegramLinked ? qsTr("Чат привязан: подтверждения приходят и на телефон")
                                                   : qsTr("Чат не привязан")
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }

    FileDialog {
        id: keyFile
        property string connection
        title: qsTr("Файл ключа сервисного аккаунта")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Файл ключа (*.json)")]
        onAccepted: Services.saveFile(connection, selectedFile.toString())
    }

    // ---------- the agent's mailbox (9.4b) ----------
    MailSection { width: parent.width }

    // ---------- the user's API keys for the agent (9.4b) ----------
    KeysSection { width: parent.width }

    // ---------- outside MCP servers (9.5) ----------
    McpSection { width: parent.width }

    // ---------- the password itself ----------
    Column {
        visible: Services.state === "open"
        width: parent.width
        spacing: 8
        Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
        SectionTitle { text: qsTr("Сменить пароль") }
        Field { id: oldPassword; width: parent.width; echoMode: TextInput.Password; placeholderText: qsTr("Текущий пароль") }
        Field { id: newPassword; width: parent.width; echoMode: TextInput.Password; placeholderText: qsTr("Новый пароль") }
        Field { id: newAgain; width: parent.width; echoMode: TextInput.Password; placeholderText: qsTr("Новый ещё раз") }
        PlainButton {
            text: qsTr("Сменить пароль")
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
        text: qsTr("Забыли пароль — в консоли python -m hub connections reset: он сбросится вместе со всеми сохранёнными подключениями, их нужно будет ввести заново.")
    }
}
