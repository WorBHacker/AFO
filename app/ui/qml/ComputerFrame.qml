import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window

// The agent at the desktop (9.6, D41): a frame around the window it controls - it lets every click through - and a
// bar above it: who acts, in what, and «Стоп». Both are windows of their own, on top of everything, and do not go
// away with AFO's own window minimized.
Item {
    id: root
    readonly property var place: AgentScreen.frame  // [x, y, w, h]

    Window {
        id: frame
        objectName: "agentFrame"
        transientParent: null
        visible: AgentScreen.shown
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput
               | Qt.WindowDoesNotAcceptFocus
        color: "transparent"
        x: root.place[0] - 4
        y: root.place[1] - 4
        width: Math.max(root.place[2] + 8, 40)
        height: Math.max(root.place[3] + 8, 40)
        Rectangle {
            anchors.fill: parent
            color: "transparent"
            radius: 8
            border.width: 3
            border.color: Theme.p.error
        }
    }

    Window {
        id: bar
        objectName: "agentBar"
        transientParent: null
        visible: AgentScreen.active
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        color: "transparent"
        width: content.implicitWidth + 28
        height: 38
        x: root.place[0] + Math.max(0, (root.place[2] - width) / 2)
        y: root.place[1] >= height + 10 ? root.place[1] - height - 8 : root.place[1] + 8
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            color: Theme.p.error
            Row {
                id: content
                anchors.centerIn: parent
                spacing: 12
                Rectangle {
                    id: workingDot
                    width: 8; height: 8; radius: 4
                    color: "white"
                    anchors.verticalCenter: parent.verticalCenter
                    Pulse { target: workingDot; running: bar.visible; low: 0.3 }
                }
                Text {
                    text: AgentScreen.text
                    color: "white"
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    anchors.verticalCenter: parent.verticalCenter
                }
                AbstractButton {
                    id: stop
                    objectName: "agentStop"
                    implicitWidth: label.implicitWidth + 20
                    implicitHeight: 26
                    hoverEnabled: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: AgentScreen.stop()
                    background: Rectangle {
                        radius: 13
                        color: stop.hovered ? "#ffffff" : "#f2ffffff"
                    }
                    contentItem: Text {
                        id: label
                        text: qsTr("Стоп · %1").arg(AgentScreen.stopKeys)
                        color: Theme.p.error
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }
    }
}
