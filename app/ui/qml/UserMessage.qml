import QtQuick

// The user's message: a card on the right.
Item {
    id: message
    property var row: ({})  // read as row?.x: while a delegate is torn down, its row is null for a moment
    property int rowIndex: -1
    implicitHeight: bubble.height
    Rectangle {
        id: bubble
        anchors.right: parent.right
        width: Math.min(message.width * 0.8, body.implicitWidth + 32)
        height: body.height + 24
        radius: 8
        color: Theme.p.card
        border.color: Theme.p.hairline
        TextEdit {
            id: body
            x: 16
            y: 12
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
    }
}
