import QtQuick
import QtQuick.Controls.Basic

// A small menu above its anchor: sections and items { section | id, name, note, icon, cloud, loaded, selected }.
Popup {
    id: menu
    property var items: []
    property Item anchor
    property point at: Qt.point(-1, -1)  // open at a point (a right click) instead of above `anchor`
    property int menuWidth: 300
    signal chosen(string id)

    parent: Overlay.overlay
    width: menuWidth
    padding: 8
    x: at.x >= 0 ? Math.min(at.x, parent.width - width - 8)
               : anchor ? Math.max(8, anchor.mapToItem(parent, 0, 0).x - 4) : 0
    y: at.y >= 0 ? Math.min(at.y, parent.height - height - 8)
               : anchor ? anchor.mapToItem(parent, 0, 0).y - height - 8 : 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 90 } }

    background: Rectangle {
        radius: 8
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow {}
    }

    contentItem: Column {
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
            height: info.note ? 46 : 36
            hoverEnabled: true
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
                        elide: Text.ElideRight
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
