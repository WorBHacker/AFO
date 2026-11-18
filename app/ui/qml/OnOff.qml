import QtQuick
import QtQuick.Controls.Basic

// An on/off of a sheet or a panel: a check when on, a cross when off, its name beside. One for the settings'
// sheets, the search's filters, a chat's context, the director's look at its scenes (51.4).
AbstractButton {
    id: toggle
    checkable: true
    implicitHeight: 30
    implicitWidth: label.implicitWidth + 40
    hoverEnabled: true
    background: Rectangle {
        radius: 6
        color: toggle.checked ? Theme.p.item_selected : (toggle.hovered ? Theme.p.item_hover : "transparent")
        border.color: toggle.checked ? Theme.p.accent : Theme.p.hairline_strong
    }
    contentItem: Item {
        Icon {
            x: 8
            anchors.verticalCenter: parent.verticalCenter
            name: toggle.checked ? "check" : "close"
            size: 13
            tint: toggle.checked ? Theme.p.accent : Theme.p.faint
        }
        Text {
            id: label
            x: 26
            anchors.verticalCenter: parent.verticalCenter
            text: toggle.text
            color: toggle.checked ? Theme.p.text : Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
