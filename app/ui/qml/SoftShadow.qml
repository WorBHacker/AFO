import QtQuick

// A soft shadow without shader effects (the software renderer has none): translucent rounded layers under the
// parent, each a little larger. Put it inside the item that casts it.
Repeater {
    property real strength: Theme.dark ? 0.10 : 0.045
    property real corner: 8
    model: 9
    delegate: Rectangle {
        required property int index
        z: -1
        anchors.fill: parent
        anchors.leftMargin: -(index + 1) * 2
        anchors.rightMargin: -(index + 1) * 2
        anchors.topMargin: -(index + 1) * 2 + 6
        anchors.bottomMargin: -(index + 1) * 2 - 6
        radius: corner + (index + 1) * 2
        color: Qt.rgba(0, 0, 0, strength * (1 - index / 9))
    }
}
