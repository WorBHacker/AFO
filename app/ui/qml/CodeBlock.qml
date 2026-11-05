import QtQuick
import QtQuick.Controls.Basic

// A code block: header (language, copy; a diff view joins later, D17), code that scrolls sideways when long.
Rectangle {
    id: block
    property var info: ({})
    // a string property notifies only when its value changes: the same code again is not laid out again
    property string html: info.html || ""
    radius: 8
    color: Theme.p.code_bg
    border.color: Theme.p.hairline
    implicitHeight: head.height + scroller.height + 2
    function reused() {  // the chat's row now shows another answer: nothing of this block's stays
        code.deselect()
        scroller.contentX = 0
        copy.done = false
    }

    Rectangle {
        id: head
        x: 1; y: 1
        width: parent.width - 2
        height: 34
        radius: 7
        color: Theme.p.code_head
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 8; color: Theme.p.code_head }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
        Text {
            x: 14
            anchors.verticalCenter: parent.verticalCenter
            text: block.info.lang || ""
            color: Theme.p.muted
            font.family: Theme.monoFont
            font.pixelSize: 12
        }
        AbstractButton {
            id: copy
            property bool done: false
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            implicitHeight: 24
            implicitWidth: copyRow.implicitWidth + 16
            hoverEnabled: true
            onClicked: { App.copy(block.info.code || ""); done = true; reset.restart() }
            background: Rectangle { radius: 4; color: copy.hovered || copy.done ? Theme.p.item_hover : "transparent" }
            contentItem: Item {
                Row {
                    id: copyRow
                    anchors.centerIn: parent
                    spacing: 6
                    Icon {
                        name: copy.done ? "check" : "copy"
                        size: 14
                        tint: copy.done ? Theme.p.ok : (copy.hovered ? Theme.p.text : Theme.p.muted)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: copy.done ? qsTr("Скопировано") : qsTr("Копировать")
                        color: copy.done ? Theme.p.ok : (copy.hovered ? Theme.p.text : Theme.p.muted)
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
            Timer { id: reset; interval: 1500; onTriggered: copy.done = false }
        }
    }

    Flickable {
        id: scroller
        x: 1
        y: head.height + 1
        width: parent.width - 2
        height: code.height + (contentWidth > width ? 8 : 0)
        contentWidth: code.width
        contentHeight: code.height
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentWidth > width
        clip: true
        ScrollBar.horizontal: ThinScrollBar {}
        TextEdit {
            id: code
            width: Math.max(implicitWidth, scroller.width)
            padding: 14
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.RichText
            wrapMode: TextEdit.NoWrap
            text: block.html
            color: Theme.p.text
            selectionColor: Theme.p.selection
            font.family: Theme.monoFont
            font.pixelSize: 13
        }
    }
}
