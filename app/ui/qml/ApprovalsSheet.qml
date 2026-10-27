import QtQuick
import QtQuick.Controls.Basic

// The approval queue (D17, D18): what agents wait to be allowed - a command, a file edit (as a diff), an address -
// oldest first. Allow once, allow for the rest of the chat (not offered for destructive commands), or deny with a
// comment the agent reads.
Sheet {
    id: sheet
    objectName: "approvalsSheet"
    title: "Подтверждения"
    iconName: "shield"
    width: Math.min(720, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Approvals.load()

    Text {
        visible: Approvals.count === 0
        topPadding: 24
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: "Никто ничего не ждёт. Когда Claude Code захочет выполнить команду или поправить файл, запрос появится здесь."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    Column {
        objectName: "approvalItems"
        width: parent.width
        spacing: 14
        Repeater {
            model: Approvals.items
            delegate: Rectangle {
                id: card
                required property var modelData
                width: parent.width
                height: body.height + 28
                radius: 8
                color: Theme.p.card
                border.color: modelData.destructive ? Theme.p.error_line : Theme.p.hairline

                Column {
                    id: body
                    x: 14
                    y: 14
                    width: parent.width - 28
                    spacing: 10

                    Item {
                        width: parent.width
                        height: Math.max(heading.height, 20)
                        Icon {
                            id: mark
                            name: card.modelData.icon
                            size: 16
                            tint: card.modelData.destructive ? Theme.p.error : Theme.p.text_soft
                            y: 2
                        }
                        Column {
                            id: heading
                            x: 26
                            width: parent.width - x - when.width - 10
                            spacing: 2
                            Text {
                                width: parent.width
                                text: card.modelData.who + ": " + card.modelData.title
                                wrapMode: Text.Wrap
                                maximumLineCount: 3
                                elide: Text.ElideRight
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width
                                visible: text !== ""
                                text: card.modelData.where
                                elide: Text.ElideRight
                                color: Theme.p.faint
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                        Text {
                            id: when
                            anchors.right: parent.right
                            text: card.modelData.time
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }

                    Text {
                        width: parent.width
                        visible: card.modelData.destructive
                        text: "Разрушительное действие: спрашивается каждый раз, разрешить можно только этот раз."
                        wrapMode: Text.Wrap
                        color: Theme.p.error
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Text {
                        width: parent.width
                        visible: text !== ""
                        text: card.modelData.note
                        wrapMode: Text.Wrap
                        color: Theme.p.muted
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }

                    // the command, the diff or the input
                    Rectangle {
                        width: parent.width
                        height: Math.min(lines.height + 16, 320)
                        radius: 6
                        color: Theme.p.code_bg
                        border.color: Theme.p.hairline
                        Flickable {
                            anchors.fill: parent
                            anchors.margins: 8
                            contentWidth: lines.width
                            contentHeight: lines.height
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ThinScrollBar {}
                            ScrollBar.horizontal: ThinScrollBar {}
                            Column {
                                id: lines
                                Repeater {
                                    model: card.modelData.lines
                                    delegate: Text {
                                        required property var modelData
                                        text: modelData.text === "" ? " " : modelData.text
                                        textFormat: Text.PlainText
                                        color: modelData.mark === "add" ? Theme.p.ok
                                               : modelData.mark === "del" ? Theme.p.error
                                               : modelData.mark === "hunk" ? Theme.p.accent
                                               : modelData.mark === "file" ? Theme.p.faint : Theme.p.text_soft
                                        font.family: Theme.monoFont
                                        font.pixelSize: 12
                                    }
                                }
                            }
                        }
                    }

                    Field {
                        id: comment
                        objectName: "approvalComment"
                        width: parent.width
                        placeholderText: "Комментарий к отказу — его прочтёт агент (необязательно)"
                    }

                    Row {
                        spacing: 8
                        PlainButton {
                            objectName: "approveOnce"
                            kind: "primary"
                            iconName: "check"
                            text: "Разрешить"
                            onClicked: Approvals.answer(card.modelData.id, true, "once", "")
                        }
                        PlainButton {
                            objectName: "approveChat"
                            visible: !card.modelData.destructive && card.modelData.conversationId !== ""
                            text: "Разрешить: " + card.modelData.chatScope
                            onClicked: Approvals.answer(card.modelData.id, true, "chat", "")
                        }
                        PlainButton {
                            objectName: "deny"
                            kind: "danger"
                            iconName: "close"
                            text: "Отклонить"
                            onClicked: Approvals.answer(card.modelData.id, false, "once", comment.text)
                        }
                    }
                }
            }
        }
    }
}
