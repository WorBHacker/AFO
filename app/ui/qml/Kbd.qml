import QtQuick

// A keyboard hint: "Ctrl N".
Rectangle {
    property string text
    implicitWidth: label.implicitWidth + 12
    implicitHeight: 20
    radius: 4
    color: "transparent"
    border.color: Theme.p.hairline_strong
    Text {
        id: label
        anchors.centerIn: parent
        text: parent.text
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 11
    }
}
