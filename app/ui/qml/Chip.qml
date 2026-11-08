import QtQuick
import QtQuick.Controls.Basic

// A compact control in the composer: icon, label, a chevron when it opens a menu. It is never wider than the row that
// holds it: a long name ends in «…», the whole of it in a tooltip.
AbstractButton {
    id: chip
    property string iconName
    property bool menu: true
    property bool active: false
    implicitHeight: 30
    readonly property real room: parent && parent.width > 0 ? parent.width : Infinity
    readonly property real around: (iconName !== "" ? 21 : 0) + (menu ? 19 : 0) + 20
    implicitWidth: Math.min(label.implicitWidth + around, room)
    hoverEnabled: true
    ToolTip.visible: hovered && label.truncated
    ToolTip.text: text
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
                id: label
                width: Math.max(0, Math.min(implicitWidth, chip.width - chip.around))
                elide: Text.ElideRight
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
