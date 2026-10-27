import QtQuick

// The model's reasoning as an accordion: a muted header with a timer, the text folded away until asked.
Column {
    id: think
    property var info: ({})
    property int rowIndex: -1
    // folded reasoning is not laid out at all; a string property notifies only when the text really changes
    property string bodyText: info.open ? (info.text || "") : ""
    // Streaming reasoning grows at its end. It shows in parts of about `partSize` characters, cut at line ends (the
    // line end is the cut), and a tick lays out only the last part again: one TextEdit of 100k characters laid all of
    // it out on every tick. A selection does not cross parts.
    readonly property int partSize: 4000
    property string shownText: ""
    onBodyTextChanged: {
        if (shownText !== "" && bodyText.startsWith(shownText) && parts.count > 0) {
            addText(bodyText.slice(shownText.length))
        } else {
            parts.clear()
            addText(bodyText)
        }
        shownText = bodyText
    }
    Component.onCompleted: if (shownText === "") { addText(bodyText); shownText = bodyText }
    function addText(more) {
        if (more === "")
            return
        const growing = parts.count > 0
        let tail = (growing ? parts.get(parts.count - 1).part : "") + more
        const cut = []
        while (tail.length > partSize) {
            const end = tail.lastIndexOf("\n", partSize)
            if (end <= 0)
                break  // no line end yet: the part grows until one comes
            cut.push(tail.slice(0, end))
            tail = tail.slice(end + 1)
        }
        cut.push(tail)
        if (growing)
            parts.setProperty(parts.count - 1, "part", cut.shift())
        for (const part of cut)
            parts.append({ part: part })
    }
    ListModel { id: parts }
    spacing: 8

    Item {
        width: header.implicitWidth
        height: 22
        Row {
            id: header
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            Rectangle {
                width: 7; height: 7; radius: 3.5
                color: Theme.p.accent
                visible: !!think.info.running
                anchors.verticalCenter: parent.verticalCenter
                SequentialAnimation on opacity {
                    running: !!think.info.running
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.25; duration: 600 }
                    NumberAnimation { to: 1; duration: 600 }
                }
            }
            Icon { name: "sparkle"; size: 14; visible: !think.info.running; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: (think.info.running ? "Думает… " : (think.info.seconds ? "Размышления · " : "Размышления"))
                      + (think.info.seconds || "")
                color: hover.hovered ? Theme.p.text_soft : Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
            Icon {
                name: think.info.open ? "chevron-down" : "chevron-right"
                size: 13
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: App.toggleThinking(think.rowIndex) }
    }

    Row {
        visible: !!think.info.open
        spacing: 12
        leftPadding: 6
        Rectangle { width: 2; height: body.height; radius: 1; color: Theme.p.hairline_strong }
        Column {
            id: body
            objectName: "thinkingParts"
            width: think.width - 26
            Repeater {
                model: parts
                delegate: TextEdit {
                    required property string part
                    width: body.width
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    text: part
                    color: Theme.p.muted
                    selectionColor: Theme.p.selection
                    selectedTextColor: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
            }
        }
    }
}
