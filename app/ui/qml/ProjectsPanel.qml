import QtQuick
import QtQuick.Controls.Basic

// The projects side of the sidebar: which project (a menu with the list and "add"), then its file tree. Folders
// open on a click and load then; files changed in git carry their mark.
Item {
    id: panel
    signal projectMenuRequested(Item anchor)
    signal addRequested()

    AbstractButton {
        id: picker
        objectName: "projectPicker"
        x: 12
        width: parent.width - 24
        height: 36
        hoverEnabled: true
        onClicked: Projects.projectList.length ? panel.projectMenuRequested(picker) : panel.addRequested()
        background: Rectangle {
            radius: 6
            color: picker.hovered ? Theme.p.card_hover : Theme.p.card
            border.color: Theme.p.hairline
        }
        contentItem: Item {
            Icon {
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                name: Projects.currentId ? "folder" : "folder-plus"
                tint: Theme.p.text
            }
            Text {
                x: 34
                width: parent.width - 60
                anchors.verticalCenter: parent.verticalCenter
                text: Projects.currentName || (Projects.projectList.length ? "Выбрать проект" : "Добавить проект…")
                elide: Text.ElideRight
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            Icon {
                visible: Projects.projectList.length > 0
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                name: "chevron-down"
                size: 14
            }
        }
    }

    Item {
        id: treeHead
        visible: Projects.currentId !== ""
        x: 18
        y: picker.height + 8
        width: parent.width - 30
        height: 28
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "Файлы"
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        IconButton {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            implicitWidth: 26
            implicitHeight: 26
            iconName: "refresh"
            tip: "Обновить дерево и git (F5)"
            onClicked: Projects.refresh()
        }
    }

    ListView {
        id: tree
        objectName: "projectTree"
        visible: Projects.currentId !== ""
        x: 8
        y: treeHead.y + treeHead.height + 2
        width: parent.width - 16
        height: parent.height - y
        clip: true
        model: Projects.treeModel
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        delegate: AbstractButton {
            id: node
            required property int index
            required property string name
            required property string path
            required property int depth
            required property bool dir
            required property bool open
            required property string status
            readonly property bool selected: !dir && Projects.file.path === path
            readonly property int indent: 6 + depth * 14
            width: tree.width
            height: 26
            hoverEnabled: true
            onClicked: dir ? Projects.toggleDir(index) : Projects.openFile(path, false)
            background: Rectangle {
                radius: 4
                color: node.selected ? Theme.p.item_selected : (node.hovered ? Theme.p.item_hover : "transparent")
            }
            contentItem: Item {
                Icon {
                    x: node.indent
                    visible: node.dir
                    anchors.verticalCenter: parent.verticalCenter
                    name: node.open ? "chevron-down" : "chevron-right"
                    size: 12
                    tint: Theme.p.faint
                }
                Icon {
                    x: node.indent + 14
                    anchors.verticalCenter: parent.verticalCenter
                    name: node.dir ? "folder" : "file"
                    size: 14
                    tint: node.dir ? Theme.p.muted : Theme.p.faint
                }
                Text {
                    x: node.indent + 34
                    width: parent.width - x - 22
                    anchors.verticalCenter: parent.verticalCenter
                    text: node.name
                    elide: Text.ElideMiddle
                    color: node.status && node.status !== "•" ? mark.colour
                           : node.selected ? Theme.p.text : Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.strikeout: node.status === "D"
                }
                GitMark {
                    id: mark
                    mark: node.status
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            ToolTip.visible: hovered && name.length > 28
            ToolTip.delay: 700
            ToolTip.text: path
        }
    }

    Text {
        visible: Projects.currentId === ""
        x: 18
        y: picker.height + 16
        width: parent.width - 36
        wrapMode: Text.Wrap
        text: "Проект — папка с кодом. Здесь будут её файлы и git; дальше в этом же месте — процессы, порты и терминалы."
        color: Theme.p.faint
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
