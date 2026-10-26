import QtQuick
import QtQuick.Controls.Basic

// A flat square button with an icon; highlights on hover.
AbstractButton {
    id: button
    property string iconName
    property color tint: Theme.p.muted
    property color hoverColour: Theme.p.item_hover
    property color hoverTint: Theme.p.text
    property string tip
    implicitWidth: 30
    implicitHeight: 30
    hoverEnabled: true
    background: Rectangle { radius: 6; color: button.hovered ? button.hoverColour : "transparent" }
    contentItem: Item {
        Icon { anchors.centerIn: parent; name: button.iconName; tint: button.hovered ? button.hoverTint : button.tint }
    }
    ToolTip.visible: tip !== "" && hovered
    ToolTip.delay: 600
    ToolTip.text: tip
}
