import QtQuick
import QtQuick.Controls.Basic

// «Улучшить ролик» (54.6): a video of the Studio's or a film's joined one - twice as large (fast or with details)
// and/or 60 frames a second or slowed. What it takes - the parts - is fetched here, each by a click; «Улучшить»
// sends it to the Studio's queue, where it goes on as a video does. `prefix` names its parts: one sheet in the
// Studio, one in «Режиссёр».
Popup {
    id: sheet
    property string prefix: "improve"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent ? parent.width - 40 : 560)
    padding: 20
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
    background: Rectangle {
        radius: 10
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow { corner: 10 }
    }

    component Label: Text {
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Note: Text {
        width: sheet.availableWidth
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        lineHeight: 1.25
    }

    contentItem: Column {
        spacing: 10
        Text {
            text: qsTr("Улучшить ролик")
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Text {
            objectName: sheet.prefix + "Title"
            width: sheet.availableWidth
            elide: Text.ElideRight
            text: Improve.title
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Label { text: qsTr("Размер") }
        Segmented {
            objectName: sheet.prefix + "Size"
            options: Improve.sizes
            value: Improve.size
            onPicked: (id) => Improve.setSize(id)
        }
        Label { text: qsTr("Движение") }
        Segmented {
            objectName: sheet.prefix + "Motion"
            options: Improve.motions
            value: Improve.motion
            onPicked: (id) => Improve.setMotion(id)
        }
        Note {
            objectName: sheet.prefix + "Note"
            text: Improve.note
        }
        Label { text: qsTr("Что для этого нужно") }
        Repeater {
            model: Improve.parts
            delegate: Item {
                id: part
                required property var modelData
                objectName: sheet.prefix + "Part_" + part.modelData.id
                width: sheet.availableWidth
                height: 38
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - partDo.width - 12
                    spacing: 2
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: part.modelData.name + " — " + part.modelData.about
                        color: part.modelData.needed ? Theme.p.text : Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        font.weight: part.modelData.needed ? Font.DemiBold : Font.Normal
                    }
                    Text {
                        objectName: sheet.prefix + "PartNote_" + part.modelData.id
                        width: parent.width
                        elide: Text.ElideRight
                        text: part.modelData.note
                        color: part.modelData.failed ? Theme.p.error : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                Row {
                    id: partDo
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    PlainButton {
                        objectName: sheet.prefix + "Get_" + part.modelData.id
                        visible: part.modelData.state === "missing"
                        iconName: "download"
                        text: part.modelData.getText
                        onClicked: Improve.getPart(part.modelData.id)
                    }
                    PlainButton {
                        objectName: sheet.prefix + "Stop_" + part.modelData.id
                        visible: part.modelData.state === "loading"
                        kind: "danger"
                        iconName: "stop"
                        text: qsTr("Стоп")
                        onClicked: Improve.stopPart(part.modelData.id)
                    }
                    IconButton {
                        objectName: sheet.prefix + "Remove_" + part.modelData.id
                        visible: part.modelData.state === "ready"
                        iconName: "trash"
                        tip: qsTr("Удалить с диска")
                        onClicked: Improve.removePart(part.modelData.id)
                    }
                }
            }
        }
        Note {
            text: qsTr("Программы улучшения работают на этом компьютере, ролик в облако не уходит. Каждая скачивается один раз.")
        }
        Text {
            objectName: sheet.prefix + "Need"
            width: sheet.availableWidth
            visible: text !== ""
            wrapMode: Text.Wrap
            text: Improve.need
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            PlainButton {
                objectName: sheet.prefix + "Cancel"
                text: qsTr("Отмена")
                onClicked: sheet.close()
            }
            PlainButton {
                objectName: sheet.prefix + "Go"
                kind: "primary"
                iconName: "sparkle"
                enabled: Improve.canStart
                text: qsTr("Улучшить")
                onClicked: {
                    Improve.start()
                    sheet.close()
                }
            }
        }
    }
}
