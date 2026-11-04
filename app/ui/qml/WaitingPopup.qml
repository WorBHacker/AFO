import QtQuick
import QtQuick.Controls.Basic

// Who waits for an answer (D38), under the shield in the title bar: a row per chat or agent run - what it asks, how
// many more, how long it has waited; a row opens that chat with its card (from the projects mode too). A request
// with no chat (an outside MCP client) is answered right here, as a card.
Popup {
    id: popup
    objectName: "approvalsList"
    parent: Overlay.overlay
    width: Math.min(460, (parent ? parent.width : 960) - 32)
    x: parent ? parent.width - width - 12 : 0
    y: 56
    padding: 8
    height: Math.min(implicitHeight, (parent ? parent.height : 800) - y - 40)
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onAboutToShow: Approvals.load()  // fresh from the hub, and how long each waits, now
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 90 } }

    background: Rectangle {
        radius: 8
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow {}
    }

    contentItem: Flickable {
        implicitHeight: list.height
        contentHeight: list.height
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}

        Column {
            id: list
            width: parent.width
            spacing: 2

            Text {
                height: 28
                leftPadding: 10
                bottomPadding: 5
                verticalAlignment: Text.AlignBottom
                text: Approvals.count > 0 ? qsTr("Ждут ответа: %1").arg(Approvals.count) : qsTr("Подтверждения")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
            Text {
                visible: Approvals.count === 0
                width: parent.width
                padding: 10
                wrapMode: Text.Wrap
                text: qsTr("Никто ничего не ждёт. Когда модель или агент захотят выполнить команду или поправить файл, карточка появится сверху их чата, а щит в заголовке покажет, сколько запросов ждёт.")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 13
            }

            Repeater {
                model: Approvals.waiting
                delegate: AbstractButton {
                    id: entry
                    required property var modelData
                    objectName: "waiting_" + modelData.conversationId
                    width: list.width
                    height: 54
                    hoverEnabled: true
                    onClicked: { Approvals.openChat(modelData.conversationId); popup.close() }
                    background: Rectangle {
                        radius: 6
                        color: entry.hovered ? Theme.p.item_hover : "transparent"
                    }
                    contentItem: Item {
                        Icon {
                            x: 10
                            anchors.verticalCenter: parent.verticalCenter
                            name: entry.modelData.icon
                            tint: entry.modelData.destructive ? Theme.p.error : Theme.p.warn
                        }
                        Column {
                            x: 38
                            width: parent.width - x - age.width - 16
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                width: parent.width
                                text: entry.modelData.chat + (entry.modelData.here ? qsTr(" · открыт") : "")
                                elide: Text.ElideRight
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width
                                text: [entry.modelData.what, entry.modelData.more].filter(s => s !== "").join(" · ")
                                elide: Text.ElideRight
                                color: Theme.p.muted
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                        Text {
                            id: age
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: entry.modelData.age
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }
                }
            }

            Text {
                visible: Approvals.looseCount > 0
                height: 34
                leftPadding: 10
                bottomPadding: 5
                verticalAlignment: Text.AlignBottom
                text: qsTr("Без чата — внешние клиенты")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
            Column {
                objectName: "looseApprovals"
                width: parent.width
                spacing: 8
                Repeater {
                    model: Approvals.looseModel
                    delegate: ApprovalCard {
                        required property var model  // one request's fields, kept while other cards come and go
                        width: parent.width
                        height: implicitHeight
                        request: model
                        previewMax: 240
                    }
                }
            }
        }
    }
}
