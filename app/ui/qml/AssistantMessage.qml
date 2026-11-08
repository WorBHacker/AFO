import QtQuick

// An answer: who answered, the reasoning accordion, a status line (model loading), what it looked at with the
// hub's tools, the text and code, a footer.
Column {
    id: answer
    property var row: ({})  // read as row?.x: while a delegate is torn down, its row is null for a moment
    property int rowIndex: -1
    spacing: 12
    // the chat's row now shows another answer (ChatView.reuseItems): its pieces are kept where they match, so
    // their selection, a code block's sideways scroll and «Скопировано» are cleared
    function reused() {
        for (let i = 0; i < parts.count; ++i) {
            const piece = parts.itemAt(i)
            if (piece && piece.item && piece.item.reused)
                piece.item.reused()
        }
    }

    Row {
        spacing: 8
        Rectangle {
            width: 22; height: 22; radius: 6
            color: answer.row?.local ? Theme.p.item_selected : Theme.p.accent
            Icon {
                anchors.centerIn: parent
                name: answer.row?.local ? "cpu" : "sparkle"
                size: 13
                tint: answer.row?.local ? Theme.p.text_soft : Theme.p.on_accent
            }
        }
        Text {
            objectName: "answerAuthor"
            width: Math.max(0, Math.min(implicitWidth, answer.width - 30 - (answering.visible ? answering.implicitWidth + 8 : 0)))
            elide: Text.ElideRight
            text: answer.row?.author || ""
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            id: answering
            visible: !!answer.row?.streaming
            text: qsTr("· отвечает")
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    Text {
        visible: !!answer.row?.notice
        width: parent.width
        wrapMode: Text.Wrap
        text: answer.row?.notice || ""
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Thinking {
        visible: !!answer.row?.thinking
        width: answer.width
        info: answer.row?.thinking || ({})
        rowIndex: answer.rowIndex
    }

    ToolCalls {
        visible: !!answer.row?.tools && answer.row?.tools.length > 0
        width: answer.width
        tools: answer.row?.tools || []
        rowIndex: answer.rowIndex
    }

    // The pieces live in a ListModel that changes only where the answer did: while an answer streams, its new
    // text re-renders the last piece alone. A Repeater over the plain list would rebuild every piece on each tick.
    property var segments: answer.row?.segments || []
    onSegmentsChanged: syncSegments()
    Component.onCompleted: syncSegments()
    function syncSegments() {
        const list = answer.segments
        for (let i = 0; i < list.length; ++i) {
            const piece = list[i]
            if (i >= pieces.count) {
                pieces.append(piece)
                continue
            }
            const old = pieces.get(i)
            if (old.kind !== piece.kind || old.html !== piece.html || old.code !== piece.code || old.lang !== piece.lang)
                pieces.set(i, piece)
        }
        if (pieces.count > list.length)
            pieces.remove(list.length, pieces.count - list.length)
    }
    ListModel { id: pieces }

    Repeater {
        id: parts
        model: pieces
        delegate: Loader {
            required property string kind
            required property string lang
            required property string html
            required property string code
            width: answer.width
            sourceComponent: kind === "code" ? codeSegment : proseSegment
            onLoaded: item.info = Qt.binding(() => ({ kind, lang, html, code }))
        }
    }
    Component { id: proseSegment; Prose { width: parent ? parent.width : 0 } }
    Component { id: codeSegment; CodeBlock { width: parent ? parent.width : 0 } }

    Row {
        visible: !answer.row?.streaming && !!answer.row?.footer
        spacing: 8
        Text {
            width: Math.max(0, Math.min(implicitWidth, answer.width - 60))
            elide: Text.ElideRight
            text: answer.row?.footer || ""
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
        }
        IconButton {
            iconName: "copy"
            tint: Theme.p.faint
            implicitWidth: 22
            implicitHeight: 22
            tip: qsTr("Копировать ответ")
            onClicked: App.copy(answer.row?.markdown || "")
        }
    }
}
