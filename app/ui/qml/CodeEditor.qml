import QtQuick
import QtQuick.Controls.Basic

// A tab's text, edited (D47, steps 14.2, 14.4): a TextEdit in a Flickable, numbers for the lines on screen only,
// the cursor's line marked; one of these per tab (ProjectView.qml), the shown one visible - each keeps its document
// and its undo history. Its document is its tab's (Editor.attach): Enter keeps the indent, Tab and Shift+Tab
// indent the selected lines - done there, one undo step each; Ctrl+Z / Ctrl+Y are the TextEdit's own; Ctrl+S saves
// through the hub. A pause in typing tells the editor to compare with the saved text («не сохранено»). The colours
// (app/ui/highlighter.py) are laid on the document's lines from Python: the ones on screen first. Ctrl+F and Ctrl+H:
// the find bar (FindBar.qml, 14.3), its matches on screen drawn behind the text.
Item {
    id: editor
    objectName: "codeEditor"
    property string tabKey  // the tab's: its project and path
    readonly property alias body: body
    readonly property real lineHeight: body.lineCount > 0 ? body.contentHeight / body.lineCount : metrics.height
    readonly property real gutterWidth: Math.ceil(metrics.advanceWidth("0") * Math.max(3, String(body.lineCount).length)) + 24
    readonly property int firstLine: Math.max(0, Math.floor((flick.contentY - body.topPadding) / lineHeight))
    readonly property int cursorLine: Math.floor((body.cursorRectangle.y - body.topPadding) / lineHeight)
    readonly property int screenLines: Math.ceil(flick.height / lineHeight) + 1
    onFirstLineChanged: Editor.screen(tabKey, firstLine, screenLines)  // the colours on screen first (14.2b)

    function focusText() { body.forceActiveFocus() }

    // the cursor stays on screen as it moves (typing, arrows, a jump)
    function keepInView() {
        const r = body.cursorRectangle
        if (flick.contentX > r.x - 8) flick.contentX = Math.max(0, r.x - 8)
        else if (flick.contentX + flick.width < r.x + r.width + 24) flick.contentX = r.x + r.width + 24 - flick.width
        if (flick.contentY > r.y) flick.contentY = r.y
        else if (flick.contentY + flick.height < r.y + r.height) flick.contentY = r.y + r.height - flick.height
    }

    FontMetrics { id: metrics; font: body.font }

    Rectangle { anchors.fill: parent; color: Theme.p.code_bg }

    Item {
        id: gutter
        width: editor.gutterWidth
        height: parent.height
        clip: true
        Repeater {
            model: Math.ceil(flick.height / editor.lineHeight) + 2
            delegate: Text {
                required property int index
                readonly property int line: editor.firstLine + index
                visible: line < body.lineCount
                y: line * editor.lineHeight + body.topPadding - flick.contentY
                width: gutter.width - 12
                height: editor.lineHeight
                horizontalAlignment: Text.AlignRight
                verticalAlignment: Text.AlignVCenter
                text: line + 1
                color: line === editor.cursorLine ? Theme.p.text_soft : Theme.p.faint
                font.family: Theme.monoFont
                font.pixelSize: 13
            }
        }
    }

    Flickable {
        id: flick
        x: gutter.width
        width: parent.width - x
        height: parent.height
        clip: true
        contentWidth: Math.max(width, body.contentWidth + body.leftPadding + 40)
        contentHeight: Math.max(height, body.contentHeight + body.topPadding + height / 2)
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        ScrollBar.horizontal: ThinScrollBar {}

        Rectangle {  // the cursor's line
            y: body.cursorRectangle.y
            width: flick.contentWidth
            height: body.cursorRectangle.height
            color: Theme.p.item_hover
            visible: body.activeFocus && body.selectionStart === body.selectionEnd
        }

        Repeater {  // the find bar's matches on screen (14.3), behind the text: a piece of a line each
            model: editor.visible && Finder.active ? Finder.marks(editor.firstLine, editor.screenLines,
                                                                  body.selectionStart, body.selectionEnd,
                                                                  Finder.revision) : []
            delegate: Rectangle {
                required property var modelData
                readonly property rect from: body.positionToRectangle(modelData[0])
                readonly property rect to: body.positionToRectangle(modelData[1])
                objectName: "findMark"
                x: from.x
                y: from.y
                width: Math.max(2, to.x - from.x)
                height: from.height
                radius: 2
                color: modelData[2] ? Theme.p.find_current : Theme.p.find_match
            }
        }

        TextEdit {
            id: body
            objectName: "editorText"
            width: flick.contentWidth
            height: flick.contentHeight
            textFormat: TextEdit.PlainText
            wrapMode: TextEdit.NoWrap
            selectByMouse: true
            persistentSelection: true
            leftPadding: 6
            topPadding: 6
            color: Theme.p.text
            selectionColor: Theme.p.selection
            selectedTextColor: Theme.p.text
            font.family: Theme.monoFont
            font.pixelSize: 13
            readonly property string unit: Editor.unitOf(editor.tabKey)
            tabStopDistance: metrics.advanceWidth(" ") * (unit === "\t" ? 4 : Math.max(1, unit.length))
            Component.onCompleted: Editor.attach(editor.tabKey, textDocument)
            onTextChanged: idle.restart()
            onCursorRectangleChanged: editor.keepInView()
            onCursorPositionChanged: Editor.locate(editor.tabKey, cursorPosition)
            Keys.onPressed: (event) => {
                const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
                if (ctrl && event.key === Qt.Key_S) {
                    idle.stop()
                    Editor.check(editor.tabKey)
                    Editor.save()
                    event.accepted = true
                } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !ctrl) {
                    body.cursorPosition = Editor.newline(body.selectionStart, body.selectionEnd)
                    event.accepted = true
                } else if ((event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) && !ctrl) {
                    const range = Editor.tab(body.selectionStart, body.selectionEnd, event.key === Qt.Key_Backtab
                                             || (event.modifiers & Qt.ShiftModifier) !== 0)
                    if (range[0] === range[1]) body.cursorPosition = range[0]
                    else body.select(range[0], range[1])
                    event.accepted = true
                } else if (ctrl && (event.key === Qt.Key_F || event.key === Qt.Key_H)) {
                    findBar.open(event.key === Qt.Key_H)
                    event.accepted = true
                } else if (event.key === Qt.Key_F3) {
                    if (Finder.active) findBar.go((event.modifiers & Qt.ShiftModifier) !== 0)
                    else findBar.open(false)
                    event.accepted = true
                } else if (event.key === Qt.Key_Escape && Finder.active) {
                    findBar.close()
                    event.accepted = true
                }
            }
        }
    }

    FindBar {
        id: findBar
        body: body
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 6
        anchors.rightMargin: 16
        onClosed: body.forceActiveFocus()
    }

    Timer { id: idle; interval: 300; onTriggered: Editor.check(editor.tabKey) }

    Connections {
        target: Editor
        function onLoaded(key, text) {
            if (key !== editor.tabKey) return
            body.text = text
            body.cursorPosition = 0
            flick.contentX = 0
            flick.contentY = 0
            Editor.check(editor.tabKey)
            if (editor.visible) body.forceActiveFocus()
        }
        function onPlaced(key, at) { if (key === editor.tabKey) body.cursorPosition = Math.min(at, body.length) }
    }
}
