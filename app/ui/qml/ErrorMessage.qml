import QtQuick

// An error or a refusal, as a card outlined in the error colour.
Rectangle {
    id: card
    property var row: ({})  // read as row?.x: while a delegate is torn down, its row is null for a moment
    property int rowIndex: -1
    implicitHeight: column.height + 24
    radius: 8
    color: Theme.p.card
    border.color: Theme.p.error_line
    Column {
        id: column
        x: 16
        y: 12
        width: parent.width - 32
        spacing: 4
        Text {
            text: card.row?.title || qsTr("Ошибка")
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        Text {
            visible: (card.row?.hint || "") !== ""
            width: parent.width
            wrapMode: Text.Wrap
            text: card.row?.hint || ""
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 14
            lineHeight: 1.3
        }
        TextEdit {
            width: parent.width
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
            text: card.row?.text || ""
            color: (card.row?.hint || "") !== "" ? Theme.p.faint : Theme.p.text_soft
            selectionColor: Theme.p.selection
            font.family: Theme.uiFont
            font.pixelSize: 14
        }
    }
}
