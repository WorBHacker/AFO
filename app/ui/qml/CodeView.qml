import QtQuick
import QtQuick.Controls.Basic

// A file's text, or a diff, for reading (24.1): up to the editor's limit (1 MB) one read-only TextEdit - selected
// through the whole of it, coloured as the editor colours (Readers, app/ui/reader.py: the lines on screen first, the
// rest when the screen gets there), numbers for the lines on screen only; a bigger file in pieces, plain
// (CodePieces.qml). A line to show (from a search) is scrolled to and marked for a moment. The same key again (the
// file changed on disk) keeps the place; another starts at the top.
//
// A new text goes in step by step: the old one out and the place at the top at once, the new text at the next turn
// of the event loop, its place and its line after that. Put in in the same go as a jump of the place - from far down
// a long file to a short one - the text was not drawn (Qt draws only the visible part of a long text and kept the
// old part's bounds) until its width changed. Out of sight (the editor over it, another view) it does nothing: the
// text waits, the colours stop - shown again, it goes on.
Item {
    id: view
    // {text | chunks, lexer, columns, digits, key} (Projects.fileShown, Editor.compareView)
    property var source: ({})
    property string reader: "file"  // its name in Readers: its colours
    property bool showNumbers: true
    // a line to show (from 1); a new `gotoSeq` asks again for the same place (the pieces go by its offset)
    property int gotoLine: 0
    property int gotoOffset: -1
    property int gotoSeq: 0
    property string shownKey
    property int shownSeq: 0
    property int stage: 0  // 1 - the new text on its way in, 2 - its place on its way; the line to show waits
    property var place: [0, 0]  // where the text goes back to once it is in (contentX, contentY)
    readonly property bool whole: source.text !== undefined
    // a line's height - never under the font's: while a text is replaced its height and its line count change one
    // after the other, and 17 px over 3000 lines put 100 000 numbers in the gutter for a moment (7 s, 24.1)
    readonly property real lineHeight: Math.max(metrics.height, body.lineCount > 0 ? body.contentHeight / body.lineCount : 0)
    readonly property real gutterWidth: Math.ceil(metrics.advanceWidth("0") * Math.max(3, String(body.lineCount).length)) + 28
    readonly property int firstLine: Math.max(0, Math.floor((flick.contentY - body.topPadding) / lineHeight))
    readonly property int screenLines: Math.ceil(flick.height / lineHeight) + 1
    onFirstLineChanged: if (whole) Readers.screen(reader, firstLine, screenLines)

    function load() {
        const key = source.key || ""
        if (key !== shownKey) {
            place = [0, 0]
            mark.visible = false
        } else if (stage === 0) {
            place = [flick.contentX, flick.contentY]
        }
        shownKey = key
        body.deselect()
        body.text = ""  // a long text let go (a bigger file is in pieces: `source.text` is not there)
        flick.contentX = 0
        flick.contentY = 0
        stage = source.text !== undefined ? 1 : 0  // not `whole`: its binding may not have followed `source` yet
        if (stage)
            next.restart()
    }

    // the next step, at the next turn of the event loop
    function step() {
        if (!visible)
            return
        if (stage === 1 && source.text !== undefined) {
            body.text = source.text
            stage = 2
            next.restart()
        } else if (stage === 2) {
            stage = 0
            flick.contentX = Math.max(0, Math.min(place[0], flick.contentWidth - flick.width))
            flick.contentY = Math.max(0, Math.min(place[1], flick.contentHeight - flick.height))
            settle()
        } else {
            settle()
        }
    }

    function settle() {
        if (source.text === undefined || stage !== 0)
            return
        if (gotoSeq !== shownSeq) {
            shownSeq = gotoSeq
            showLine()
        }
        Readers.shown(reader, source.lexer || "", firstLine, screenLines)
    }

    function showLine() {
        const at = gotoLine > 0 ? Readers.lineStart(reader, gotoLine) : -1
        if (at < 0) { mark.visible = false; return }
        const r = body.positionToRectangle(at)
        flick.contentX = 0
        flick.contentY = Math.max(0, Math.min(r.y - flick.height / 3, flick.contentHeight - flick.height))
        mark.y = r.y
        mark.height = r.height
        mark.visible = true
        markFade.restart()
    }

    // a selection going past the screen (the mouse dragged out, Shift and the keys) takes the text along
    function follow() {
        if (!body.activeFocus || body.selectionStart === body.selectionEnd)
            return
        const r = body.cursorRectangle
        if (flick.contentX > r.x - 8) flick.contentX = Math.max(0, r.x - 8)
        else if (flick.contentX + flick.width < r.x + r.width + 24) flick.contentX = r.x + r.width + 24 - flick.width
        if (flick.contentY > r.y) flick.contentY = r.y
        else if (flick.contentY + flick.height < r.y + r.height) flick.contentY = r.y + r.height - flick.height
    }

    onSourceChanged: load()
    onGotoSeqChanged: next.restart()
    onVisibleChanged: {
        if (visible)
            next.restart()
        else
            Readers.hidden(reader)
    }

    Timer { id: next; interval: 0; onTriggered: view.step() }
    FontMetrics { id: metrics; font.family: Theme.monoFont; font.pixelSize: 13 }

    // by `numbered`, not `visible` (the view's own too: out of sight, the gutter's width went to 0, the text's width
    // changed - and 1 MB was laid out again, 0.4 s, each time the editor came over the viewer and went)
    Item {
        id: gutter
        readonly property bool numbered: view.whole && view.showNumbers
        visible: numbered
        width: numbered ? view.gutterWidth : 0
        height: parent.height
        clip: true
        Repeater {
            objectName: "lineNumbers"
            model: gutter.numbered ? Math.ceil(flick.height / view.lineHeight) + 2 : 0
            delegate: Text {
                required property int index
                readonly property int line: view.firstLine + index
                visible: line < body.lineCount
                y: line * view.lineHeight + body.topPadding - flick.contentY
                width: gutter.width - 14
                height: view.lineHeight
                horizontalAlignment: Text.AlignRight
                verticalAlignment: Text.AlignVCenter
                text: line + 1
                color: Theme.p.faint
                font.family: Theme.monoFont
                font.pixelSize: 13
            }
        }
    }

    Flickable {
        id: flick
        objectName: "codeFlick"
        visible: view.whole
        x: gutter.width
        width: parent.width - x
        height: parent.height
        clip: true
        contentWidth: Math.max(width, body.contentWidth + body.leftPadding + body.rightPadding)
        contentHeight: Math.max(height, body.contentHeight + body.topPadding + body.bottomPadding)
        flickableDirection: Flickable.AutoFlickDirection
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 4000
        ScrollBar.vertical: ThinScrollBar {}
        ScrollBar.horizontal: ThinScrollBar {}

        Rectangle {
            id: mark
            objectName: "gotoMark"
            visible: false
            width: flick.contentWidth
            color: Theme.p.selection
            opacity: 0.6
            Timer { id: markFade; interval: 2500; onTriggered: mark.visible = false }
        }

        TextEdit {
            id: body
            objectName: "codeText"
            width: flick.contentWidth
            height: flick.contentHeight
            leftPadding: view.showNumbers ? 4 : 20
            rightPadding: 24
            topPadding: 14
            bottomPadding: 14
            readOnly: true
            selectByMouse: true
            selectByKeyboard: true
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.NoWrap
            tabStopDistance: metrics.advanceWidth(" ") * 4
            color: Theme.p.text
            selectionColor: Theme.p.selection
            selectedTextColor: Theme.p.text
            font.family: Theme.monoFont
            font.pixelSize: 13
            Component.onCompleted: Readers.attach(view.reader, textDocument)
            onCursorRectangleChanged: view.follow()
        }
    }

    CodePieces {
        anchors.fill: parent
        visible: !view.whole && !!view.source.chunks
        source: view.whole ? ({}) : view.source
        showNumbers: view.showNumbers
        gotoOffset: view.gotoOffset
        gotoSeq: view.gotoSeq
    }
}
