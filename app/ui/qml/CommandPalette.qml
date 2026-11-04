import QtQuick
import QtQuick.Controls.Basic

// Ctrl+K: type to find an action, a chat, a model or a budget; ↑↓ to move, Enter to run, Esc to close.
Popup {
    id: palette
    objectName: "palette"
    property var items: []
    property int current: -1

    parent: Overlay.overlay
    x: (parent.width - width) / 2
    y: 96
    width: Math.min(640, parent.width - 80)
    padding: 0
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 } }

    onOpened: {
        search.text = ""
        refresh()
        search.forceActiveFocus()
    }

    function refresh() {
        items = App.paletteItems(search.text)
        current = next(-1, 1)
        list.positionViewAtBeginning()
    }
    function next(from, step) {
        for (let i = from + step; i >= 0 && i < items.length; i += step)
            if (!items[i].section) return i
        return from
    }
    function move(step) {
        current = next(current, step)
        list.positionViewAtIndex(current, ListView.Contain)
    }
    function run(index) {
        if (index < 0 || index >= items.length || items[index].section) return
        const id = items[index].id
        close()
        App.runCommand(id)
    }

    background: Rectangle {
        radius: 10
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow { corner: 10 }
    }

    contentItem: Column {
        Item {
            width: palette.width
            height: 54
            Icon { x: 18; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 18 }
            TextField {
                id: search
                objectName: "paletteSearch"
                x: 44
                width: parent.width - 110
                anchors.verticalCenter: parent.verticalCenter
                placeholderText: qsTr("Команда, чат, модель или бюджет…")
                placeholderTextColor: Theme.p.faint
                color: Theme.p.text
                selectionColor: Theme.p.selection
                font.family: Theme.uiFont
                font.pixelSize: 16
                background: null
                onTextChanged: palette.refresh()
                Keys.onUpPressed: palette.move(-1)
                Keys.onDownPressed: palette.move(1)
                Keys.onReturnPressed: palette.run(palette.current)
                Keys.onEnterPressed: palette.run(palette.current)
            }
            Kbd { text: "Esc"; anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
        }

        ListView {
            id: list
            x: 8
            width: palette.width - 16
            height: Math.min(contentHeight + 12, 430)
            topMargin: 6
            bottomMargin: 6
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: palette.items
            ScrollBar.vertical: ThinScrollBar {}
            delegate: Loader {
                required property var modelData
                required property int index
                width: list.width
                sourceComponent: modelData.section ? sectionRow : commandRow
                onLoaded: { item.info = modelData; item.row = index }
            }
        }

        Text {
            visible: palette.items.length === 0
            width: palette.width
            height: 64
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: qsTr("Ничего не найдено")
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }

    Component {
        id: sectionRow
        Text {
            property var info: ({})
            property int row: -1
            height: 28
            leftPadding: 10
            bottomPadding: 5
            verticalAlignment: Text.AlignBottom
            text: info.section || ""
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
    }
    Component {
        id: commandRow
        AbstractButton {
            id: entry
            property var info: ({})
            property int row: -1
            readonly property bool chosen: row === palette.current
            height: 40
            hoverEnabled: true
            onHoveredChanged: if (hovered) palette.current = row
            onClicked: palette.run(row)
            background: Rectangle { radius: 6; color: entry.chosen ? Theme.p.item_selected : "transparent" }
            contentItem: Item {
                Icon {
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    name: entry.info.icon || "chat"
                    tint: entry.chosen ? Theme.p.text : Theme.p.muted
                }
                Text {
                    x: 40
                    width: Math.min(implicitWidth, parent.width - 52 - note.implicitWidth - hint.width)
                    anchors.verticalCenter: parent.verticalCenter
                    text: entry.info.name || ""
                    textFormat: Text.StyledText
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Text {
                    id: note
                    anchors.right: hint.left
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: entry.info.note || ""
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Kbd {
                    id: hint
                    visible: !!entry.info.key
                    width: visible ? implicitWidth : 0
                    text: entry.info.key || ""
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
}
