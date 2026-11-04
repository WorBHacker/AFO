import QtQuick
import QtQuick.Controls.Basic

// Find and replace in the editor's file (D47, step 14.3), over the text's top right corner. Ctrl+F opens it, Ctrl+H
// with replacing; Enter and F3 go to the next match, Shift+Enter and Shift+F3 to the one before; in the replacement
// Enter replaces the selected match, Ctrl+Alt+Enter all of them; Esc closes and gives the text its focus back. The
// matching is Finder's (app/ui/finder.py); the bar moves the TextEdit's selection to a match.
Rectangle {
    id: bar
    objectName: "findBar"
    required property TextEdit body
    signal closed()
    visible: Finder.active
    width: Math.min(parent ? parent.width - 32 : 480, 480)
    height: rows.implicitHeight + 12
    radius: 8
    color: Theme.p.popup
    border.color: Theme.p.hairline_strong

    function open(replacing) {
        Finder.open(replacing, body.selectedText)
        query.text = Finder.query
        query.forceActiveFocus()
        query.selectAll()
    }
    function select(found) { if (found.length) body.select(found[0], found[1]) }
    function go(backwards) { select(Finder.step(body.selectionStart, body.selectionEnd, backwards)) }
    function replaceOne() { select(Finder.replace(body.selectionStart, body.selectionEnd, replacement.text)) }
    function close() {
        Finder.close()
        bar.closed()
    }
    function keys(event, replacing) {
        const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
        const shift = (event.modifiers & Qt.ShiftModifier) !== 0
        if (event.key === Qt.Key_Escape) {
            close()
        } else if (event.key === Qt.Key_F3) {
            go(shift)
        } else if (ctrl && event.key === Qt.Key_F) {
            query.forceActiveFocus()
            query.selectAll()
        } else if (ctrl && event.key === Qt.Key_H) {
            Finder.setReplacing(true)
            replacement.forceActiveFocus()
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            if (!replacing) go(shift)
            else if (ctrl && (event.modifiers & Qt.AltModifier) !== 0) Finder.replaceAll(replacement.text)
            else replaceOne()
        } else {
            return
        }
        event.accepted = true
    }

    onVisibleChanged: if (visible) query.text = Finder.query  // another tab shown: the bar its own, the query one

    Connections {
        target: Finder
        enabled: bar.visible  // the shown tab's bar only
        function onFresh() { bar.select(Finder.seek(bar.body.selectionStart)) }  // a new query: the nearest match
    }

    component Toggle: AbstractButton {
        id: toggle
        property string tip
        width: 26
        height: 26
        hoverEnabled: true
        focusPolicy: Qt.NoFocus  // the field keeps the focus: Enter, F3 and Esc go on working
        anchors.verticalCenter: parent.verticalCenter
        background: Rectangle {
            radius: 5
            color: toggle.checked ? Theme.p.selection : toggle.hovered ? Theme.p.item_hover : "transparent"
            border.color: toggle.checked ? Theme.p.accent : "transparent"
        }
        contentItem: Text {
            text: toggle.text
            color: toggle.checked ? Theme.p.text : Theme.p.muted
            font.family: Theme.monoFont
            font.pixelSize: 12
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        ToolTip.visible: hovered
        ToolTip.delay: 600
        ToolTip.text: tip
    }

    component Small: IconButton {
        width: 26
        height: 26
        focusPolicy: Qt.NoFocus
        anchors.verticalCenter: parent.verticalCenter
    }

    Column {
        id: rows
        x: 6
        y: 6
        width: parent.width - 12
        spacing: 6

        Row {
            spacing: 4
            Small {
                iconName: Finder.replacing ? "chevron-down" : "chevron-right"
                tip: qsTr("Замена (Ctrl+H)")
                onClicked: Finder.setReplacing(!Finder.replacing)
            }
            Field {
                id: query
                objectName: "findQuery"
                width: rows.width - 26 * 6 - 84 - 4 * 7
                implicitHeight: 30
                placeholderText: qsTr("Найти")
                font.family: Theme.monoFont
                onTextEdited: Finder.setQuery(text)
                Keys.onPressed: (event) => bar.keys(event, false)
            }
            Toggle {
                text: "Aa"
                tip: qsTr("Учитывать регистр")
                checked: Finder.caseSensitive
                onClicked: Finder.setCase(!Finder.caseSensitive)
            }
            Toggle {
                text: ".*"
                tip: qsTr("Регулярное выражение (Python: группы — $1 или \\1)")
                checked: Finder.regex
                onClicked: Finder.setRegex(!Finder.regex)
            }
            Text {
                objectName: "findCount"
                width: 84
                anchors.verticalCenter: parent.verticalCenter
                text: Finder.label(bar.body.selectionStart, bar.body.selectionEnd, Finder.revision)
                color: Finder.ready && Finder.count === 0 ? Theme.p.error : Theme.p.muted
                elide: Text.ElideRight
                horizontalAlignment: Text.AlignHCenter
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Small {
                iconName: "chevron-down"
                rotation: 180
                tip: qsTr("Предыдущее (Shift+F3)")
                onClicked: bar.go(true)
            }
            Small {
                iconName: "chevron-down"
                tip: qsTr("Следующее (F3)")
                onClicked: bar.go(false)
            }
            Small {
                iconName: "close"
                tip: qsTr("Закрыть (Esc)")
                onClicked: bar.close()
            }
        }

        Row {
            visible: Finder.replacing
            spacing: 4
            leftPadding: 30
            Field {
                id: replacement
                objectName: "findReplacement"
                width: query.width
                implicitHeight: 30
                placeholderText: qsTr("Заменить на")
                font.family: Theme.monoFont
                Keys.onPressed: (event) => bar.keys(event, true)
            }
            PlainButton {
                objectName: "replaceOne"
                implicitHeight: 30
                focusPolicy: Qt.NoFocus
                text: qsTr("Заменить")
                onClicked: bar.replaceOne()
            }
            PlainButton {
                objectName: "replaceAll"
                implicitHeight: 30
                focusPolicy: Qt.NoFocus
                text: qsTr("Все")
                enabled: Finder.ready && Finder.count > 0
                onClicked: Finder.replaceAll(replacement.text)
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: qsTr("Заменить все — одной отменой (Ctrl+Alt+Enter)")
            }
        }

        Text {
            width: parent.width
            visible: text !== ""
            text: Finder.error
            wrapMode: Text.Wrap
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }
}
