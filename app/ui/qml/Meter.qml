import QtQuick

// A label, a thin bar and its value: "VRAM ▬▬▬ 14.7/15.9 ГБ".
Row {
    property string label
    property real value
    property string valueText
    property bool warn: false
    spacing: 6
    Text { text: parent.label; color: Theme.p.muted; font.family: Theme.uiFont; font.pixelSize: 12 }
    Rectangle {
        width: 44; height: 4; radius: 2
        color: Theme.p.hairline
        anchors.verticalCenter: parent.verticalCenter
        Rectangle {
            width: parent.width * Math.max(0, Math.min(1, parent.parent.value))
            height: 4; radius: 2
            color: parent.parent.warn ? Theme.p.warn : Theme.p.accent
        }
    }
    Text {
        text: parent.valueText
        color: parent.warn ? Theme.p.warn : Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
