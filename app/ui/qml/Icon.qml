import QtQuick

// A line icon from the app's icon provider (app/ui/icons.py), tinted with any theme colour.
Image {
    property string name
    property color tint: Theme.p.muted
    property int size: 16
    source: name ? "image://icons/" + name + "?" + tint.toString().substring(1) : ""
    sourceSize: Qt.size(size * 2, size * 2)
    width: size
    height: size
    smooth: true
}
