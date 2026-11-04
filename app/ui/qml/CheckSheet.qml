import QtQuick
import QtQuick.Controls.Basic

// «Проверить систему» (10.6, D37): what works and what does not, in plain words with what to do; «Собрать журналы»
// makes one file for whoever helps (no keys or tokens in it).
Sheet {
    id: sheet
    objectName: "checkSheet"
    title: qsTr("Проверка системы")
    iconName: "activity"
    width: Math.min(640, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Check.run()

    function tone(t) { return t === "ok" ? Theme.p.ok : t === "warn" ? Theme.p.warn : t === "error" ? Theme.p.error : Theme.p.muted }

    Text {
        objectName: "checkSummary"
        width: parent.width
        wrapMode: Text.Wrap
        text: Check.summary
        color: Theme.p.text
        font.family: Theme.uiFont
        font.pixelSize: 14
        font.weight: Font.DemiBold
    }
    Repeater {
        model: Check.lines
        delegate: Row {
            id: line
            required property var modelData
            width: parent ? parent.width : 560
            spacing: 10
            Rectangle {
                y: 6
                width: 9
                height: 9
                radius: 5
                color: sheet.tone(line.modelData.tone)
            }
            Column {
                width: line.width - 20
                spacing: 2
                Text {
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: line.modelData.title + " — " + line.modelData.mark
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WrapAnywhere
                    text: line.modelData.text
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Text {
                    visible: line.modelData.advice !== ""
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: line.modelData.advice
                    color: sheet.tone(line.modelData.tone)
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
        }
    }
    Row {
        spacing: 8
        PlainButton {
            iconName: "refresh"
            text: Check.busy ? qsTr("Проверяю…") : qsTr("Проверить ещё раз")
            enabled: !Check.busy
            onClicked: Check.run()
        }
        PlainButton {
            objectName: "collectLogs"
            iconName: "folder"
            text: qsTr("Собрать журналы для поддержки")
            enabled: !Check.busy
            onClicked: Check.collect()
        }
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("В файле — эта проверка, версии и последние записи журналов; ключи и токены из них вырезаны. Базы с вашими разговорами в нём нет.")
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 11
    }
}
