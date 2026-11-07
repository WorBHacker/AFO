import QtQuick
import QtQuick.Controls.Basic

// What an answer looked at with the hub's tools: one line per call ("Читаю hub/chat.py"), its state (running, done,
// failed) and, on a click, what the tool returned; a picture a call drew (29.6) - under it, always.
Column {
    id: calls
    property var tools: []
    property int rowIndex: -1
    spacing: 2

    // The calls live in a ListModel changed only where the list did: a result arriving or a click on one call
    // does not rebuild the others (each may hold thousands of characters of output).
    onToolsChanged: syncCalls()
    Component.onCompleted: syncCalls()
    function syncCalls() {
        const list = calls.tools
        const roles = ["id", "icon", "label", "project", "status", "summary", "output", "open", "picture"]
        for (let i = 0; i < list.length; ++i) {
            const entry = {}
            for (const role of roles) {
                const value = list[i][role === "status" ? "state" : role]  // `state` is every Item's own property
                entry[role] = role === "open" ? !!value : String(value ?? "")
            }
            if (i >= entries.count) {
                entries.append(entry)
                continue
            }
            const old = entries.get(i)
            if (roles.some(role => old[role] !== entry[role]))
                entries.set(i, entry)
        }
        if (entries.count > list.length)
            entries.remove(list.length, entries.count - list.length)
    }
    ListModel { id: entries }

    Repeater {
        model: entries
        delegate: Column {
            id: call
            required property int index
            required property string icon
            required property string label
            required property string project
            required property string status
            required property string summary
            required property string output
            required property bool open
            required property string picture
            width: calls.width
            spacing: 4

            AbstractButton {
                id: line
                width: parent.width
                height: 28
                hoverEnabled: true
                enabled: call.status !== "running"
                onClicked: App.toggleTool(calls.rowIndex, call.index)
                background: Rectangle { radius: 6; color: line.hovered ? Theme.p.item_hover : "transparent" }
                contentItem: Item {
                    Icon {
                        x: 6
                        anchors.verticalCenter: parent.verticalCenter
                        name: call.icon
                        size: 14
                        tint: Theme.p.muted
                    }
                    Text {
                        id: label
                        x: 28
                        anchors.verticalCenter: parent.verticalCenter
                        text: call.label
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        x: label.x + label.width + 8
                        width: Math.max(0, parent.width - x - 40)
                        anchors.verticalCenter: parent.verticalCenter
                        text: call.status === "running" ? "…" : call.summary
                        elide: Text.ElideRight
                        color: call.status === "error" ? Theme.p.error : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Icon {
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        visible: call.status !== "running"
                        name: call.status === "error" ? "close" : (call.open ? "chevron-down" : "check")
                        size: 13
                        tint: call.status === "error" ? Theme.p.error : Theme.p.ok
                    }
                }
                ToolTip.visible: hovered && call.project !== ""
                ToolTip.delay: 800
                ToolTip.text: qsTr("Проект: %1 · щелчок — что вернул инструмент").arg(call.project)
            }

            Image {
                objectName: "toolPicture"
                visible: call.picture !== ""
                x: 28
                width: Math.min(420, parent.width - 28)
                height: implicitWidth > 0 ? width * implicitHeight / implicitWidth : 120
                source: call.picture
                asynchronous: true  // the provider asks the hub the first time: not on the window's thread
                fillMode: Image.PreserveAspectFit
                MouseArea {  // «По картинке» (29.6б): to «Нарисовать» as the source
                    id: pictureMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Visual.fromChat(call.picture.split("/").pop()); App.openUi("draw") }
                }
                ToolTip.visible: pictureMouse.containsMouse
                ToolTip.delay: 600
                ToolTip.text: qsTr("Щелчок — нарисовать по этой картинке")
            }

            Rectangle {
                visible: !!call.open
                x: 28
                width: parent.width - 28
                height: Math.min(260, output.implicitHeight + 16)
                radius: 6
                color: Theme.p.code_bg
                border.color: Theme.p.hairline
                Flickable {
                    id: scroller
                    anchors.fill: parent
                    anchors.margins: 8
                    contentWidth: output.width
                    contentHeight: output.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ThinScrollBar {}
                    ScrollBar.horizontal: ThinScrollBar {}
                    TextEdit {
                        id: output
                        width: Math.max(implicitWidth, scroller.width)
                        readOnly: true
                        selectByMouse: true
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.NoWrap
                        text: call.open ? call.output : ""  // a folded result is not laid out at all
                        color: Theme.p.text_soft
                        selectionColor: Theme.p.selection
                        font.family: Theme.monoFont
                        font.pixelSize: 12
                    }
                }
            }
        }
    }
}
