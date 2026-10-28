import QtQuick
import QtQuick.Controls.Basic

// The hub's word to the model between its turns (a goal's next turn, D44; an agent's reminder, D40): one quiet line
// in the conversation - what it is and, in short, what it says; a click shows the whole text the model read.
Item {
    id: note
    property var row: ({})
    property int rowIndex: -1
    property bool open: false
    implicitHeight: line.height + (open ? body.height + 8 : 0)

    AbstractButton {
        id: line
        objectName: "hubNote_" + note.rowIndex
        width: parent.width
        height: 26
        hoverEnabled: true
        onClicked: note.open = !note.open
        contentItem: Row {
            spacing: 8
            Icon {
                name: note.row?.goal ? "target" : "bolt"
                size: 14
                tint: Theme.p.faint
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: note.row?.title || ""
                color: line.hovered ? Theme.p.text_soft : Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                visible: !!note.row?.note
                text: "· " + (note.row?.note || "")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
                anchors.verticalCenter: parent.verticalCenter
            }
            Icon {
                name: note.open ? "chevron-down" : "chevron-right"
                size: 12
                tint: Theme.p.faint
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        ToolTip.visible: hovered && !note.open
        ToolTip.delay: 800
        ToolTip.text: "Это пишет модели хаб, а не вы. Щелчок — показать текст целиком"
    }
    Rectangle {
        id: body
        visible: note.open
        y: line.height + 4
        width: parent.width
        height: text.height + 20
        radius: 6
        color: Theme.p.code_bg
        border.color: Theme.p.hairline
        TextEdit {
            id: text
            x: 12
            y: 10
            width: parent.width - 24
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
            text: note.row?.text || ""
            color: Theme.p.text_soft
            selectionColor: Theme.p.selection
            selectedTextColor: Theme.p.text
            font.family: Theme.monoFont
            font.pixelSize: 12
        }
    }
}
