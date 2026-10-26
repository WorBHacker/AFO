import QtQuick
import QtQuick.Controls.Basic

// What an answer looked at with the hub's tools: one line per call ("Читаю hub/chat.py"), its state (running, done,
// failed) and, on a click, what the tool returned.
Column {
    id: calls
    property var tools: []
    property int rowIndex: -1
    spacing: 2

    Repeater {
        model: calls.tools
        delegate: Column {
            id: call
            required property var modelData
            required property int index
            width: calls.width
            spacing: 4

            AbstractButton {
                id: line
                width: parent.width
                height: 28
                hoverEnabled: true
                enabled: call.modelData.state !== "running"
                onClicked: App.toggleTool(calls.rowIndex, call.index)
                background: Rectangle { radius: 6; color: line.hovered ? Theme.p.item_hover : "transparent" }
                contentItem: Item {
                    Icon {
                        x: 6
                        anchors.verticalCenter: parent.verticalCenter
                        name: call.modelData.icon
                        size: 14
                        tint: Theme.p.muted
                    }
                    Text {
                        id: label
                        x: 28
                        anchors.verticalCenter: parent.verticalCenter
                        text: call.modelData.label
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        x: label.x + label.width + 8
                        width: Math.max(0, parent.width - x - 40)
                        anchors.verticalCenter: parent.verticalCenter
                        text: call.modelData.state === "running" ? "…" : call.modelData.summary
                        elide: Text.ElideRight
                        color: call.modelData.state === "error" ? Theme.p.error : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Icon {
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        visible: call.modelData.state !== "running"
                        name: call.modelData.state === "error" ? "close" : (call.modelData.open ? "chevron-down" : "check")
                        size: 13
                        tint: call.modelData.state === "error" ? Theme.p.error : Theme.p.ok
                    }
                }
                ToolTip.visible: hovered && call.modelData.project !== ""
                ToolTip.delay: 800
                ToolTip.text: "Проект: " + call.modelData.project + " · щелчок — что вернул инструмент"
            }

            Rectangle {
                visible: !!call.modelData.open
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
                        text: call.modelData.output || ""
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
