import QtQuick
import QtQuick.Controls.Basic

// A one-line input in the app's style (D36): card background, hairline border, a stronger one with focus.
TextField {
    id: field
    implicitHeight: 34
    color: Theme.p.text
    placeholderTextColor: Theme.p.faint
    selectionColor: Theme.p.selection
    selectedTextColor: Theme.p.text
    font.family: Theme.uiFont
    font.pixelSize: 13
    leftPadding: 10
    rightPadding: 10
    background: Rectangle {
        radius: 6
        color: Theme.p.card
        border.color: field.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
    }
}
