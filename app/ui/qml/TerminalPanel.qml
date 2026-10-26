import QtQuick
import QtQuick.Controls.Basic

// The terminal panel under a project: tabs of its terminals, the chosen one's screen with its scrollback, the
// keyboard going to it. The top edge drags to change the height. The screen is drawn line by line from the
// window's emulator (app/ui/terminals.py); the hub runs the terminals themselves.
Rectangle {
    id: panel
    objectName: "terminalPanel"
    color: Theme.p.code_bg

    FontMetrics { id: fm; font.family: Theme.monoFont; font.pixelSize: 13 }
    readonly property real cw: fm.advanceWidth("M")
    readonly property real ch: Math.ceil(fm.height)
    readonly property var current: Terminals.tabs.find(t => t.selected) || null

    function focusScreen() { keys.forceActiveFocus() }

    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline_strong }
    MouseArea {
        id: grip
        width: parent.width
        height: 5
        cursorShape: Qt.SizeVerCursor
        property real startY
        property int startHeight
        onPressed: (mouse) => { startY = mapToItem(null, 0, mouse.y).y; startHeight = panel.height }
        onPositionChanged: (mouse) => {
            if (pressed) Terminals.setPanelHeight(startHeight - (mapToItem(null, 0, mouse.y).y - startY))
        }
    }

    // ---------- tabs ----------

    Rectangle {
        id: head
        y: 1
        width: parent.width
        height: 34
        color: Theme.p.code_head
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }

        Flickable {
            id: tabStrip
            x: 6
            width: Math.max(0, headButtons.x - x - 6)
            height: parent.height
            contentWidth: tabRow.width
            clip: true
            interactive: contentWidth > width
            boundsBehavior: Flickable.StopAtBounds
            Row {
                id: tabRow
                height: parent.height
                spacing: 2
                Repeater {
                    model: Terminals.tabs
                    delegate: AbstractButton {
                        id: tab
                        required property var modelData
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 26
                        implicitWidth: Math.min(220, tabLabel.implicitWidth + 58)
                        hoverEnabled: true
                        onClicked: { Terminals.openTerminal(modelData.id); panel.focusScreen() }
                        background: Rectangle {
                            radius: 5
                            color: tab.modelData.selected ? Theme.p.item_selected : (tab.hovered ? Theme.p.item_hover : "transparent")
                        }
                        contentItem: Item {
                            Rectangle {
                                x: 10
                                width: 7; height: 7; radius: 3.5
                                anchors.verticalCenter: parent.verticalCenter
                                color: tab.modelData.running ? Theme.p.ok : Theme.p.faint
                            }
                            Text {
                                id: tabLabel
                                x: 24
                                width: parent.width - 54
                                anchors.verticalCenter: parent.verticalCenter
                                text: tab.modelData.title
                                elide: Text.ElideRight
                                color: tab.modelData.selected ? Theme.p.text : Theme.p.text_soft
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                            IconButton {
                                anchors.right: parent.right
                                anchors.rightMargin: 2
                                anchors.verticalCenter: parent.verticalCenter
                                implicitWidth: 22
                                implicitHeight: 22
                                iconName: "close"
                                tip: tab.modelData.running ? "Остановить терминал" : "Убрать (журнал удаляется)"
                                onClicked: Terminals.closeTab(tab.modelData.id)
                            }
                        }
                        ToolTip.visible: hovered
                        ToolTip.delay: 800
                        ToolTip.text: modelData.title + " — " + modelData.note
                    }
                }
            }
        }

        Row {
            id: headButtons
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text {
                anchors.verticalCenter: parent.verticalCenter
                rightPadding: 8
                visible: !!panel.current && !panel.current.running
                text: panel.current ? panel.current.note : ""
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            IconButton {
                objectName: "newTerminal"
                anchors.verticalCenter: parent.verticalCenter
                iconName: "plus"
                tip: "Новый терминал в папке проекта"
                onClicked: Terminals.newTerminal()
            }
            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                visible: Terminals.currentId !== ""
                iconName: "copy"
                tip: "Копировать весь вывод"
                onClicked: Terminals.copyAll()
            }
            IconButton {
                anchors.verticalCenter: parent.verticalCenter
                iconName: "chevron-down"
                tip: "Скрыть терминалы (Ctrl+J)"
                onClicked: Terminals.setPanelOpen(false)
            }
        }
    }

    // ---------- the screen ----------

    Item {
        id: screen
        y: head.y + head.height
        width: parent.width
        height: parent.height - y

        ListView {
            id: lines
            objectName: "terminalLines"
            x: 12
            y: 8
            width: parent.width - 24
            // whole lines only: the top line is never cut in half
            height: panel.ch > 0 ? Math.floor((parent.height - 16) / panel.ch) * panel.ch : parent.height - 16
            clip: true
            model: Terminals.lineModel
            boundsBehavior: Flickable.StopAtBounds
            flickDeceleration: 4000
            ScrollBar.vertical: ThinScrollBar {}
            property bool follow: true
            onMovementEnded: follow = atYEnd
            onContentHeightChanged: if (follow) Qt.callLater(positionViewAtEnd)
            onCountChanged: if (follow) Qt.callLater(positionViewAtEnd)

            delegate: Text {
                required property string html
                width: lines.width
                height: panel.ch
                text: html
                textFormat: Text.RichText
                wrapMode: Text.NoWrap
                color: Theme.p.text
                font.family: Theme.monoFont
                font.pixelSize: 13
            }

            Rectangle {
                parent: lines.contentItem
                visible: Terminals.cursorVisible
                x: Terminals.cursorX * panel.cw
                y: Terminals.cursorY * panel.ch
                width: panel.cw
                height: panel.ch
                color: Theme.p.text
                opacity: keys.activeFocus ? 0.55 : 0.2
            }

            onWidthChanged: Qt.callLater(screen.report)
            onHeightChanged: Qt.callLater(screen.report)
        }

        function report() {
            if (panel.visible && panel.cw > 0 && panel.ch > 0 && lines.width > 0 && lines.height > 0)
                Terminals.setViewSize(Math.floor(lines.width / panel.cw), Math.floor(lines.height / panel.ch))
        }

        TapHandler { onTapped: panel.focusScreen() }

        Item {
            id: keys
            objectName: "terminalKeys"
            focus: true
            Keys.onPressed: (event) => {
                if (Terminals.key(event.key, event.modifiers, event.text)) {
                    event.accepted = true
                    lines.follow = true
                    lines.positionViewAtEnd()
                }
            }
            // the terminal takes keys the window would use (Ctrl+L, Ctrl+C, F5...), except the window's own panels
            Keys.onShortcutOverride: (event) => {
                const ctrl = (event.modifiers & Qt.ControlModifier) !== 0
                const windowKeys = [Qt.Key_K, Qt.Key_J, Qt.Key_QuoteLeft, Qt.Key_1, Qt.Key_2, Qt.Key_Comma, Qt.Key_B]
                event.accepted = Terminals.currentRunning && !(ctrl && windowKeys.indexOf(event.key) >= 0)
            }
        }

        Column {
            visible: Terminals.tabs.length === 0
            anchors.centerIn: parent
            spacing: 10
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Терминалов в этом проекте нет"
                color: Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            PlainButton {
                anchors.horizontalCenter: parent.horizontalCenter
                iconName: "terminal"
                text: "Новый терминал"
                onClicked: Terminals.newTerminal()
            }
        }
    }

    onVisibleChanged: if (visible) { Qt.callLater(screen.report); Qt.callLater(focusScreen) }
    Connections {
        target: Terminals
        function onCurrentChanged() { lines.follow = true; Qt.callLater(lines.positionViewAtEnd) }
    }
}
