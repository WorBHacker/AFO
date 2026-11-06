import QtQuick
import QtQuick.Controls.Basic

// A tab's text, edited (D47, steps 14.2, 14.4): a TextEdit in a Flickable, numbers for the lines on screen only,
// the cursor's line marked; one of these per tab (ProjectView.qml), the shown one visible - each keeps its document
// and its undo history. Its document is its tab's (Editor.attach): Enter keeps the indent, Tab and Shift+Tab
// indent the selected lines - done there, one undo step each; Ctrl+Z / Ctrl+Y are the TextEdit's own; Ctrl+S saves
// through the hub. A pause in typing tells the editor to compare with the saved text («не сохранено»). The colours
// (app/ui/highlighter.py) are laid on the document's lines from Python: the ones on screen first. Ctrl+F and Ctrl+H:
// the find bar (FindBar.qml, 14.3), its matches on screen drawn behind the text. After two letters of a name, or
// at Ctrl+Space, the list of names to complete it (Completer, app/ui/completions.py, 14.5): arrows choose, Tab or Enter
// puts it in, Esc closes; F12 and Ctrl+click go to the definition of the name under the cursor. Once typing
// pauses at a line's end, the model's grey text (Ghost, app/ui/ghost.py, 14.6): Tab takes it, Ctrl+→ a word, Esc puts
// it away. Tab: the list's name first, else the grey text, else an indent. Ctrl+I (Rewriter, app/ui/rewrite.py,
// 14.7): what to do with the selected lines - the model's edit shows as a diff in their place, Ctrl+Enter takes it,
// Esc does not; «Объяснить», «Спросить в чате» take them to a chat of the project.
Item {
    id: editor
    objectName: "codeEditor"
    property string tabKey  // the tab's: its project and path
    property string typing  // the key just pressed, for the list under the cursor once its text is in
    readonly property bool completing: Completer.open && Completer.key === tabKey && visible
    readonly property bool ghostShown: visible && Ghost.key === tabKey && Ghost.text !== ""
                                       && body.cursorPosition === Ghost.position
    readonly property alias body: body
    // a line's height - never under the font's: while a text is replaced its height and its line count change one
    // after the other, and 17 px over 3000 lines put 100 000 numbers in the gutter for a moment (7 s, 24.1)
    readonly property real lineHeight: Math.max(metrics.height, body.lineCount > 0 ? body.contentHeight / body.lineCount : 0)
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
            objectName: "lineNumbers"
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
            onTextChanged: {
                idle.restart()
                const typed = editor.typing
                editor.typing = ""
                if (typed === "\u0001") return  // the grey text taken (14.6): nothing more
                if (typed === "") {  // not typed: loaded, replaced - the grey text goes
                    if (editor.visible) Ghost.dismiss()
                    return
                }
                // what was typed: the grey text that begins with it stays; asked again once typing pauses
                Qt.callLater(() => Ghost.typed(editor.tabKey, body.cursorPosition,
                                               typed.length === 1 && typed !== "\b" && typed !== "\r" ? typed : ""))
                // a name's letter: the list (from two letters); Backspace: the list follows; anything else closes it
                if (/^[\w\u00c0-\uffff]$/.test(typed) || (typed === "\b" && editor.completing))
                    Qt.callLater(() => Completer.update(editor.tabKey, body.cursorPosition, false))
                else
                    Completer.close()
            }
            onCursorRectangleChanged: editor.keepInView()
            onCursorPositionChanged: Editor.locate(editor.tabKey, cursorPosition)
            TapHandler {  // Ctrl+click: to the definition
                acceptedModifiers: Qt.ControlModifier
                onTapped: (point) => Completer.define(editor.tabKey, body.positionAt(point.position.x,
                                                                                     point.position.y))
            }
            Keys.onPressed: (event) => {
                const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
                editor.typing = ""
                if (editor.completing) {  // the list under the cursor has the keys first (Tab: its name, not an indent)
                    if (event.key === Qt.Key_Down || event.key === Qt.Key_Up) {
                        Completer.move(event.key === Qt.Key_Down ? 1 : -1)
                        event.accepted = true
                        return
                    }
                    if (!ctrl && (event.key === Qt.Key_Tab || event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                        body.cursorPosition = Completer.accept()
                        event.accepted = true
                        return
                    }
                    if (event.key === Qt.Key_Escape) {
                        Completer.close()
                        event.accepted = true
                        return
                    }
                    if ([Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp, Qt.Key_PageDown]
                            .includes(event.key))
                        Completer.close()
                }
                if (editor.ghostShown) {  // the model's grey text (14.6): after the list, before an indent
                    if (!ctrl && event.key === Qt.Key_Tab) {
                        editor.typing = "\u0001"
                        body.cursorPosition = Ghost.accept(false)
                        event.accepted = true
                        return
                    }
                    if (ctrl && event.key === Qt.Key_Right) {
                        editor.typing = "\u0001"
                        body.cursorPosition = Ghost.accept(true)
                        event.accepted = true
                        return
                    }
                    if (event.key === Qt.Key_Escape) {
                        Ghost.dismiss()
                        event.accepted = true
                        return
                    }
                }
                if ([Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End, Qt.Key_PageUp,
                     Qt.Key_PageDown].includes(event.key))
                    Ghost.dismiss()  // the cursor goes elsewhere: the grey text, and its request, go
                if (ctrl && event.key === Qt.Key_I) {
                    Rewriter.begin(editor.tabKey, body.selectionStart, body.selectionEnd)
                    event.accepted = true
                    return
                }
                if (ctrl && event.key === Qt.Key_Space) {
                    Completer.update(editor.tabKey, body.cursorPosition, true)
                    event.accepted = true
                    return
                }
                if (event.key === Qt.Key_F12) {
                    Completer.define(editor.tabKey, body.cursorPosition)
                    event.accepted = true
                    return
                }
                if (!ctrl) editor.typing = event.key === Qt.Key_Backspace ? "\b" : event.text
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

    // Ctrl+I (14.7): what to do with the lines, then the model's edit as a diff over them
    Rectangle {
        id: rewrite
        objectName: "rewritePanel"
        readonly property bool shown: editor.visible && Rewriter.open && Rewriter.key === editor.tabKey
        readonly property rect at: body.positionToRectangle(Rewriter.start)
        visible: shown
        x: gutter.width + 4
        y: Math.max(4, Math.min(at.y - flick.contentY, editor.height - height - 4))
        width: editor.width - x - 16
        height: rewriteColumn.implicitHeight + 16
        radius: 8
        color: Theme.p.popup
        border.color: Theme.p.accent
        onShownChanged: {
            if (shown) {
                instruction.text = ""
                instruction.forceActiveFocus()
            } else if (editor.visible) {
                body.forceActiveFocus()
            }
        }

        function keys(event) {
            const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
            const enter = event.key === Qt.Key_Return || event.key === Qt.Key_Enter
            if (event.key === Qt.Key_Escape) Rewriter.close()
            else if (enter && ctrl && Rewriter.ready) body.cursorPosition = Rewriter.accept()
            else if (enter && !ctrl) Rewriter.ask(instruction.text)
            else return
            event.accepted = true
        }

        Column {
            id: rewriteColumn
            x: 8
            y: 8
            width: parent.width - 16
            spacing: 6
            Row {
                spacing: 6
                Field {
                    id: instruction
                    objectName: "rewriteAsk"
                    width: rewriteColumn.width - explainButton.width - askButton.width - 12
                    implicitHeight: 30
                    enabled: !Rewriter.busy
                    placeholderText: qsTr("Что сделать с этими строками? (Enter)")
                    Keys.onPressed: (event) => rewrite.keys(event)
                }
                PlainButton {
                    id: explainButton
                    objectName: "explainCode"
                    implicitHeight: 30
                    focusPolicy: Qt.NoFocus
                    text: qsTr("Объяснить")
                    onClicked: Rewriter.explain(editor.tabKey, Rewriter.start, Rewriter.end)
                }
                PlainButton {
                    id: askButton
                    objectName: "askInChat"
                    implicitHeight: 30
                    focusPolicy: Qt.NoFocus
                    text: qsTr("Спросить в чате")
                    onClicked: Rewriter.askInChat(editor.tabKey, Rewriter.start, Rewriter.end)
                }
            }
            Text {
                visible: Rewriter.busy
                text: qsTr("Модель правит…")
                color: Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Text {
                width: parent.width
                visible: Rewriter.error !== ""
                text: Rewriter.error
                wrapMode: Text.Wrap
                color: Theme.p.error
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Flickable {  // the diff: the lines taken out, those put in
                width: parent.width
                height: Math.min(diffRows.implicitHeight, 360)
                visible: Rewriter.ready
                clip: true
                contentHeight: diffRows.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ThinScrollBar {}
                Column {
                    id: diffRows
                    width: parent.width
                    Repeater {
                        model: Rewriter.rows
                        delegate: Item {
                            required property var modelData
                            objectName: "rewriteRow"
                            width: diffRows.width
                            height: rowText.implicitHeight + 2
                            Rectangle {
                                anchors.fill: parent
                                visible: modelData.kind !== " "
                                color: modelData.kind === "+" ? Theme.p.ok : Theme.p.error
                                opacity: 0.16
                            }
                            Text {
                                id: rowText
                                x: 4
                                text: modelData.kind + " " + modelData.text.replace(/\t/g, "    ")
                                textFormat: Text.PlainText
                                font: body.font
                                color: modelData.kind === " " ? Theme.p.faint : Theme.p.text
                            }
                        }
                    }
                }
            }
            Row {
                visible: Rewriter.ready
                spacing: 6
                PlainButton {
                    objectName: "acceptEdit"
                    kind: "primary"
                    focusPolicy: Qt.NoFocus
                    text: qsTr("Принять (Ctrl+Enter)")
                    onClicked: body.cursorPosition = Rewriter.accept()
                }
                PlainButton {
                    focusPolicy: Qt.NoFocus
                    text: qsTr("Отказать (Esc)")
                    onClicked: Rewriter.close()
                }
            }
        }
    }

    // the model's grey text (14.6): its first line after the cursor, the others on a panel over the lines below
    Item {
        id: ghost
        objectName: "ghostText"
        visible: editor.ghostShown
        readonly property rect at: body.cursorRectangle
        readonly property var lines: Ghost.text.split("\n")
        x: flick.x - flick.contentX
        y: -flick.contentY
        Text {
            x: ghost.at.x + body.x
            y: ghost.at.y
            height: ghost.at.height
            verticalAlignment: Text.AlignVCenter
            text: ghost.lines[0].replace(/\t/g, "    ")
            textFormat: Text.PlainText
            color: Theme.p.faint
            font: body.font
        }
        Rectangle {
            visible: ghost.lines.length > 1
            x: body.leftPadding
            y: ghost.at.y + ghost.at.height
            width: rest.implicitWidth + 12
            height: rest.implicitHeight
            color: Theme.p.code_bg
            Text {
                id: rest
                text: ghost.lines.slice(1).join("\n").replace(/\t/g, "    ")  // a Text has no tab stops
                textFormat: Text.PlainText
                color: Theme.p.faint
                font: body.font
            }
        }
    }

    // the names to complete the one being typed (14.5): under its first letter, above it near the bottom
    Rectangle {
        id: completions
        objectName: "completions"
        readonly property rect at: body.positionToRectangle(Completer.start)
        visible: editor.completing
        width: 360
        height: Math.min(completionList.contentHeight, 8 * 24) + 8
        x: Math.max(0, Math.min(flick.x + at.x - flick.contentX, editor.width - width - 8))
        y: at.y + at.height - flick.contentY + height + 4 > editor.height ? at.y - flick.contentY - height - 2
           : at.y + at.height - flick.contentY + 2
        radius: 6
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        ListView {
            id: completionList
            x: 4
            y: 4
            width: parent.width - 8
            height: parent.height - 8
            clip: true
            model: completions.visible ? Completer.items : []
            currentIndex: Completer.index
            onCurrentIndexChanged: positionViewAtIndex(currentIndex, ListView.Contain)
            delegate: Rectangle {
                required property var modelData
                required property int index
                objectName: "completion"
                width: completionList.width
                height: 24
                radius: 4
                color: index === Completer.index ? Theme.p.item_selected : "transparent"
                Text {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - kindText.width - 24
                    text: modelData.name
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.monoFont
                    font.pixelSize: 13
                }
                Text {
                    id: kindText
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: [modelData.kind, modelData.detail].filter(s => !!s).join(" · ")
                    width: Math.min(implicitWidth, 160)
                    elide: Text.ElideLeft
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        Completer.move(index - Completer.index)
                        body.cursorPosition = Completer.accept()
                        body.forceActiveFocus()
                    }
                }
            }
        }
    }

    // several definitions of one name (F12): the one to go to (14.5)
    Popup {
        id: places
        objectName: "definitionPlaces"
        readonly property rect at: body.cursorRectangle
        x: Math.max(0, Math.min(flick.x + at.x - flick.contentX, editor.width - width - 8))
        y: Math.min(at.y + at.height - flick.contentY + 2, editor.height - height - 8)
        width: 460
        height: Math.min(placeList.contentHeight, 10 * 40) + 12
        padding: 6
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: { Completer.closePlaces(); body.forceActiveFocus() }
        background: Rectangle { radius: 8; color: Theme.p.popup; border.color: Theme.p.hairline_strong }
        Connections {
            target: Completer
            enabled: editor.visible
            function onPlacesChanged() {
                if (Completer.places.length > 1) { places.open(); placeList.forceActiveFocus() }
                else if (places.opened) places.close()
            }
        }
        contentItem: ListView {
            id: placeList
            clip: true
            model: Completer.places
            keyNavigationEnabled: true
            Keys.onReturnPressed: Completer.goTo(currentIndex)
            Keys.onEnterPressed: Completer.goTo(currentIndex)
            delegate: Rectangle {
                required property var modelData
                required property int index
                width: placeList.width
                height: 40
                radius: 4
                color: placeList.currentIndex === index ? Theme.p.item_selected : "transparent"
                Column {
                    x: 8
                    width: parent.width - 16
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        width: parent.width
                        text: modelData.path + ":" + modelData.line + (modelData.kind ? "  · " + modelData.kind : "")
                        elide: Text.ElideMiddle
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Text {
                        width: parent.width
                        text: modelData.signature || ""
                        elide: Text.ElideRight
                        color: Theme.p.faint
                        font.family: Theme.monoFont
                        font.pixelSize: 11
                    }
                }
                MouseArea { anchors.fill: parent; onClicked: Completer.goTo(index) }
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
