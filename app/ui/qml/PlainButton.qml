import QtQuick
import QtQuick.Controls.Basic

// A text button: kind "primary" (accent), "plain" (outlined) or "danger" (error outline), with an optional icon.
// `maxWidth`: for a label made of data - the room there is; past it the label ends in «…», the whole of it in a tooltip.
AbstractButton {
    id: button
    property string kind: "plain"
    property string iconName
    implicitHeight: 32
    property real maxWidth: Infinity
    readonly property real around: (iconName !== "" ? 23 : 0) + 28
    implicitWidth: Math.min(label.implicitWidth + around, maxWidth)
    hoverEnabled: true
    ToolTip.visible: hovered && label.truncated
    ToolTip.text: text
    opacity: enabled ? 1 : 0.5
    background: Rectangle {
        radius: 6
        color: button.kind === "primary" ? (button.hovered ? Theme.p.accent_hover : Theme.p.accent)
               : (button.hovered ? Theme.p.item_hover : "transparent")
        border.color: button.kind === "primary" ? "transparent"
                      : button.kind === "danger" ? Theme.p.error_line : Theme.p.hairline_strong
    }
    contentItem: Item {
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 8
            Icon {
                visible: button.iconName !== ""
                name: button.iconName
                size: 15
                tint: button.kind === "primary" ? Theme.p.on_accent : button.kind === "danger" ? Theme.p.error : Theme.p.text_soft
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                id: label
                width: Math.max(0, Math.min(implicitWidth, button.width - button.around))
                elide: Text.ElideRight
                text: button.text
                color: button.kind === "primary" ? Theme.p.on_accent : button.kind === "danger" ? Theme.p.error : Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
                font.weight: button.kind === "primary" ? Font.DemiBold : Font.Normal
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
