import QtQuick
import QtQuick.Controls.Basic

// One choice out of a few, as a row of small buttons: options [{id, name}], `value` is the chosen id.
Flow {
    id: segmented
    property var options: []
    property string value
    signal picked(string id)
    spacing: 4
    Repeater {
        model: segmented.options
        delegate: AbstractButton {
            id: option
            required property var modelData
            readonly property bool chosen: modelData.id === segmented.value
            implicitHeight: 30
            implicitWidth: label.implicitWidth + 22
            hoverEnabled: true
            onClicked: { segmented.value = modelData.id; segmented.picked(modelData.id) }
            background: Rectangle {
                radius: 6
                color: option.chosen ? Theme.p.item_selected : (option.hovered ? Theme.p.item_hover : "transparent")
                border.color: option.chosen ? Theme.p.accent : Theme.p.hairline
            }
            contentItem: Text {
                id: label
                text: option.modelData.name
                color: option.chosen ? Theme.p.text : Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
