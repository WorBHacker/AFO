import QtQuick
import QtQuick.Controls.Basic

// A small menu above its anchor (`below`: under it): sections and items { section | id, name, note, icon, cloud,
// loaded, selected }.
Popup {
    id: menu
    property var items: []
    property Item anchor
    property point at: Qt.point(-1, -1)  // open at a point (a right click) instead of above `anchor`
    property bool below: false  // under `anchor` rather than above it
    property int menuWidth: 300
    property bool wrapNotes: false  // a note that explains the choice: whole, on several lines
    signal chosen(string id)

    parent: Overlay.overlay
    width: menuWidth
    padding: 8
    // Where it opens is worked out each time it opens, and again when its size settles or the window's changes.
    // A binding to the anchor's place was worked out only when the anchor changed: opened again from the same chip
    // after the window changed size, the menu came where the chip had been (2026-11-05). It stays in the window;
    // without room on its side of the anchor it goes to the other.
    function place() {
        const room = menu.parent
        if (!room)
            return
        let left = 0, top = 0
        if (at.x >= 0) {
            left = at.x
            top = at.y
        } else if (anchor) {
            const p = anchor.mapToItem(room, 0, 0)
            const above = p.y - height - 8, under = p.y + anchor.height + 8
            left = p.x - 4
            top = below ? (under + height <= room.height - 8 ? under : above)
                        : (above >= 8 ? above : under)
        }
        x = Math.max(8, Math.min(left, room.width - width - 8))
        y = Math.max(8, Math.min(top, room.height - height - 8))
    }
    onAboutToShow: place()
    onHeightChanged: if (visible) place()
    onWidthChanged: if (visible) place()
    Connections {
        target: menu.parent
        function onWidthChanged() { if (menu.visible) menu.place() }
        function onHeightChanged() { if (menu.visible) menu.place() }
    }
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 90 } }

    background: Rectangle {
        radius: 8
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow {}
    }

    // no taller than the window: a long list (many models) scrolls inside the menu instead of going past an edge
    contentItem: Flickable {
        objectName: "menuList"
        implicitHeight: Math.min(rows.height, (menu.parent ? menu.parent.height : 800) - 16 - menu.topPadding
                                              - menu.bottomPadding)
        contentHeight: rows.height
        interactive: contentHeight > height
        clip: interactive
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        Column {
            id: rows
            width: parent.width
            Repeater {
                model: menu.items
                delegate: Loader {
                    required property var modelData
                    width: menu.availableWidth
                    sourceComponent: modelData.section ? sectionRow : itemRow
                    onLoaded: item.info = modelData
                }
            }
        }
    }

    Component {
        id: sectionRow
        Text {
            property var info: ({})
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
        id: itemRow
        AbstractButton {
            id: entry
            property var info: ({})
            height: info.note ? (menu.wrapNotes ? body.implicitHeight + 16 : 46) : 36
            hoverEnabled: true
            enabled: info.enabled !== false
            opacity: enabled ? 1 : 0.5
            onClicked: { menu.chosen(info.id); menu.close() }
            background: Rectangle {
                radius: 6
                color: entry.info.selected ? Theme.p.item_selected : (entry.hovered ? Theme.p.item_hover : "transparent")
            }
            contentItem: Item {
                Icon {
                    x: 10
                    visible: !!entry.info.icon || entry.info.cloud !== undefined
                    anchors.verticalCenter: parent.verticalCenter
                    name: entry.info.icon || (entry.info.cloud ? "cloud" : "cpu")
                    tint: Theme.p.text_soft
                }
                Column {
                    id: body
                    x: 36
                    width: parent.width - 80
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    Text {
                        width: parent.width
                        text: entry.info.name || ""
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        width: parent.width
                        visible: !!entry.info.note
                        text: entry.info.note || ""
                        elide: menu.wrapNotes ? Text.ElideNone : Text.ElideRight
                        wrapMode: menu.wrapNotes ? Text.Wrap : Text.NoWrap
                        lineHeight: 1.15
                        color: entry.info.loaded ? Theme.p.ok : Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
                Icon {
                    visible: !!entry.info.selected
                    name: "check"
                    tint: Theme.p.accent
                    size: 15
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
    }
}
