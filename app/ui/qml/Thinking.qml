import QtQuick

// The model's reasoning as an accordion: a muted header with a timer, the text folded away until asked.
Column {
    id: think
    property var info: ({})
    property int rowIndex: -1
    // folded reasoning is not laid out at all; a string property notifies only when the text really changes
    property string bodyText: info.open ? (info.text || "") : ""
    spacing: 8

    Item {
        width: header.implicitWidth
        height: 22
        Row {
            id: header
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            Rectangle {
                width: 7; height: 7; radius: 3.5
                color: Theme.p.accent
                visible: !!think.info.running
                anchors.verticalCenter: parent.verticalCenter
                SequentialAnimation on opacity {
                    running: !!think.info.running
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.25; duration: 600 }
                    NumberAnimation { to: 1; duration: 600 }
                }
            }
            Icon { name: "sparkle"; size: 14; visible: !think.info.running; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: (think.info.running ? "Думает… " : (think.info.seconds ? "Размышления · " : "Размышления"))
                      + (think.info.seconds || "")
                color: hover.hovered ? Theme.p.text_soft : Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
            Icon {
                name: think.info.open ? "chevron-down" : "chevron-right"
                size: 13
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: App.toggleThinking(think.rowIndex) }
    }

    Row {
        visible: !!think.info.open
        spacing: 12
        leftPadding: 6
        Rectangle { width: 2; height: body.height; radius: 1; color: Theme.p.hairline_strong }
        TextEdit {
            id: body
            width: think.width - 26
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.Wrap
            text: think.bodyText
            color: Theme.p.muted
            selectionColor: Theme.p.selection
            selectedTextColor: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
