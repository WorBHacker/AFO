import QtQuick

// The user's message: a card on the right; the files and pictures sent with it under its text.
Item {
    id: message
    property var row: ({})  // read as row?.x: while a delegate is torn down, its row is null for a moment
    property int rowIndex: -1
    readonly property var files: message.row?.files || []
    implicitHeight: bubble.height
    function reused() { body.deselect() }  // the chat's row now shows another message (ChatView.reuseItems)
    Rectangle {
        id: bubble
        anchors.right: parent.right
        width: Math.min(message.width * 0.8, Math.max(body.visible ? body.implicitWidth : 0, sent.implicitWidth) + 32)
        height: column.height + 24
        radius: 8
        color: Theme.p.card
        border.color: Theme.p.hairline
        Column {
            id: column
            x: 16
            y: 12
            width: parent.width - 32
            spacing: 8
            TextEdit {
                id: body
                visible: text !== ""
                width: Math.min(message.width * 0.8 - 32, implicitWidth)
                readOnly: true
                selectByMouse: true
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                text: message.row?.text || ""
                color: Theme.p.text
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
            }
            Flow {
                id: sent
                objectName: "sentFiles"
                visible: message.files.length > 0
                width: parent.width
                spacing: 6
                Repeater {
                    model: message.files
                    delegate: Rectangle {
                        required property var modelData
                        width: fileRow.implicitWidth + 16
                        height: 24
                        radius: 6
                        color: Theme.p.item_hover
                        Row {
                            id: fileRow
                            x: 8
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6
                            Icon {
                                name: modelData.picture ? "image" : "file"
                                size: 13
                                tint: Theme.p.text_soft
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: modelData.name
                                color: Theme.p.text_soft
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                                elide: Text.ElideMiddle
                                width: Math.min(implicitWidth, 260)
                                anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                    }
                }
            }
        }
    }
}
