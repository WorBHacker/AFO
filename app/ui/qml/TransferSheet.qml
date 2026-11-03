import QtQuick
import QtQuick.Controls.Basic

// «Загрузка настроек» (D46, step 13.10): what a settings file would do, part by part, before anything is written.
// A part can be left out; the parts behind the connections' password ask for it here; loading never removes.
Sheet {
    id: sheet
    objectName: "transferSheet"
    title: "Загрузка настроек"
    iconName: "download"
    width: Math.min(640, (parent ? parent.width : 960) * 0.92)
    onClosed: Transfer.close()

    Connections {
        target: Transfer
        function onFinished() { sheet.close() }
    }

    function tone(t) {
        return t === "ok" ? Theme.p.ok : t === "accent" ? Theme.p.accent : t === "warn" ? Theme.p.warn : Theme.p.faint
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Файл " + Transfer.fileName + (Transfer.exportedAt ? ", сохранён " + Transfer.exportedAt : "")
              + ". Ниже — что изменится, если загрузить. Загрузка только добавляет и меняет, ничего не удаляет; "
              + "MCP-серверы и расписания приходят выключенными. Ключей и паролей в файле нет — их вписывают заново."
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Text {
        objectName: "transferError"
        visible: Transfer.error !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Transfer.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Text {
        visible: Transfer.busy
        text: "Смотрю, что изменится…"
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    Repeater {
        model: Transfer.parts
        delegate: Column {
            id: part
            required property var modelData
            width: parent ? parent.width : 560
            spacing: 4
            AbstractButton {
                id: toggle
                objectName: "part_" + part.modelData.id
                width: parent.width
                implicitHeight: 32
                hoverEnabled: true
                enabled: !Transfer.busy
                onClicked: Transfer.toggle(part.modelData.id)
                background: Rectangle {
                    radius: 6
                    color: toggle.hovered ? Theme.p.item_hover : "transparent"
                }
                contentItem: Item {
                    Icon {
                        x: 6
                        anchors.verticalCenter: parent.verticalCenter
                        name: part.modelData.chosen ? "check" : "close"
                        size: 13
                        tint: part.modelData.chosen ? Theme.p.accent : Theme.p.faint
                    }
                    Text {
                        x: 26
                        width: parent.width - 30
                        anchors.verticalCenter: parent.verticalCenter
                        elide: Text.ElideRight
                        text: part.modelData.title + " — " + part.modelData.counts
                        color: part.modelData.chosen ? Theme.p.text : Theme.p.muted
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                }
            }
            Text {
                visible: part.modelData.locked
                x: 26
                width: parent.width - 26
                wrapMode: Text.Wrap
                text: "Эта часть — за паролем подключений: без него она не загрузится."
                color: Theme.p.warn
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Repeater {
                model: part.modelData.items
                delegate: Column {
                    id: line
                    required property var modelData
                    x: 26
                    width: part.width - 26
                    spacing: 1
                    Text {
                        width: parent.width
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                        text: line.modelData.what + " — " + line.modelData.actionText
                              + (line.modelData.detail ? ": " + line.modelData.detail : "")
                        color: sheet.tone(line.modelData.tone)
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Text {
                        visible: line.modelData.warn !== ""
                        width: parent.width
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        text: "⚠ " + line.modelData.warn
                        color: Theme.p.warn
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
            }
        }
    }

    Row {
        visible: Transfer.needsPassword
        spacing: 8
        Field {
            id: password
            objectName: "transferPassword"
            width: 240
            echoMode: TextInput.Password
            placeholderText: "Пароль подключений"
            onAccepted: { Transfer.unlock(text); text = "" }
        }
        PlainButton {
            iconName: "key"
            text: "Открыть"
            enabled: !Transfer.busy
            onClicked: { Transfer.unlock(password.text); password.text = "" }
        }
    }
    Row {
        spacing: 8
        PlainButton {
            objectName: "transferApply"
            kind: "primary"
            iconName: "download"
            text: "Загрузить выбранное"
            enabled: !Transfer.busy && Transfer.parts.some(p => p.chosen)
            onClicked: Transfer.apply()
        }
        PlainButton {
            text: "Отмена"
            onClicked: sheet.close()
        }
    }
}
