import QtQuick
import QtQuick.Controls.Basic

// Settings → «Автономный агент»: the programs it may drive, its folders and sites, the quiet hours.
Sheet {
    id: sheet
    objectName: "settingsAgent"
    title: qsTr("Автономный агент")
    iconName: "agent"

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Программы, которыми агент может управлять: хаб находит их сам и спрашивает о каждой новой.")
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openPrograms"
        iconName: "shield"
        text: qsTr("Программы для агента…")
        onClicked: { sheet.close(); App.openUi("programs") }
    }
    PlainButton {
        objectName: "openAgentAccess"
        iconName: "folder"
        text: qsTr("Папки и сайты для агента…")
        onClicked: { sheet.close(); App.openUi("agent-access") }
    }
    SectionTitle { text: qsTr("Тихие часы") }
    Row {
        objectName: "quietHours"
        spacing: 8
        Text {
            text: qsTr("Тихие часы: с")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Field {
            id: quietFrom
            objectName: "quietFrom"
            width: 70
            text: Schedules.quiet.from || "23:00"
            onEditingFinished: Schedules.setQuiet(Schedules.quiet.enabled !== false, quietFrom.text, quietTo.text)
        }
        Text {
            text: qsTr("до")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Field {
            id: quietTo
            objectName: "quietTo"
            width: 70
            text: Schedules.quiet.to || "08:00"
            onEditingFinished: Schedules.setQuiet(Schedules.quiet.enabled !== false, quietFrom.text, quietTo.text)
        }
        Segmented {
            objectName: "quietEnabled"
            options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }]
            value: Schedules.quiet.enabled === false ? "off" : "on"
            onPicked: (id) => Schedules.setQuiet(id === "on", quietFrom.text, quietTo.text)
        }
    }  // a time is saved when its field is left (the row has no room for a button of its own)
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: Schedules.quietText + "."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
