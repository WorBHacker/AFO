import QtQuick
import QtQuick.Controls.Basic

// A compact control in the composer: icon, label, a chevron when it opens a menu.
AbstractButton {
    id: chip
    property string iconName
    property bool menu: true
    property bool active: false
    implicitHeight: 30
    implicitWidth: row.implicitWidth + 20
    hoverEnabled: true
    background: Rectangle {
        radius: 6
        color: chip.active ? Theme.p.item_selected : (chip.hovered ? Theme.p.item_hover : "transparent")
        border.color: chip.active ? Theme.p.hairline_strong : Theme.p.hairline
    }
    contentItem: Item {
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 6
            Icon { name: chip.iconName; visible: chip.iconName !== ""; size: 15; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: chip.text
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
            Icon { name: "chevron-down"; visible: chip.menu; size: 13; anchors.verticalCenter: parent.verticalCenter }
        }
    }
}
