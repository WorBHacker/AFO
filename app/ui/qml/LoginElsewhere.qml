import QtQuick

// AFO at Windows' sign-in is another copy of it (the user's word 2026-11-27, «создаёт в трей ещё одну afo»: the
// development folder's copy started there, and the installed AFO opened later was a second icon in the tray): which
// one, and a click makes it this one. Under the switch «Запускать AFO при входе в Windows», which is off then.
Column {
    id: root
    spacing: 6
    readonly property string other: App.autostart.login_other || ""
    visible: other !== ""
    Text {
        objectName: "loginElsewhere"
        width: root.width
        wrapMode: Text.Wrap
        text: App.autostart.login_other_gone
              ? qsTr("При входе в Windows записан запуск другой копии AFO — из папки %1, но её там уже нет.").arg(root.other)
              : qsTr("При входе в Windows запускается другая копия AFO — из папки %1. Если открыть и эту, в трее будет два значка AFO.").arg(root.other)
        color: Theme.p.warn
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    PlainButton {
        objectName: "loginHere"
        iconName: "power"
        text: qsTr("Запускать эту")
        onClicked: App.setAutostart("login", true)
    }
}
