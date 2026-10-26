import QtQuick
import QtQuick.Controls.Basic

// A flat 6px scroll bar without arrows (D36). It shows while scrolling or under the mouse, only when there is
// something to scroll, and fades out after.
ScrollBar {
    id: bar
    policy: ScrollBar.AsNeeded
    hoverEnabled: true
    padding: 2
    implicitWidth: orientation === Qt.Vertical ? 10 : 0
    implicitHeight: orientation === Qt.Horizontal ? 10 : 0
    background: null
    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: 3
        color: bar.pressed || bar.hovered ? Theme.p.scroll_handle_hover : Theme.p.scroll_handle
        opacity: bar.size < 1.0 && (bar.active || bar.hovered) ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
    }
}
