import QtQuick
import QtQuick.Controls.Basic

// A file's text with line numbers, scrolling both ways, selectable. Rich text when coloured, plain text otherwise;
// the numbers are plain text either way (a plain line is as tall as a line of the coloured <pre>).
Flickable {
    id: view
    property string code
    property string numbers
    property bool rich
    property int columns  // the longest line, in characters (from Python)
    property bool showNumbers: true
    // a line to show (from a search): its offset in the text; a new `gotoSeq` asks again for the same place
    property int gotoOffset: -1
    property int gotoSeq: 0

    function showLine() {
        if (gotoOffset < 0 || gotoOffset > body.length) { mark.visible = false; return }
        const r = body.positionToRectangle(gotoOffset)
        contentX = 0
        contentY = Math.max(0, Math.min(r.y - height / 3, contentHeight - height))
        mark.y = r.y
        mark.height = r.height
        mark.visible = true
        markFade.restart()
    }
    onGotoSeqChanged: Qt.callLater(showLine)

    contentWidth: row.width
    contentHeight: row.height
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickDeceleration: 4000
    ScrollBar.vertical: ThinScrollBar {}
    ScrollBar.horizontal: ThinScrollBar {}

    Rectangle {
        id: mark
        objectName: "gotoMark"
        visible: false
        width: Math.max(view.width, row.width)
        color: Theme.p.selection
        opacity: 0.6
        Timer { id: markFade; interval: 2500; onTriggered: mark.visible = false }
    }

    Row {
        id: row
        Text {
            id: gutter
            visible: view.showNumbers
            topPadding: 14
            bottomPadding: 14
            leftPadding: 16
            rightPadding: 14
            text: view.showNumbers ? view.numbers : ""
            textFormat: Text.PlainText  // as tall a line as the coloured code's, at a fraction of the cost
            color: Theme.p.faint
            font.family: Theme.monoFont
            font.pixelSize: 13
        }
        // The width comes from the longest line, not from implicitWidth: asking a TextEdit for its implicit width
        // lays the whole document out once more (a 2700-line file opened in 0.5 s instead of ~0.2 s).
        FontMetrics { id: metrics; font: body.font }
        TextEdit {
            id: body
            width: Math.max(Math.ceil(metrics.advanceWidth("0") * (view.columns + 2)) + leftPadding + rightPadding,
                            view.width - (gutter.visible ? gutter.width : 0))
            topPadding: 14
            bottomPadding: 14
            leftPadding: view.showNumbers ? 4 : 20
            rightPadding: 24
            readOnly: true
            selectByMouse: true
            textFormat: view.rich ? TextEdit.RichText : TextEdit.PlainText
            wrapMode: TextEdit.NoWrap
            text: view.code
            color: Theme.p.text
            selectionColor: Theme.p.selection
            font.family: Theme.monoFont
            font.pixelSize: 13
        }
    }
}
