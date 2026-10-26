import QtQuick

// An error or a refusal, as a card outlined in the error colour.
Rectangle {
    id: card
    property var row: ({})
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
            text: "Ошибка"
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        TextEdit {
            width: parent.width
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
            text: card.row.text || ""
            color: Theme.p.text_soft
            selectionColor: Theme.p.selection
            font.family: Theme.uiFont
            font.pixelSize: 14
        }
    }
}
