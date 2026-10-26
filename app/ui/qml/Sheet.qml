import QtQuick
import QtQuick.Controls.Basic

// A panel that slides in from the right over a dimmed window, for budgets and settings. Its content is a column.
Drawer {
    id: sheet
    property string title
    property string iconName
    default property alias content: body.data

    edge: Qt.RightEdge
    width: Math.min(480, (parent ? parent.width : 960) * 0.9)
    height: parent ? parent.height : 600
    modal: true
    Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }

    background: Rectangle {
        color: Theme.p.panel
        Rectangle { width: 1; height: parent.height; color: Theme.p.hairline }
    }

    contentItem: Item {
        Item {
            id: head
            width: parent.width
            height: 60
            Row {
                x: 24
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10
                Icon { name: sheet.iconName; size: 18; tint: Theme.p.text; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: sheet.title
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 16
                    font.weight: Font.DemiBold
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            IconButton {
                iconName: "close"
                tip: "Закрыть (Esc)"
                anchors.right: parent.right
                anchors.rightMargin: 14
                anchors.verticalCenter: parent.verticalCenter
                onClicked: sheet.close()
            }
        }
        Flickable {
            y: head.height
            width: parent.width
            height: parent.height - head.height
            contentHeight: body.height + 32
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            ScrollBar.vertical: ThinScrollBar {}
            Column {
                id: body
                x: 24
                width: parent.width - 48
                spacing: 12
            }
        }
    }
}
