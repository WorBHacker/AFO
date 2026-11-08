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
                text: Projects.currentName || (Projects.projectList.length ? qsTr("Выбрать проект") : qsTr("Добавить проект…"))
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

    function focusSearch() { searchField.forceActiveFocus(); searchField.selectAll() }

    // ---------- search in the project's files ----------

    Item {
        id: searchRow
        visible: Projects.currentId !== ""
        x: 12
        y: picker.height + 8
        width: parent.width - 24
        height: 32
        Field {
            id: searchField
            objectName: "projectSearch"
            width: parent.width - toggles.width - 4
            height: parent.height
            rightPadding: clearSearch.visible ? 30 : 10
            placeholderText: symbolToggle.checked ? qsTr("Поиск определений") : qsTr("Поиск по файлам")
            onTextEdited: Projects.search(text, regexToggle.checked, caseToggle.checked)
            Keys.onEscapePressed: { text = ""; Projects.clearSearch() }
            IconButton {
                id: clearSearch
                visible: searchField.text !== ""
                anchors.right: parent.right
                anchors.rightMargin: 3
                anchors.verticalCenter: parent.verticalCenter
                implicitWidth: 24
                implicitHeight: 24
                iconName: "close"
                tip: qsTr("Очистить (Esc)")
                onClicked: { searchField.text = ""; Projects.clearSearch() }
            }
        }
        Row {
            id: toggles
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            component Toggle: AbstractButton {
                id: toggle
                property string tip
                checkable: true
                implicitWidth: 28
                implicitHeight: 28
                hoverEnabled: true
                opacity: enabled ? 1 : 0.4
                onToggled: {
                    if (symbolToggle.checked !== Projects.symbolSearch)
                        Projects.setSymbolSearch(symbolToggle.checked)  // runs the query again
                    else
                        Projects.search(searchField.text, regexToggle.checked, caseToggle.checked)
                }
                background: Rectangle {
                    radius: 5
                    color: toggle.checked ? Theme.p.item_selected : (toggle.hovered ? Theme.p.item_hover : "transparent")
                    border.color: toggle.checked ? Theme.p.accent : "transparent"
                }
                contentItem: Text {
                    text: toggle.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    color: toggle.checked ? Theme.p.text : Theme.p.muted
                    font.family: Theme.monoFont
                    font.pixelSize: 12
                }
                ToolTip.visible: hovered
                ToolTip.delay: 600
                ToolTip.text: tip
            }
            Toggle {
                id: caseToggle
                enabled: !symbolToggle.checked
                text: "Aa"
                tip: qsTr("Учитывать регистр (без этого — только если в запросе есть заглавные)")
            }
            Toggle { id: regexToggle; enabled: !symbolToggle.checked; text: ".*"; tip: qsTr("Регулярное выражение") }
            Toggle {
                id: symbolToggle
                objectName: "symbolToggle"
                text: "ƒ"
                tip: qsTr("Только определения: функции, классы, методы — по индексу символов (Класс.метод тоже можно)")
            }
        }
    }

    Item {
        id: treeHead
        visible: Projects.currentId !== ""
        x: 18
        y: searchRow.y + searchRow.height + 6
        width: parent.width - 30
        height: 28
        Text {
            width: parent.width - 30
            anchors.verticalCenter: parent.verticalCenter
            text: Projects.searchActive ? Projects.searchSummary : qsTr("Файлы")
            elide: Text.ElideRight
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        IconButton {
            visible: !Projects.searchActive
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            implicitWidth: 26
            implicitHeight: 26
            iconName: "refresh"
            tip: qsTr("Обновить дерево и git (F5)")
            onClicked: Projects.refresh()
        }
    }

    ListView {
        id: results
        objectName: "searchResults"
        visible: Projects.currentId !== "" && Projects.searchActive
        x: 8
        y: treeHead.y + treeHead.height + 2
        width: parent.width - 16
        height: parent.height - y
        clip: true
        model: Projects.searchRows
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        delegate: AbstractButton {
            id: hit
            required property var modelData
            readonly property bool isFile: modelData.kind === "file"
            readonly property bool isSymbol: modelData.kind === "symbol"
            width: results.width
            height: isSymbol ? 40 : (isFile ? 28 : 24)
            hoverEnabled: true
            onClicked: Projects.openFileAt(modelData.path, isFile ? 1 : modelData.line)
            ToolTip.visible: isSymbol && hovered
            ToolTip.delay: 700
            ToolTip.text: modelData.tip || ""
            background: Rectangle {
                radius: 4
                color: hit.hovered ? Theme.p.item_hover : "transparent"
            }
            contentItem: Item {
                Icon {
                    visible: hit.isFile
                    x: 6
                    anchors.verticalCenter: parent.verticalCenter
                    name: "file"
                    size: 14
                    tint: Theme.p.muted
                }
                Text {
                    id: hitName
                    visible: hit.isFile
                    x: 26
                    width: Math.max(0, Math.min(implicitWidth, parent.width * 0.6))
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                    text: hit.modelData.name
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Text {
                    visible: hit.isFile
                    x: hitName.x + hitName.width + 6
                    width: Math.max(0, parent.width - x - 34)
                    anchors.verticalCenter: parent.verticalCenter
                    text: hit.modelData.folder
                    elide: Text.ElideMiddle
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                Text {
                    visible: hit.isFile
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: hit.modelData.count
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                // a definition from the symbol index: badge, name, its class; file and line under them
                SymbolBadge {
                    visible: hit.isSymbol
                    x: 6
                    y: 5
                    badge: hit.modelData.badge || ""
                }
                Text {
                    id: symbolName
                    visible: hit.isSymbol
                    x: 30
                    y: 3
                    width: Math.min(implicitWidth, parent.width - x - 8)
                    text: hit.modelData.name
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.monoFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Text {
                    visible: hit.isSymbol && !!hit.modelData.container
                    x: symbolName.x + symbolName.width + 6
                    y: 4
                    width: Math.max(0, parent.width - x - 8)
                    text: hit.modelData.container || ""
                    elide: Text.ElideLeft
                    color: Theme.p.faint
                    font.family: Theme.monoFont
                    font.pixelSize: 11
                }
                Text {
                    visible: hit.isSymbol
                    x: 30
                    y: 21
                    width: parent.width - x - 8
                    text: hit.modelData.folder
                    elide: Text.ElideMiddle
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                Text {
                    id: lineNo
                    visible: !hit.isFile && !hit.isSymbol
                    x: 10
                    width: 34
                    anchors.verticalCenter: parent.verticalCenter
                    horizontalAlignment: Text.AlignRight
                    text: hit.modelData.line
                    color: Theme.p.faint
                    font.family: Theme.monoFont
                    font.pixelSize: 11
                }
                Item {
                    visible: !hit.isFile && !hit.isSymbol
                    x: 52
                    width: parent.width - x - 4
                    height: parent.height
                    clip: true
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: hit.modelData.html
                        textFormat: Text.RichText
                        color: Theme.p.text_soft
                        font.family: Theme.monoFont
                        font.pixelSize: 12
                    }
                }
            }
        }
    }

    ListView {
        id: tree
        objectName: "projectTree"
        visible: Projects.currentId !== "" && !Projects.searchActive
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
            required property bool secret
            readonly property bool selected: !dir && Projects.openPath === path
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
                    name: node.dir ? "folder" : node.secret ? "lock" : "file"
                    size: 14
                    tint: node.secret ? Theme.p.warn : node.dir ? Theme.p.muted : Theme.p.faint
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
            ToolTip.visible: hovered && (name.length > 28 || secret)
            ToolTip.delay: 700
            ToolTip.text: secret ? qsTr("%1 — секретный файл: моделям не показывается").arg(path) : path
        }
    }

    Text {
        visible: Projects.currentId === ""
        x: 18
        y: picker.height + 16
        width: parent.width - 36
        wrapMode: Text.Wrap
        text: qsTr("Проект — папка с кодом: её файлы, поиск по ним, git, процессы и порты, терминалы.")
        color: Theme.p.faint
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Connections {
        target: Projects
        function onProjectChanged() { searchField.text = "" }
    }
}
