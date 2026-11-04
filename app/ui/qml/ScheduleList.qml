import QtQuick
import QtQuick.Controls.Basic

// The schedules in the Agent mode's sidebar (9.7): what starts when, the next start in words; run now, on / off,
// remove.
Column {
    id: list
    objectName: "scheduleList"
    spacing: 2
    visible: Schedules.items.length > 0

    Text {
        leftPadding: 8
        text: qsTr("Расписание")
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
    Repeater {
        model: Schedules.items
        delegate: Item {
            id: row
            required property var modelData
            width: list.width
            implicitHeight: texts.implicitHeight + 12
            HoverHandler { id: hover }
            Rectangle {
                anchors.fill: parent
                radius: 6
                color: hover.hovered ? Theme.p.item_hover : "transparent"
            }
            Column {
                id: texts
                x: 8
                y: 6
                width: parent.width - buttons.width - 20
                Text {
                    width: parent.width
                    elide: Text.ElideRight
                    text: row.modelData.title
                    color: row.modelData.enabled ? Theme.p.text : Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Text {
                    width: parent.width
                    elide: Text.ElideRight
                    text: row.modelData.trigger + " · " + row.modelData.when
                          + (row.modelData.computer ? qsTr(" · с компьютером") : "")
                    color: row.modelData.error !== "" ? Theme.p.error : Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
            }
            Row {
                id: buttons
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                visible: hover.hovered
                spacing: 2
                IconButton {
                    objectName: "scheduleRun_" + row.modelData.id
                    iconName: "bolt"
                    tip: qsTr("Запустить сейчас")
                    onClicked: Schedules.runNow(row.modelData.id)
                }
                IconButton {
                    iconName: "power"
                    tip: row.modelData.enabled ? qsTr("Выключить") : qsTr("Включить")
                    onClicked: Schedules.setEnabled(row.modelData.id, !row.modelData.enabled)
                }
                IconButton {
                    iconName: "trash"
                    tip: qsTr("Удалить")
                    onClicked: Schedules.remove(row.modelData.id)
                }
            }
        }
    }
}
