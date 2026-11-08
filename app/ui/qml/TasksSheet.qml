import QtQuick
import QtQuick.Controls.Basic

// A coordinator's board (16.4, D50): each task - the agent and its model, the status (one that waits for the user
// says so), what it waits for, how long, what it cost, its check, why it stopped; «Открыть» opens its run.
Sheet {
    id: sheet
    objectName: "tasksSheet"
    title: qsTr("Подзадачи координатора")
    iconName: "agent"
    width: Math.min(860, (parent ? parent.width : 960) * 0.94)
    onAboutToShow: Tasks.load()

    function toneColor(tone) {
        return tone === "ok" ? Theme.p.ok : tone === "warn" ? Theme.p.warn : tone === "error" ? Theme.p.error
             : tone === "accent" ? Theme.p.accent : Theme.p.faint
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Подзадачи без зависимостей идут сразу: облачные — вместе, локальные — по одной, видеокарта одна. Запросы подтверждения подзадач видны и здесь, у координатора.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Repeater {
        model: Tasks.rows
        delegate: Column {
            id: entry
            required property var modelData
            objectName: "taskRow_" + modelData.id
            width: parent ? parent.width : 600
            spacing: 4
            Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
            Item {
                width: parent.width
                height: 28
                Text {
                    id: name
                    width: Math.max(0, Math.min(implicitWidth, parent.width * 0.55))
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                    text: "[" + entry.modelData.id + "] «" + entry.modelData.agent + "»"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }
                Rectangle {
                    x: name.width + 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: chip.implicitWidth + 16
                    height: 20
                    radius: 10
                    color: "transparent"
                    border.color: sheet.toneColor(entry.modelData.tone)
                    Text {
                        id: chip
                        objectName: "taskStatus_" + entry.modelData.id
                        anchors.centerIn: parent
                        text: entry.modelData.statusText
                        color: sheet.toneColor(entry.modelData.tone)
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                PlainButton {
                    objectName: "taskOpen_" + entry.modelData.id
                    visible: entry.modelData.runId !== ""
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitHeight: 26
                    text: qsTr("Открыть")
                    onClicked: { sheet.close(); Tasks.openRun(entry.modelData.runId) }
                }
            }
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                text: [entry.modelData.model, entry.modelData.after, entry.modelData.time, entry.modelData.cost,
                       entry.modelData.check].filter(function (s) { return s !== "" }).join(" · ")
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Text {
                visible: entry.modelData.detail !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: entry.modelData.detail
                color: entry.modelData.tone === "warn" || entry.modelData.tone === "error" ? Theme.p.warn : Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }
}
