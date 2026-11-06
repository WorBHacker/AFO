import QtQuick
import QtQuick.Controls.Basic

// A file over the editor's limit (CodeView.qml shows smaller ones as one text, 24.1): plain, with line numbers,
// scrolling both ways, in pieces of a few hundred lines (from Python) - only the pieces on screen and near it are laid
// out, so it opens at once. A selection with the mouse stays within one piece («Копировать содержимое» takes all).
ListView {
    id: view
    objectName: "codePieces"
    // {chunks: [{code, numbers, start}], columns, digits, key} (Projects.fileShown): another key starts at the top,
    // the same one (the file changed on disk) keeps the place
    property var source: ({})
    property bool showNumbers: true
    // a line to show (from a search): its offset in the text; a new `gotoSeq` asks again for the same place
    property int gotoOffset: -1
    property int gotoSeq: 0
    property string shownKey
    property int shownSeq: 0
    property real kept: -1
    readonly property real digitWidth: metrics.advanceWidth("0")
    readonly property real gutterWidth: showNumbers ? Math.ceil(digitWidth * (source.digits || 1)) + 30 : 0
    // the width comes from the longest line, not from implicitWidth: asking a TextEdit for its implicit width lays
    // the whole piece out once more
    readonly property real bodyWidth: Math.ceil(digitWidth * ((source.columns || 0) + 2)) + (showNumbers ? 4 : 20) + 24

    function settle() {
        if ((source.key || "") !== shownKey) {
            shownKey = source.key || ""
            contentX = originX
            positionViewAtBeginning()
        } else if (kept >= 0) {
            contentY = Math.max(originY, Math.min(kept, originY + contentHeight - height))
        }
        kept = -1
        if (gotoSeq !== shownSeq) {
            shownSeq = gotoSeq
            showLine()
        }
    }

    function showLine() {
        const chunks = source.chunks || []
        if (gotoOffset < 0 || !chunks.length) { mark.visible = false; return }
        let i = chunks.length - 1
        while (i > 0 && chunks[i].start > gotoOffset) i--
        positionViewAtIndex(i, ListView.Beginning)
        const piece = itemAtIndex(i)
        const at = gotoOffset - chunks[i].start
        if (!piece || at > piece.body.length) { mark.visible = false; return }
        const r = piece.body.positionToRectangle(at)
        const y = piece.y + r.y
        contentX = originX
        contentY = Math.max(originY, Math.min(y - height / 3, originY + contentHeight - height))
        mark.y = y
        mark.height = r.height
        mark.visible = true
        markFade.restart()
    }

    onSourceChanged: {
        if (kept < 0)
            kept = contentY
        model = source.chunks || []
        Qt.callLater(settle)
    }
    onGotoSeqChanged: Qt.callLater(settle)

    contentWidth: Math.max(width, gutterWidth + bodyWidth)
    flickableDirection: Flickable.AutoFlickDirection
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickDeceleration: 4000
    ScrollBar.vertical: ThinScrollBar {}
    ScrollBar.horizontal: ThinScrollBar {}

    FontMetrics { id: metrics; font.family: Theme.monoFont; font.pixelSize: 13 }

    Rectangle {
        id: mark
        objectName: "piecesMark"
        visible: false
        width: view.contentWidth
        color: Theme.p.selection
        opacity: 0.6
        Timer { id: markFade; interval: 2500; onTriggered: mark.visible = false }
    }

    // the margins above the first line and below the last are the list's own: every full piece is as tall, so the
    // list places the ones it has not made yet exactly (a line far down is where the scroll bar says)
    header: Item { width: 1; height: 14 }
    footer: Item { width: 1; height: 14 }

    delegate: Row {
        id: piece
        required property var modelData
        property alias body: body

        Text {
            visible: view.showNumbers
            width: view.gutterWidth  // padded numbers: every piece's gutter is as wide
            leftPadding: 16
            rightPadding: 14
            text: view.showNumbers ? piece.modelData.numbers : ""
            textFormat: Text.PlainText
            color: Theme.p.faint
            font.family: Theme.monoFont
            font.pixelSize: 13
        }
        TextEdit {
            id: body
            width: view.contentWidth - view.gutterWidth
            leftPadding: view.showNumbers ? 4 : 20
            rightPadding: 24
            readOnly: true
            selectByMouse: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.NoWrap
            text: piece.modelData.code
            color: Theme.p.text
            selectionColor: Theme.p.selection
            font.family: Theme.monoFont
            font.pixelSize: 13
        }
    }
}
