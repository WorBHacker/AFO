import QtQuick
import QtQuick.Controls.Basic

// One approval request (D17, D18, D38): who asks what, the warning (destructive, network), a short preview - the
// command, the first lines of a diff - that "Показать всё" opens inside the card, a comment for a denial, and the
// answers. Keys while the card has focus: Ctrl+Enter allows once (never a destructive action: only its button
// does), Esc goes to the comment, Enter in the comment denies with it.
FocusScope {
    id: card
    property var request: ({})  // a row of Approvals: a map, or a list model's row with the same names
    property string position: ""  // "1 из 3" when more of this chat wait
    property real previewMax: 360  // the open preview's height at most (never below the closed one's)
    readonly property int shortLines: 12
    property bool expanded: false
    readonly property string requestId: request && request.id ? request.id : ""
    readonly property var lines: request && request.lines ? request.lines : []
    readonly property bool destructive: !!(request && request.destructive)

    implicitHeight: body.height + 28
    // another request in the card: its own comment, its preview closed
    onRequestIdChanged: { comment.text = ""; expanded = false }

    function allowOnce() { if (requestId) Approvals.answer(requestId, true, "once", "") }
    function deny() { if (requestId) Approvals.answer(requestId, false, "once", comment.text) }

    Keys.onPressed: (event) => {
        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            event.accepted = true
            if (!card.destructive) card.allowOnce()
        } else if (event.key === Qt.Key_Escape) {
            event.accepted = true
            comment.forceActiveFocus()
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: Theme.p.card
        border.color: card.destructive ? Theme.p.error_line : card.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
    }

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
                name: card.request.icon || "shield"
                size: 16
                tint: card.destructive ? Theme.p.error : Theme.p.warn
                y: 2
            }
            Column {
                id: heading
                x: 26
                width: parent.width - x - side.width - 10
                spacing: 2
                Text {
                    objectName: "approvalTitle"
                    width: parent.width
                    text: (card.request.who || "") + ": " + (card.request.title || "")
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
                    text: card.request.where || ""
                    elide: Text.ElideRight
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
            Text {
                id: side
                objectName: "approvalPosition"
                anchors.right: parent.right
                text: [card.position, card.request.time || ""].filter(s => s !== "").join(" · ")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }

        Text {
            width: parent.width
            visible: (card.request.alwaysAsk || "") !== ""
            text: card.request.alwaysAsk === "network"
                  ? "Команда обращается к сети: спрашивается каждый раз, разрешить можно только этот раз."
                  : card.request.alwaysAsk === "script"
                  ? "Скрипт Python: выполняется от вашего имени — проверьте код. Спрашивается каждый раз."
                  : card.request.alwaysAsk === "outside"
                  ? "Инструмент внешнего MCP-сервера обращается к сети: спрашивается каждый раз, разрешить можно только этот раз."
                  : card.request.alwaysAsk === "send"
                  ? "Отправляет от вашего имени: спрашивается каждый раз, разрешить можно только этот раз."
                  : card.request.alwaysAsk === "personal"
                  ? "Личные данные: агент запомнит их только с вашего разрешения — спрашивается каждый раз."
                  : card.request.alwaysAsk === "hub"
                  ? "Команда обращается к данным самого хаба — токенам, базе, журналам: то, что она выведет, увидит модель. Спрашивается каждый раз, разрешить можно только этот раз."
                  : card.request.alwaysAsk === "opaque"
                  ? "Из текста команды не видно, что она запустит: имя в переменной, псевдоним или код из строки. Спрашивается каждый раз, разрешить можно только этот раз и только кнопкой."
                  : "Разрушительное действие: спрашивается каждый раз, разрешить можно только этот раз и только кнопкой."
            wrapMode: Text.Wrap
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Text {
            objectName: "approvalHidden"
            width: parent.width
            visible: text !== ""
            text: card.request.hidden ? "⚠ " + card.request.hidden : ""
            wrapMode: Text.Wrap
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            visible: text !== ""
            text: card.request.note || ""
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }

        // the command, the diff or the input: a few lines, all of them on "Показать всё" (scrolling past the height)
        Rectangle {
            visible: card.lines.length > 0
            width: parent.width
            height: Math.min(lines.height, lines.lineHeight * card.shortLines
                                           * (card.expanded ? Math.max(1, card.previewMax / (lines.lineHeight * card.shortLines)) : 1)) + 16
            radius: 6
            color: Theme.p.code_bg
            border.color: Theme.p.hairline
            Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            Flickable {
                id: flick
                anchors.fill: parent
                anchors.margins: 8
                contentWidth: lines.width
                contentHeight: lines.height
                clip: true
                interactive: card.expanded || contentWidth > width
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ThinScrollBar {}
                ScrollBar.horizontal: ThinScrollBar {}
                Column {
                    id: lines
                    objectName: "approvalLines"
                    readonly property real lineHeight: metrics.height
                    FontMetrics { id: metrics; font.family: Theme.monoFont; font.pixelSize: 12 }
                    Repeater {
                        // closed, only the lines it shows are made
                        model: card.expanded ? card.lines : card.lines.slice(0, card.shortLines)
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
        Text {
            objectName: "approvalShowAll"
            visible: card.lines.length > card.shortLines
            text: "<a href=\"x\">" + (card.expanded ? "Свернуть" : "Показать всё (" + card.lines.length + " строк)") + "</a>"
            textFormat: Text.StyledText
            linkColor: Theme.p.link
            font.family: Theme.uiFont
            font.pixelSize: 12
            onLinkActivated: { card.expanded = !card.expanded; flick.contentY = 0 }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }

        Field {
            id: comment
            objectName: "approvalComment"
            width: parent.width
            placeholderText: "Комментарий к отказу — его прочтёт агент (необязательно)"
            Keys.onPressed: (event) => {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    event.accepted = true  // Ctrl+Enter here never allows: the user is writing why not
                    if (!(event.modifiers & Qt.ControlModifier)) card.deny()
                } else if (event.key === Qt.Key_Escape) {
                    event.accepted = true
                    card.forceActiveFocus()
                }
            }
        }

        Flow {
            width: parent.width
            spacing: 8
            PlainButton {
                objectName: "approveOnce"
                kind: "primary"
                iconName: "check"
                text: "Разрешить"
                onClicked: card.allowOnce()
            }
            PlainButton {
                objectName: "approveChat"
                visible: (card.request.alwaysAsk || "") === "" && (card.request.conversationId || "") !== ""
                text: "Разрешить: " + (card.request.chatScope || "")
                onClicked: Approvals.answer(card.requestId, true, "chat", "")
            }
            PlainButton {
                objectName: "approveProject"
                visible: (card.request.projectScope || "") !== ""
                text: "Разрешить " + (card.request.projectScope || "")
                onClicked: Approvals.answer(card.requestId, true, "project", "")
            }
            PlainButton {
                objectName: "deny"
                kind: "danger"
                iconName: "close"
                text: "Отклонить"
                onClicked: card.deny()
            }
        }
        Text {
            width: parent.width
            text: comment.activeFocus ? "Enter — отклонить с комментарием · Esc — назад"
                  : card.destructive ? "Esc — комментарий к отказу"
                  : "Ctrl+Enter — разрешить один раз · Esc — комментарий к отказу"
            visible: card.activeFocus
            elide: Text.ElideRight
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 11
        }
    }
}
