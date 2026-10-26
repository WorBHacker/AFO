import QtQuick

// The kind of a definition from the symbol index, as one letter in a small square: ƒ function or method, C class,
// I interface, T type, N module or namespace, K constant, # macro.
Rectangle {
    property string badge
    readonly property color colour: badge === "ƒ" ? Theme.p.ok
                                    : badge === "C" || badge === "I" || badge === "T" ? Theme.p.accent
                                    : badge === "K" || badge === "#" ? Theme.p.warn : Theme.p.muted
    width: 18
    height: 18
    radius: 4
    color: "transparent"
    border.color: Qt.rgba(colour.r, colour.g, colour.b, 0.45)
    Text {
        anchors.centerIn: parent
        text: parent.badge
        color: parent.colour
        font.family: Theme.monoFont
        font.pixelSize: 11
        font.weight: Font.DemiBold
    }
}
