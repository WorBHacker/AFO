import QtQuick
import QtQuick.Controls.Basic

// The main area in projects mode: the project's overview (git: branch, changes, recent commits), or an open file
// with line numbers and, for a changed file, its diff.
Item {
    id: view
    signal addRequested()

    // ---------- no project ----------

    Column {
        visible: Projects.currentId === ""
        anchors.centerIn: parent
        width: Math.min(420, parent.width - 48)
        spacing: 12
        Icon { anchors.horizontalCenter: parent.horizontalCenter; name: "folder"; size: 36; tint: Theme.p.faint }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Нет открытого проекта"
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            lineHeight: 1.3
            text: "Добавьте папку с кодом: её файлы, git и изменения будут видны здесь. Папка не меняется — хаб только читает её."
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
        PlainButton {
            anchors.horizontalCenter: parent.horizontalCenter
            kind: "primary"
            iconName: "folder-plus"
            text: "Добавить проект…"
            onClicked: view.addRequested()
        }
    }

    // ---------- the overview ----------

    Flickable {
        id: overview
        objectName: "projectOverview"
        visible: Projects.currentId !== "" && !Projects.file.path
        anchors.fill: parent
        contentHeight: page.height + 56
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickDeceleration: 4000
        ScrollBar.vertical: ThinScrollBar {}

        Column {
            id: page
            x: (overview.width - width) / 2
            y: 28
            width: Math.min(860, overview.width - 48)
            spacing: 18

            Item {
                width: parent.width
                height: titleColumn.height
                Column {
                    id: titleColumn
                    width: parent.width - actions.width - 16
                    spacing: 4
                    Text {
                        width: parent.width
                        text: Projects.currentName
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 20
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        text: Projects.currentPath
                        elide: Text.ElideMiddle
                        color: Theme.p.faint
                        font.family: Theme.monoFont
                        font.pixelSize: 12
                    }
                }
                Row {
                    id: actions
                    anchors.right: parent.right
                    spacing: 2
                    IconButton { iconName: "external"; tip: "Открыть в проводнике"; onClicked: Projects.openFolder("") }
                    IconButton { iconName: "copy"; tip: "Копировать путь"; onClicked: Projects.copyPath("") }
                    IconButton { iconName: "refresh"; tip: "Обновить (F5)"; onClicked: Projects.refresh() }
                    IconButton {
                        iconName: "trash"
                        tip: "Убрать из списка (папка останется)"
                        onClicked: Projects.askRemoveProject(Projects.currentId)
                    }
                }
            }

            Rectangle {
                visible: Projects.currentMissing
                width: parent.width
                height: 40
                radius: 6
                color: "transparent"
                border.color: Theme.p.error_line
                Text {
                    x: 14
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Папки проекта нет на месте — её переместили или удалили."
                    color: Theme.p.error
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
            }

            // git at a glance
            Rectangle {
                width: parent.width
                height: 52
                radius: 8
                color: Theme.p.card
                border.color: Theme.p.hairline
                Icon {
                    x: 16
                    anchors.verticalCenter: parent.verticalCenter
                    name: "branch"
                    size: 18
                    tint: Projects.git.repo ? Theme.p.accent : Theme.p.faint
                }
                Column {
                    x: 46
                    width: parent.width - 62
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2
                    Text {
                        width: parent.width
                        text: Projects.git.repo === undefined ? "Читаю git…" : Projects.git.summary
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 14
                    }
                    Text {
                        width: parent.width
                        visible: Projects.git.repo === false
                        text: "Дерево без служебных папок (node_modules, .venv, кэши); с git — по .gitignore."
                        elide: Text.ElideRight
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
            }

            // changed files
            Column {
                visible: !!Projects.git.changes && Projects.git.changes.length > 0
                width: parent.width
                spacing: 2
                Text {
                    bottomPadding: 6
                    text: "Изменения"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Projects.git.changes || []
                    delegate: AbstractButton {
                        id: change
                        required property var modelData
                        width: page.width
                        height: 32
                        hoverEnabled: true
                        onClicked: Projects.openFile(modelData.path, true)
                        background: Rectangle { radius: 6; color: change.hovered ? Theme.p.item_hover : "transparent" }
                        contentItem: Item {
                            GitMark {
                                id: changeMark
                                x: 10
                                width: 14
                                anchors.verticalCenter: parent.verticalCenter
                                mark: change.modelData.mark
                            }
                            Text {
                                id: changeName
                                x: 32
                                anchors.verticalCenter: parent.verticalCenter
                                text: change.modelData.name
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 13
                                font.strikeout: change.modelData.deleted
                            }
                            Text {
                                x: changeName.x + changeName.width + 10
                                width: Math.max(0, parent.width - x - 110)
                                anchors.verticalCenter: parent.verticalCenter
                                text: change.modelData.folder
                                elide: Text.ElideMiddle
                                color: Theme.p.faint
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                            Text {
                                anchors.right: parent.right
                                anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                text: change.modelData.label
                                color: changeMark.colour
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                    }
                }
            }

            // recent commits
            Column {
                visible: !!Projects.git.log && Projects.git.log.length > 0
                width: parent.width
                spacing: 2
                Text {
                    bottomPadding: 6
                    text: "Последние коммиты"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Projects.git.log || []
                    delegate: Item {
                        id: commit
                        required property var modelData
                        width: page.width
                        height: 30
                        Icon { x: 8; anchors.verticalCenter: parent.verticalCenter; name: "commit"; size: 14; tint: Theme.p.faint }
                        Text {
                            id: sha
                            x: 30
                            anchors.verticalCenter: parent.verticalCenter
                            text: commit.modelData.sha
                            color: Theme.p.muted
                            font.family: Theme.monoFont
                            font.pixelSize: 12
                        }
                        Text {
                            x: sha.x + 72
                            width: parent.width - x - who.width - 20
                            anchors.verticalCenter: parent.verticalCenter
                            text: commit.modelData.subject
                            elide: Text.ElideRight
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            id: who
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: commit.modelData.author + " · " + commit.modelData.when
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }
                }
            }
        }
    }

    // ---------- an open file ----------

    Item {
        id: fileView
        objectName: "projectFile"
        visible: Projects.currentId !== "" && !!Projects.file.path
        anchors.fill: parent

        Rectangle {
            id: fileHead
            width: parent.width
            height: 44
            color: Theme.p.code_head
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
            IconButton {
                id: back
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                iconName: "back"
                tip: "К обзору проекта"
                onClicked: Projects.closeFile()
            }
            Column {
                x: back.x + back.width + 6
                width: Math.max(0, fileButtons.x - x - 12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Text {
                    width: parent.width
                    textFormat: Text.StyledText
                    text: (Projects.file.folder ? "<font color='" + Theme.p.faint + "'>" + Projects.file.folder + "/</font>" : "")
                          + (Projects.file.name || "")
                    elide: Text.ElideMiddle
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: [Projects.file.sizeText, Projects.file.encoding, Projects.file.lang,
                           Projects.file.truncated ? "показаны первые 2 МБ" : "",
                           Projects.file.linesText].filter(s => !!s).join(" · ")
                    elide: Text.ElideRight
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
            }
            Row {
                id: fileButtons
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Segmented {
                    visible: !!Projects.file.hasDiff
                    anchors.verticalCenter: parent.verticalCenter
                    options: [{ id: "file", name: "Файл" }, { id: "diff", name: "Изменения" }]
                    value: Projects.file.diffMode ? "diff" : "file"
                    onPicked: (id) => Projects.showDiff(id === "diff")
                }
                IconButton {
                    visible: !Projects.file.binary && !Projects.file.diffMode && !Projects.file.missing
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "copy"
                    tip: "Копировать содержимое"
                    onClicked: App.copy(Projects.file.text || "")
                }
                IconButton {
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "external"
                    tip: "Показать в проводнике"
                    onClicked: Projects.openFolder(Projects.file.path)
                }
                IconButton {
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "close"
                    tip: "Закрыть файл"
                    onClicked: Projects.closeFile()
                }
            }
        }

        CodeView {
            id: code
            objectName: "codeView"
            y: fileHead.height
            width: parent.width
            height: parent.height - fileHead.height
            visible: !Projects.file.binary || !!Projects.file.diffMode
            code: Projects.file.diffMode ? (Projects.file.diff || "") : (Projects.file.code || "")
            numbers: Projects.file.numbers || ""
            rich: Projects.file.diffMode ? !!Projects.file.diffRich : !!Projects.file.rich
            showNumbers: !Projects.file.diffMode
            // another file (or the diff instead of the file) starts at the top; a refresh keeps the place
            property string shown
            onCodeChanged: {
                const now = (Projects.file.path || "") + (Projects.file.diffMode ? ":diff" : "")
                if (now !== shown) { shown = now; contentX = 0; contentY = 0 }
            }
        }

        Text {
            visible: (!!Projects.file.binary && !Projects.file.diffMode) || (!!Projects.file.diffMode && !!Projects.file.diffEmpty)
            anchors.centerIn: code
            width: Math.min(420, parent.width - 48)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: Projects.file.diffMode ? "Изменений нет: файл совпадает с последним коммитом (или изменения уже в индексе)."
                                         : "Двоичный файл (" + (Projects.file.sizeText || "") + ") — не показывается."
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
