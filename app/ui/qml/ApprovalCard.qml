import QtQuick
import QtQuick.Controls.Basic

// One approval request (D17, D18, D38): who asks what, the warning (destructive, network), a short preview - the
// command, the first lines of a diff - that "Показать всё" opens inside the card, a comment for a denial, and the
// answers. Keys while the card has focus: Ctrl+Enter allows once (never a destructive action: only its button
// does), Esc goes to the comment, Enter in the comment denies with it. A write of several hunks (17.3) shows each
// with a tick: a digit ticks one; with some unticked the rest is allowed this once, the comment saying why.
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
    readonly property var hunks: request && request.hunks ? request.hunks : []
    readonly property bool partial: hunks.length > 0 && request.ticked < hunks.length

    implicitHeight: body.height + 28
    // another request in the card: its own comment, its preview closed
    onRequestIdChanged: { comment.text = ""; expanded = false }

    function allowOnce() {
        if (requestId && !(hunks.length > 0 && request.ticked === 0))
            Approvals.answer(requestId, true, "once", partial ? comment.text : "")
    }
    function deny() { if (requestId) Approvals.answer(requestId, false, "once", comment.text) }

    Keys.onPressed: (event) => {
        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            event.accepted = true
            if (!card.destructive) card.allowOnce()
        } else if (event.key === Qt.Key_Escape) {
            event.accepted = true
            comment.forceActiveFocus()
        } else if (event.key >= Qt.Key_1 && event.key <= Qt.Key_9 && event.key - Qt.Key_1 < card.hunks.length) {
            event.accepted = true
            Approvals.tickHunk(card.requestId, card.hunks[event.key - Qt.Key_1].index)
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
            text: card.request.alwaysAsk === "unsaved"
                  ? qsTr("Этот файл открыт в редакторе с несохранёнными правками. Если разрешить, файл на диске изменится, а над вашими правками появится полоса «Файл изменился на диске». Спрашивается каждый раз, в «Доверенном» тоже.")
                  : card.request.alwaysAsk === "network"
                  ? qsTr("Команда обращается к сети: спрашивается каждый раз, разрешить можно только этот раз.")
                  : card.request.alwaysAsk === "script"
                  ? qsTr("Скрипт Python в контейнере Windows: видит только свою папку, в сеть — только если задача это разрешает. Проверьте код. Спрашивается каждый раз.")
                  : card.request.alwaysAsk === "outside"
                  ? qsTr("Инструмент внешнего MCP-сервера обращается к сети: спрашивается каждый раз, разрешить можно только этот раз.")
                  : card.request.alwaysAsk === "send"
                  ? qsTr("Отправляет от вашего имени: спрашивается каждый раз, разрешить можно только этот раз.")
                  : card.request.alwaysAsk === "personal"
                  ? qsTr("Личные данные: агент запомнит их только с вашего разрешения — спрашивается каждый раз.")
                  : card.request.alwaysAsk === "hub"
                  ? qsTr("Команда обращается к данным самого хаба — токенам, базе, журналам: то, что она выведет, увидит модель. Спрашивается каждый раз, разрешить можно только этот раз.")
                  : card.request.alwaysAsk === "opaque"
                  ? qsTr("Из текста команды не видно, что она запустит: имя в переменной, псевдоним или код из строки. Спрашивается каждый раз, разрешить можно только этот раз и только кнопкой.")
                  : qsTr("Разрушительное действие: спрашивается каждый раз, разрешить можно только этот раз и только кнопкой.")
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
            height: Math.min(flick.contentHeight, lines.lineHeight * card.shortLines
                                           * (card.expanded ? Math.max(1, card.previewMax / (lines.lineHeight * card.shortLines)) : 1)) + 16
            radius: 6
            color: Theme.p.code_bg
            border.color: Theme.p.hairline
            Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            Flickable {
                id: flick
                anchors.fill: parent
                anchors.margins: 8
                contentWidth: card.hunks.length ? parts.width : lines.width
                contentHeight: card.hunks.length ? parts.height : lines.height
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
                        // closed, only the lines it shows are made; none when the hunks show instead
                        model: card.hunks.length ? [] : card.expanded ? card.lines : card.lines.slice(0, card.shortLines)
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
                // a write's hunks (17.3): each with its tick and number - the digit that ticks it
                Column {
                    id: parts
                    objectName: "approvalHunks"
                    visible: card.hunks.length > 0
                    spacing: 6
                    Repeater {
                        model: card.hunks
                        delegate: Column {
                            id: part
                            required property var modelData
                            Row {
                                spacing: 6
                                AbstractButton {
                                    objectName: "approvalHunk_" + part.modelData.index
                                    width: 15
                                    height: 15
                                    anchors.verticalCenter: parent.verticalCenter
                                    focusPolicy: Qt.NoFocus  // the card keeps the keyboard
                                    onClicked: Approvals.tickHunk(card.requestId, part.modelData.index)
                                    background: Rectangle {
                                        radius: 3
                                        color: part.modelData.ticked ? Theme.p.item_selected : "transparent"
                                        border.color: part.modelData.ticked ? Theme.p.accent : Theme.p.hairline_strong
                                    }
                                    contentItem: Item {
                                        Icon {
                                            anchors.centerIn: parent
                                            visible: part.modelData.ticked
                                            name: "check"
                                            size: 11
                                            tint: Theme.p.accent
                                        }
                                    }
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: part.modelData.number + "  " + part.modelData.header
                                    textFormat: Text.PlainText
                                    color: part.modelData.ticked ? Theme.p.accent : Theme.p.faint
                                    font.family: Theme.monoFont
                                    font.pixelSize: 12
                                }
                            }
                            Repeater {
                                model: part.modelData.lines
                                delegate: Text {
                                    required property var modelData
                                    text: modelData.text === "" ? " " : modelData.text
                                    textFormat: Text.PlainText
                                    opacity: part.modelData.ticked ? 1 : 0.45
                                    color: modelData.mark === "add" ? Theme.p.ok
                                           : modelData.mark === "del" ? Theme.p.error : Theme.p.text_soft
                                    font.family: Theme.monoFont
                                    font.pixelSize: 12
                                }
                            }
                        }
                    }
                }
            }
        }
        Text {
            objectName: "approvalShowAll"
            visible: card.lines.length > card.shortLines
            text: "<a href=\"x\">" + (card.expanded ? qsTr("Свернуть") : qsTr("Показать всё (%1 строк)").arg(card.lines.length)) + "</a>"
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
            placeholderText: card.partial ? qsTr("Почему не всё — это прочтёт агент (необязательно)")
                                          : qsTr("Комментарий к отказу — его прочтёт агент (необязательно)")
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
                maxWidth: parent.width
                kind: "primary"
                iconName: "check"
                text: card.request.allowText || qsTr("Разрешить")
                enabled: !(card.hunks.length > 0 && card.request.ticked === 0)
                onClicked: card.allowOnce()
            }
            PlainButton {
                objectName: "approveChat"
                maxWidth: parent.width
                visible: (card.request.alwaysAsk || "") === "" && (card.request.conversationId || "") !== "" && !card.partial
                text: qsTr("Разрешить: %1").arg(card.request.chatScope || "")
                onClicked: Approvals.answer(card.requestId, true, "chat", "")
            }
            PlainButton {
                objectName: "approveProject"
                maxWidth: parent.width
                visible: (card.request.projectScope || "") !== "" && !card.partial
                text: qsTr("Разрешить %1").arg(card.request.projectScope || "")
                onClicked: Approvals.answer(card.requestId, true, "project", "")
            }
            PlainButton {
                objectName: "deny"
                kind: "danger"
                iconName: "close"
                text: qsTr("Отклонить")
                onClicked: card.deny()
            }
        }
        Text {
            width: parent.width
            text: comment.activeFocus ? qsTr("Enter — отклонить с комментарием · Esc — назад")
                  : card.destructive ? qsTr("Esc — комментарий к отказу")
                  : card.hunks.length ? qsTr("Ctrl+Enter — разрешить отмеченное · 1–9 — галочка у фрагмента · Esc — комментарий")
                  : qsTr("Ctrl+Enter — разрешить один раз · Esc — комментарий к отказу")
            visible: card.activeFocus
            elide: Text.ElideRight
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 11
        }
    }
}
