import QtQuick
import QtQuick.Controls.Basic

// The agent's mailbox (9.4b, Q21), in Connections: any mailbox by IMAP and SMTP with an app password. The agent reads
// letters (a local model only) and sends one only after the user's «yes» on its card, every time.
Column {
    id: section
    objectName: "mailSection"
    spacing: 8
    readonly property bool unlocked: Services.state === "open"
    property bool custom: false

    component Note: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
    SectionTitle { text: "Почта для агента" }
    Note {
        text: "Любой ящик по IMAP и SMTP с паролем приложения (его делают в настройках почты: Яндекс, Gmail, Mail.ru, "
              + "Outlook). Письма читает только локальная модель, а отправку агент каждый раз спрашивает у вас."
    }
    Row {
        spacing: 8
        Rectangle {
            width: 7; height: 7; radius: 3.5
            color: Mail.connected ? Theme.p.ok : Theme.p.faint
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: Mail.connected ? Mail.address + " · " + Mail.servers : "не подключена"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    Column {
        visible: section.unlocked
        width: parent.width
        spacing: 6
        Field {
            id: address
            objectName: "mailAddress"
            width: parent.width
            placeholderText: Mail.connected ? Mail.address : "Адрес: name@yandex.ru"
        }
        Field {
            id: password
            objectName: "mailPassword"
            width: parent.width
            echoMode: TextInput.Password
            placeholderText: Mail.connected ? "Новый пароль приложения, чтобы заменить" : "Пароль приложения"
        }
        Text {
            visible: address.text.indexOf("@") > 0
            text: Mail.knownProvider(address.text) && !section.custom ? "Серверы подставятся сами · указать вручную"
                                                                      : "Серверы почты — из её настроек:"
            color: Theme.p.accent
            font.family: Theme.uiFont
            font.pixelSize: 12
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: section.custom = true
            }
        }
        Flow {
            visible: address.text.indexOf("@") > 0 && (section.custom || !Mail.knownProvider(address.text))
            width: parent.width
            spacing: 6
            Field { id: imapHost; objectName: "mailImapHost"; width: 200; placeholderText: "imap.example.org" }
            Field { id: imapPort; width: 70; placeholderText: "993" }
            Field { id: smtpHost; objectName: "mailSmtpHost"; width: 200; placeholderText: "smtp.example.org" }
            Field { id: smtpPort; width: 70; placeholderText: "465" }
        }
        Flow {
            width: parent.width
            spacing: 8
            PlainButton {
                objectName: "mailSave"
                kind: "primary"
                text: "Подключить"
                enabled: (address.text.trim() !== "" || Mail.connected) && !Mail.busy
                onClicked: {
                    Mail.save(address.text || Mail.address, password.text, imapHost.text, imapPort.text,
                              smtpHost.text, smtpPort.text)
                    password.text = ""
                }
            }
            PlainButton {
                visible: Mail.connected
                iconName: "refresh"
                text: "Проверить"
                enabled: !Mail.busy
                onClicked: Mail.check()
            }
            PlainButton {
                visible: Mail.connected
                kind: "danger"
                iconName: "trash"
                text: "Отключить"
                onClicked: Mail.remove()
            }
        }
    }
    Text {
        objectName: "mailNote"
        visible: Mail.note !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Mail.note
        color: Mail.noteOk ? Theme.p.ok : Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
