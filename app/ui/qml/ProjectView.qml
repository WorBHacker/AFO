import QtQuick
import QtQuick.Controls.Basic

// The main area in projects mode: the project's overview (git: branch, changes, recent commits), or an open file
// with line numbers and, for a changed file, its diff.
Item {
    id: view
    signal addRequested()
    signal settingsRequested()
    signal cardEditRequested(string text, bool draft)
    signal exportRequested(Item anchor)

    // one copy each from Python per change: every `Projects.file.x` would convert the whole file (text and HTML)
    readonly property var file: Projects.file
    readonly property var git: Projects.git
    readonly property var links: Projects.links
    readonly property var outline: Projects.outline
    // the open file is in the editor (D47, 14.2): the viewer steps aside for it
    readonly property bool editing: Editor.editing && Editor.projectId === Projects.currentId
                                    && Editor.path === (file.path || "")

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
        visible: Projects.currentId !== "" && !view.file.path
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
                        visible: text !== ""
                        text: Projects.currentDescription
                        elide: Text.ElideRight
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Row {
                        spacing: 8
                        Text {
                            // a long path gives way (in the middle), the badge always shows
                            width: Math.min(implicitWidth, titleColumn.width - cloudBadge.width - 8)
                            anchors.verticalCenter: parent.verticalCenter
                            text: Projects.currentPath
                            elide: Text.ElideMiddle
                            color: Theme.p.faint
                            font.family: Theme.monoFont
                            font.pixelSize: 12
                        }
                        Rectangle {
                            id: cloudBadge
                            objectName: "cloudBadge"
                            anchors.verticalCenter: parent.verticalCenter
                            width: badge.implicitWidth + 14
                            height: 20
                            radius: 10
                            color: "transparent"
                            border.color: Projects.currentCloud ? Theme.p.accent : Theme.p.hairline_strong
                            Text {
                                id: badge
                                anchors.centerIn: parent
                                text: Projects.currentCloud ? "облачные модели — можно" : "только локальные модели"
                                color: Projects.currentCloud ? Theme.p.accent : Theme.p.muted
                                font.family: Theme.uiFont
                                font.pixelSize: 11
                            }
                        }
                    }
                }
                Row {
                    id: actions
                    anchors.right: parent.right
                    spacing: 2
                    IconButton {
                        iconName: "chat"
                        tip: "Новый чат об этом проекте"
                        onClicked: App.newChatAbout(Projects.currentId)
                    }
                    IconButton {
                        objectName: "claudeCodeTerminal"
                        visible: App.claudeCodeInstalled
                        iconName: "agent"
                        tip: "Claude Code в терминале — с инструментами хаба (проекту нужно разрешение на облако)"
                        onClicked: Terminals.newClaudeCode()
                    }
                    IconButton {
                        objectName: "projectSettings"
                        iconName: "sliders"
                        tip: "Настройки проекта: описание, облачные модели"
                        onClicked: view.settingsRequested()
                    }
                    IconButton {
                        iconName: "terminal"
                        tip: Terminals.panelOpen ? "Скрыть терминалы (Ctrl+J)" : "Терминалы проекта (Ctrl+J)"
                        onClicked: Terminals.setPanelOpen(!Terminals.panelOpen)
                    }
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
                    tint: view.git.repo ? Theme.p.accent : Theme.p.faint
                }
                Column {
                    x: 46
                    width: parent.width - 62
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 2
                    Text {
                        width: parent.width
                        text: view.git.repo === undefined ? "Читаю git…" : view.git.summary
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 14
                    }
                    Text {
                        width: parent.width
                        visible: view.git.repo === false
                        text: "Дерево без служебных папок (node_modules, .venv, кэши); с git — по .gitignore."
                        elide: Text.ElideRight
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
            }

            // the project's card (D24): what models learn about the project first
            Column {
                id: cardBox
                objectName: "projectCard"
                width: parent.width
                spacing: 8
                property bool expanded: false
                readonly property var card: Projects.card

                Item {
                    width: parent.width
                    height: 24
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Карточка проекта" + (cardBox.card.confirmed ? " · версия " + cardBox.card.version : "")
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        visible: cardBox.card.state === "confirmed"
                        IconButton {
                            iconName: "edit"
                            tip: "Изменить карточку"
                            onClicked: view.cardEditRequested(cardBox.card.text, false)
                        }
                        IconButton {
                            iconName: "sparkle"
                            tip: "Новый черновик от модели «" + cardBox.card.writer + "»"
                            onClicked: Projects.draftCard()
                        }
                        IconButton {
                            id: exportButton
                            objectName: "cardExport"
                            iconName: "external"
                            tip: "В проект: записать карточку в CLAUDE.md, AGENTS.md или .clinerules"
                            onClicked: view.exportRequested(exportButton)
                        }
                    }
                }

                Text {
                    visible: cardBox.card.state === "none"
                    width: parent.width
                    wrapMode: Text.Wrap
                    lineHeight: 1.3
                    text: "Карточки нет. Модель составит черновик по README, зависимостям, дереву папок и истории git — "
                          + "вы проверите и подтвердите. Подтверждённая карточка — первое, что модели узнают о проекте."
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                PlainButton {
                    visible: cardBox.card.state === "none"
                    iconName: "sparkle"
                    text: "Составить черновик" + (cardBox.card.writer ? " (" + cardBox.card.writer + ")" : "")
                    onClicked: Projects.draftCard()
                }
                Text {
                    visible: cardBox.card.state === "drafting"
                    text: "Модель «" + cardBox.card.writer + "» составляет черновик…"
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    SequentialAnimation on opacity {
                        running: cardBox.card.state === "drafting"
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.4; duration: 700 }
                        NumberAnimation { to: 1; duration: 700 }
                    }
                }
                Rectangle {
                    visible: cardBox.card.state === "draft"
                    width: parent.width
                    height: 48
                    radius: 8
                    color: "transparent"
                    border.color: Theme.p.accent
                    Text {
                        x: 14
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Черновик от «" + cardBox.card.draftModel + "» готов — проверьте и подтвердите"
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Row {
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        PlainButton {
                            kind: "primary"
                            text: "Открыть черновик"
                            onClicked: view.cardEditRequested(cardBox.card.draft, true)
                        }
                        PlainButton { text: "Выбросить"; onClicked: Projects.dropCardDraft() }
                    }
                }
                Text {
                    visible: cardBox.card.missing.length > 0
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Карточка устарела: в ней есть то, чего уже нет в проекте — " + cardBox.card.missing.join(", ")
                    color: Theme.p.warn
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                // the project moved on since the card was confirmed (D24): offer an update of what changed
                Item {
                    objectName: "cardStale"
                    visible: cardBox.card.state === "confirmed" && cardBox.card.changes !== ""
                    width: parent.width
                    height: visible ? Math.max(staleText.height, updateButton.height) : 0
                    Text {
                        id: staleText
                        width: parent.width - updateButton.width - 12
                        anchors.verticalCenter: parent.verticalCenter
                        wrapMode: Text.Wrap
                        text: cardBox.card.changes + " — карточка могла устареть."
                        color: Theme.p.warn
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    PlainButton {
                        id: updateButton
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 28
                        iconName: "sparkle"
                        text: "Обновить карточку"
                        onClicked: Projects.updateCard()
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: "Модель «" + cardBox.card.writer + "» поправит только разделы, которых касаются изменения; вы проверите"
                    }
                }
                Item {
                    visible: cardBox.card.confirmed && cardBox.card.state !== "draft"
                    width: parent.width
                    height: cardBox.expanded ? cardText.height : Math.min(cardText.height, 200)
                    clip: true
                    Column {
                        id: cardText
                        width: parent.width
                        spacing: 10
                        Repeater {
                            model: cardBox.card.segments
                            delegate: Loader {
                                required property var modelData
                                width: cardText.width
                                sourceComponent: modelData.kind === "code" ? cardCode : cardProse
                                onLoaded: item.info = Qt.binding(() => modelData)
                            }
                        }
                    }
                }
                Component { id: cardProse; Prose { width: parent ? parent.width : 0 } }
                Component { id: cardCode; CodeBlock { width: parent ? parent.width : 0 } }
                PlainButton {
                    visible: cardBox.card.confirmed && cardBox.card.state !== "draft" && cardText.height > 200
                    text: cardBox.expanded ? "Свернуть" : "Показать полностью"
                    onClicked: cardBox.expanded = !cardBox.expanded
                }
            }

            // notes for models (D22 L4): facts and decisions a model should always know
            Column {
                objectName: "projectNotes"
                width: parent.width
                spacing: 4
                Text {
                    bottomPadding: 2
                    text: "Заметки для моделей"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Projects.notes
                    delegate: Item {
                        id: note
                        required property var modelData
                        width: page.width
                        height: Math.max(30, noteText.implicitHeight + 10)
                        HoverHandler { id: noteHover }
                        Rectangle { anchors.fill: parent; radius: 6; color: noteHover.hovered ? Theme.p.item_hover : "transparent" }
                        Text {
                            id: noteText
                            x: 10
                            width: parent.width - 50
                            anchors.verticalCenter: parent.verticalCenter
                            text: "• " + note.modelData.text
                            wrapMode: Text.Wrap
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        IconButton {
                            visible: noteHover.hovered
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: 26
                            implicitHeight: 26
                            iconName: "trash"
                            tip: "Удалить заметку"
                            onClicked: Projects.deleteNote(note.modelData.id)
                        }
                    }
                }
                Field {
                    id: newNote
                    objectName: "newNote"
                    width: parent.width
                    placeholderText: "Добавить заметку: например, «деплой только из main» — Enter"
                    onAccepted: { Projects.addNote(text); text = "" }
                }
            }

            // what models wrote here, each undoable, and the project's "always" answers (stage 5, D17)
            Column {
                objectName: "projectWrites"
                visible: Projects.fileChanges.length > 0 || Projects.rules.length > 0
                width: parent.width
                spacing: 4
                Text {
                    bottomPadding: 2
                    text: "Правки моделей"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Projects.fileChanges.slice(0, 10)
                    delegate: Item {
                        id: write
                        required property var modelData
                        width: page.width
                        height: 40
                        HoverHandler { id: writeHover }
                        Rectangle { anchors.fill: parent; radius: 6; color: writeHover.hovered ? Theme.p.item_hover : "transparent" }
                        Icon { x: 10; anchors.verticalCenter: parent.verticalCenter; name: "edit"; size: 15
                               tint: write.modelData.reverted ? Theme.p.faint : Theme.p.text_soft }
                        Column {
                            x: 34
                            width: parent.width - x - 110
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                text: write.modelData.path + (write.modelData.reverted ? " — откачена" : "")
                                elide: Text.ElideMiddle
                                color: write.modelData.reverted ? Theme.p.faint : Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 13
                            }
                            Text {
                                width: parent.width
                                text: write.modelData.note
                                elide: Text.ElideRight
                                color: Theme.p.faint
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                        PlainButton {
                            visible: writeHover.hovered && !write.modelData.reverted
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            implicitHeight: 28
                            text: "Откатить"
                            onClicked: Projects.revertChange(write.modelData.id)
                        }
                    }
                }
                Repeater {
                    model: Projects.rules
                    delegate: Item {
                        id: rule
                        required property var modelData
                        width: page.width
                        height: 30
                        HoverHandler { id: ruleHover }
                        Text {
                            x: 10
                            width: parent.width - 50
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Без вопроса в этом проекте: " + rule.modelData.text
                            elide: Text.ElideRight
                            color: Theme.p.muted
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        IconButton {
                            visible: ruleHover.hovered
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: 26
                            implicitHeight: 26
                            iconName: "trash"
                            tip: "Снова спрашивать"
                            onClicked: Projects.removeRule(rule.modelData.id)
                        }
                    }
                }
            }

            // links with other projects (D25): confirmed ones, and what the hub found evidence for
            Column {
                id: linksBox
                objectName: "projectLinks"
                width: parent.width
                spacing: 4
                Item {
                    width: parent.width
                    height: 24
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Связи с другими проектами"
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                    }
                    PlainButton {
                        objectName: "scanLinks"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 26
                        iconName: "search"
                        enabled: !view.links.scanning
                        text: view.links.scanning ? "Ищу…" : "Найти связи"
                        onClicked: Projects.scanLinks()
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: "Пути к папкам других проектов в файлах зависимостей, имена их пакетов, порты, которые они слушают"
                    }
                }
                Text {
                    visible: view.links.confirmed.length === 0 && view.links.proposed.length === 0
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: "Связей нет. Хаб поищет улики — зависимости, пути, порты — и предложит, а вы подтвердите."
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Repeater {
                    model: view.links.confirmed
                    delegate: Item {
                        id: link
                        required property var modelData
                        width: page.width
                        height: 30
                        HoverHandler { id: linkHover }
                        Rectangle { anchors.fill: parent; radius: 6; color: linkHover.hovered ? Theme.p.item_hover : "transparent" }
                        Text {
                            x: 10
                            width: parent.width - 50
                            anchors.verticalCenter: parent.verticalCenter
                            text: link.modelData.arrow + " " + link.modelData.other + " · " + link.modelData.kind
                                  + (link.modelData.description ? " · " + link.modelData.description : "")
                            elide: Text.ElideRight
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        IconButton {
                            visible: linkHover.hovered
                            anchors.right: parent.right
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            implicitWidth: 26
                            implicitHeight: 26
                            iconName: "trash"
                            tip: "Удалить связь"
                            onClicked: Projects.deleteLink(link.modelData.id)
                        }
                    }
                }
                Repeater {
                    model: view.links.proposed
                    delegate: Rectangle {
                        id: proposal
                        required property var modelData
                        width: page.width
                        height: proposalColumn.height + 20
                        radius: 8
                        color: "transparent"
                        border.color: Theme.p.hairline_strong
                        Column {
                            id: proposalColumn
                            x: 12
                            y: 10
                            width: parent.width - 24
                            spacing: 6
                            Text {
                                text: "Похоже на связь: " + proposal.modelData.title
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                            Repeater {
                                model: proposal.modelData.evidence
                                delegate: Text {
                                    required property string modelData
                                    width: proposalColumn.width
                                    wrapMode: Text.Wrap
                                    text: "• " + modelData
                                    color: Theme.p.muted
                                    font.family: Theme.uiFont
                                    font.pixelSize: 12
                                }
                            }
                            Row {
                                spacing: 6
                                Field {
                                    id: linkNote
                                    width: Math.min(360, proposalColumn.width - 220)
                                    implicitHeight: 30
                                    placeholderText: "Описание, например: REST /api, порт 8000"
                                }
                                PlainButton {
                                    kind: "primary"
                                    implicitHeight: 30
                                    text: "Подтвердить"
                                    onClicked: Projects.confirmLink(proposal.modelData.id, linkNote.text)
                                }
                                PlainButton {
                                    implicitHeight: 30
                                    text: "Не связаны"
                                    onClicked: Projects.rejectLink(proposal.modelData.id)
                                }
                            }
                        }
                    }
                }
            }

            // what runs in the project's folder (the hub scans every few seconds)
            Column {
                objectName: "projectProcesses"
                visible: Projects.processes.length > 0
                width: parent.width
                spacing: 2
                Text {
                    bottomPadding: 6
                    text: "Процессы · " + Projects.processesSummary
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Projects.processes
                    delegate: Item {
                        id: proc
                        required property var modelData
                        width: page.width
                        height: 34
                        HoverHandler { id: procHover }
                        Rectangle { anchors.fill: parent; radius: 6; color: procHover.hovered ? Theme.p.item_hover : "transparent" }
                        Icon {
                            id: procIcon
                            x: 10 + proc.modelData.depth * 16
                            anchors.verticalCenter: parent.verticalCenter
                            name: proc.modelData.hub ? "layers" : "cpu"
                            size: 14
                            tint: proc.modelData.ports.length ? Theme.p.accent : Theme.p.faint
                        }
                        Text {
                            id: procName
                            x: procIcon.x + 22
                            anchors.verticalCenter: parent.verticalCenter
                            text: proc.modelData.name
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            x: procName.x + procName.width + 8
                            width: Math.max(0, ports.x - x - 12)
                            anchors.verticalCenter: parent.verticalCenter
                            text: proc.modelData.args
                            elide: Text.ElideRight
                            color: Theme.p.faint
                            font.family: Theme.monoFont
                            font.pixelSize: 12
                        }
                        Row {
                            id: ports
                            anchors.right: stats.left
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 4
                            Repeater {
                                model: proc.modelData.ports
                                delegate: AbstractButton {
                                    id: port
                                    required property int modelData
                                    implicitWidth: portText.implicitWidth + 12
                                    implicitHeight: 20
                                    hoverEnabled: true
                                    onClicked: Projects.openPort(modelData)
                                    background: Rectangle {
                                        radius: 4
                                        color: port.hovered ? Theme.p.item_selected : "transparent"
                                        border.color: Theme.p.hairline_strong
                                    }
                                    contentItem: Text {
                                        id: portText
                                        text: ":" + port.modelData
                                        color: Theme.p.accent
                                        horizontalAlignment: Text.AlignHCenter
                                        verticalAlignment: Text.AlignVCenter
                                        font.family: Theme.monoFont
                                        font.pixelSize: 11
                                    }
                                    ToolTip.visible: hovered
                                    ToolTip.delay: 600
                                    ToolTip.text: "Открыть http://localhost:" + modelData
                                }
                            }
                        }
                        Text {
                            id: stats
                            anchors.right: stopButton.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: [proc.modelData.cpu, proc.modelData.memory, proc.modelData.age].filter(s => !!s).join(" · ")
                            color: Theme.p.muted
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                        IconButton {
                            id: stopButton
                            anchors.right: parent.right
                            anchors.rightMargin: 2
                            anchors.verticalCenter: parent.verticalCenter
                            opacity: proc.modelData.hub ? 0 : 1
                            enabled: !proc.modelData.hub
                            iconName: "power"
                            hoverTint: Theme.p.error
                            tip: "Завершить (вместе с дочерними)"
                            onClicked: Projects.askStopProcess(proc.modelData.pid)
                        }
                        ToolTip.visible: procHover.hovered && !stopButton.hovered
                        ToolTip.delay: 900
                        ToolTip.text: (proc.modelData.hub ? "Это сам хаб AFO\n" : "") + "PID " + proc.modelData.pid
                                      + (proc.modelData.folder ? " · папка " + proc.modelData.folder : "")
                                      + "\n" + proc.modelData.cmdline
                    }
                }
            }

            // changed files
            Column {
                visible: !!view.git.changes && view.git.changes.length > 0
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
                    model: view.git.changes || []
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

            // what happened here lately (from the event log)
            Column {
                objectName: "projectRecent"
                visible: Feed.recent.length > 0
                width: parent.width
                spacing: 2
                Text {
                    bottomPadding: 6
                    text: "Недавно"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: Feed.recent
                    delegate: Item {
                        id: recentRow
                        required property var modelData
                        width: page.width
                        height: 28
                        Icon {
                            x: 8
                            anchors.verticalCenter: parent.verticalCenter
                            name: recentRow.modelData.icon
                            size: 14
                            tint: recentRow.modelData.level === "error" ? Theme.p.error : Theme.p.faint
                        }
                        Text {
                            x: 30
                            width: parent.width - x - recentTime.width - 20
                            anchors.verticalCenter: parent.verticalCenter
                            text: recentRow.modelData.title
                            elide: Text.ElideRight
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            id: recentTime
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            text: recentRow.modelData.time
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }
                }
            }

            // recent commits
            Column {
                visible: !!view.git.log && view.git.log.length > 0
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
                    model: view.git.log || []
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
        visible: Projects.currentId !== "" && !!view.file.path
        anchors.fill: parent

        // the editor's tabs (D47, 14.4): the files open for editing, a dot on those with unsaved edits
        Rectangle {
            id: tabBar
            objectName: "editorTabs"
            width: parent.width
            height: tabList.count > 0 ? 32 : 0
            visible: tabList.count > 0
            color: Theme.p.panel
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
            ListView {
                id: tabList
                anchors.fill: parent
                orientation: ListView.Horizontal
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: Editor.tabs
                delegate: Rectangle {
                    id: tabItem
                    required property string key
                    required property string path
                    required property string name
                    required property string folder
                    required property bool dirty
                    required property bool restored
                    readonly property bool shown: view.editing && Editor.active === key
                    objectName: "editorTab"
                    width: tabRow.implicitWidth + 20
                    height: tabList.height
                    color: shown ? Theme.p.code_bg : tabMouse.containsMouse ? Theme.p.item_hover : "transparent"
                    Rectangle { visible: tabItem.shown; width: parent.width; height: 2; color: Theme.p.accent }
                    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.p.hairline }
                    MouseArea {
                        id: tabMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                        onClicked: (mouse) => {
                            if (mouse.button === Qt.MiddleButton) Projects.closeTab(tabItem.key)
                            else Projects.openFile(tabItem.path, false)
                        }
                        ToolTip.visible: containsMouse && tabItem.folder !== ""
                        ToolTip.delay: 800
                        ToolTip.text: tabItem.folder + "/" + tabItem.name
                    }
                    Row {
                        id: tabRow
                        x: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: tabItem.name
                            color: tabItem.shown ? Theme.p.text : Theme.p.muted
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                            font.italic: tabItem.restored
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: tabItem.restored
                            text: "восстановлено"
                            color: Theme.p.warn
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                        Item {  // the dot while there are unsaved edits; its × under the mouse
                            width: 16
                            height: 16
                            anchors.verticalCenter: parent.verticalCenter
                            Rectangle {
                                anchors.centerIn: parent
                                visible: tabItem.dirty && !tabMouse.containsMouse && !tabClose.hovered
                                width: 8
                                height: 8
                                radius: 4
                                color: Theme.p.accent
                            }
                            IconButton {
                                id: tabClose
                                objectName: "closeTab"
                                anchors.fill: parent
                                visible: !tabItem.dirty || tabMouse.containsMouse || hovered
                                iconName: "close"
                                tip: "Закрыть вкладку"
                                onClicked: Projects.closeTab(tabItem.key)
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: fileHead
            y: tabBar.height
            width: parent.width
            height: 44
            color: Theme.p.code_head
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
            IconButton {
                id: back
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                iconName: "back"
                tip: "К обзору проекта (вкладки остаются)"
                onClicked: Projects.closeView()
            }
            Column {
                x: back.x + back.width + 6
                width: Math.max(0, fileButtons.x - x - 12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Text {
                    width: parent.width
                    textFormat: Text.StyledText
                    text: (view.file.folder ? "<font color='" + Theme.p.faint + "'>" + view.file.folder + "/</font>" : "")
                          + (view.file.name || "") + (view.editing && Editor.dirty ? "  <font color='" + Theme.p.accent
                          + "'>● не сохранено</font>" : "")
                          + (view.editing && Editor.restored ? "  <font color='" + Theme.p.warn
                          + "'>восстановлено после сбоя</font>" : "")
                    elide: Text.ElideMiddle
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: view.file.missing ? "удалён с диска"
                          : [view.file.sizeText, view.file.encoding, view.file.eol, view.file.lang,
                             view.file.truncated ? "показаны первые 2 МБ" : "",
                             view.file.linesText].filter(s => !!s).join(" · ")
                    elide: Text.ElideRight
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                Text {  // bidi or zero-width characters: the code may read differently from what it does (D46)
                    objectName: "fileHidden"
                    width: parent.width
                    visible: !!view.file.hidden && !view.file.missing
                    text: view.file.hidden || ""
                    elide: Text.ElideRight
                    color: Theme.p.error
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
                PlainButton {
                    objectName: "editFile"
                    visible: !view.editing && !view.file.diffMode && !view.file.binary && !view.file.missing
                    enabled: !view.file.editBlock && !Editor.busy
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "edit"
                    text: "Править"
                    ToolTip.visible: hovered && !!view.file.editBlock
                    ToolTip.text: view.file.editBlock || ""
                    onClicked: Editor.open(Projects.currentId, view.file.path)
                }
                PlainButton {
                    objectName: "saveFile"
                    visible: view.editing
                    enabled: Editor.dirty && !Editor.busy
                    anchors.verticalCenter: parent.verticalCenter
                    kind: "primary"
                    iconName: "check"
                    text: Editor.busy ? "Сохраняю…" : "Сохранить"
                    onClicked: { Editor.check(); Editor.save() }
                }
                PlainButton {
                    objectName: "doneEditing"
                    visible: view.editing
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Готово"
                    onClicked: Editor.stop()
                }
                Segmented {
                    visible: !!view.file.hasDiff && !view.editing
                    anchors.verticalCenter: parent.verticalCenter
                    options: [{ id: "file", name: "Файл" }, { id: "diff", name: "Изменения" }]
                    value: view.file.diffMode ? "diff" : "file"
                    onPicked: (id) => Projects.showDiff(id === "diff")
                }
                IconButton {
                    id: outlineButton
                    objectName: "outlineButton"
                    visible: !!view.file.outline && !view.file.diffMode && !view.file.missing
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "outline"
                    tip: "Структура файла: классы, функции, методы"
                    onClicked: { Projects.loadOutline(); outlinePopup.open() }
                }
                IconButton {
                    objectName: "pinFile"
                    visible: !view.file.binary && !view.file.missing && App.currentId !== ""
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "pin"
                    tip: "Закрепить в контексте открытого чата: «" + App.currentTitle + "»"
                    onClicked: App.pinFile(Projects.currentId, view.file.path)
                }
                IconButton {
                    visible: !view.file.binary && !view.file.diffMode && !view.file.missing
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "copy"
                    tip: "Копировать содержимое"
                    onClicked: Projects.copyFile()
                }
                IconButton {
                    objectName: "openInCode"
                    visible: !view.file.missing
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "code"
                    tip: "Открыть в VS Code" + (view.editing ? " на строке курсора" : "")
                    onClicked: Projects.openInCode(view.editing ? Editor.line : (view.file.gotoLine || 1))
                }
                IconButton {
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "external"
                    tip: "Показать в проводнике"
                    onClicked: Projects.openFolder(view.file.path)
                }
                IconButton {
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "close"
                    tip: "Закрыть файл"
                    onClicked: Projects.closeFile()
                }
            }
        }

        // the file's definitions from the symbol index (D26); a click shows the line
        Popup {
            id: outlinePopup
            objectName: "outlinePopup"
            parent: Overlay.overlay
            property real room: 520  // down to the window's bottom edge, set on opening
            width: 380
            height: Math.min(room, 520, Math.max(outlineList.contentHeight, 40) + 44)
            onAboutToShow: {
                // under the button, its right edges in line (a binding to mapToItem would not follow the layout)
                const at = outlineButton.mapToItem(Overlay.overlay, outlineButton.width, outlineButton.height)
                x = Math.max(8, at.x - width)
                y = at.y + 6
                room = Math.max(120, Overlay.overlay.height - y - 8)
            }
            padding: 6
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            background: Rectangle {
                radius: 8
                color: Theme.p.popup
                border.color: Theme.p.hairline_strong
                SoftShadow {}
            }
            contentItem: Item {
                Text {
                    id: outlineHead
                    x: 8
                    width: parent.width - 16
                    height: 28
                    verticalAlignment: Text.AlignVCenter
                    text: view.outline.state === "loading" && view.outline.rows.length === 0 ? "Читаю…"
                          : view.outline.state === "failed" ? "Структуру получить не удалось"
                          : view.outline.rows.length === 0 ? "Определений нет"
                          : "Структура · " + view.outline.rows.length
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                ListView {
                    id: outlineList
                    objectName: "outlineList"
                    y: outlineHead.height + 4
                    width: parent.width
                    height: parent.height - y
                    clip: true
                    model: view.outline.rows
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ThinScrollBar {}
                    delegate: AbstractButton {
                        id: entry
                        required property var modelData
                        width: outlineList.width
                        height: 28
                        hoverEnabled: true
                        onClicked: { Projects.openFileAt(view.file.path, modelData.line); outlinePopup.close() }
                        ToolTip.visible: hovered
                        ToolTip.delay: 700
                        ToolTip.text: modelData.tip
                        background: Rectangle { radius: 5; color: entry.hovered ? Theme.p.item_hover : "transparent" }
                        contentItem: Item {
                            SymbolBadge {
                                id: entryBadge
                                x: 6 + entry.modelData.depth * 16
                                anchors.verticalCenter: parent.verticalCenter
                                badge: entry.modelData.badge
                            }
                            Text {
                                x: entryBadge.x + entryBadge.width + 8
                                width: parent.width - x - 48
                                anchors.verticalCenter: parent.verticalCenter
                                text: entry.modelData.name
                                elide: Text.ElideRight
                                color: Theme.p.text
                                font.family: Theme.monoFont
                                font.pixelSize: 12
                            }
                            Text {
                                anchors.right: parent.right
                                anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: entry.modelData.line
                                color: Theme.p.faint
                                font.family: Theme.monoFont
                                font.pixelSize: 11
                            }
                        }
                    }
                }
            }
        }

        // the disk and the editor disagree (D47, 14.2): another program, a model or a command changed the file
        Rectangle {
            id: conflictBar
            objectName: "conflictBar"
            y: fileHead.y + fileHead.height
            width: parent.width
            height: visible ? 40 : 0
            visible: view.editing && !!Editor.conflict.reason
            color: Theme.p.card
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
            Icon {
                x: 14
                anchors.verticalCenter: parent.verticalCenter
                name: "refresh"
                size: 14
                tint: Theme.p.warn
            }
            Text {
                x: 36
                width: Math.max(0, conflictButtons.x - x - 12)
                anchors.verticalCenter: parent.verticalCenter
                text: (Editor.conflict.message || "") + (Editor.dirty ? " У вас есть несохранённые правки." : "")
                elide: Text.ElideRight
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Row {
                id: conflictButtons
                anchors.right: parent.right
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                PlainButton {
                    objectName: "takeDisk"
                    visible: Editor.conflict.reason === "changed"
                    text: "Взять с диска"
                    onClicked: Editor.takeDisk()
                }
                PlainButton {
                    objectName: "compareDisk"
                    text: "Сравнить"
                    onClicked: { Editor.compare(); compareSheet.open() }
                }
                PlainButton {
                    objectName: "keepMine"
                    kind: "primary"
                    text: Editor.conflict.reason === "gone" ? "Создать заново" : "Оставить мой"
                    onClicked: Editor.keepMine()
                }
            }
        }

        Repeater {  // a CodeEditor per tab (14.4): each keeps its document and its undo history while another shows
            model: Editor.tabs
            delegate: CodeEditor {
                required property string key
                tabKey: key
                y: fileHead.y + fileHead.height + conflictBar.height
                width: fileView.width
                height: fileView.height - y - editorStatus.height
                visible: view.editing && Editor.active === key
                onVisibleChanged: if (visible) focusText()
            }
        }

        // the editor's bottom line: where the cursor is, encoding, line ends, indentation
        Rectangle {
            id: editorStatus
            objectName: "editorStatus"
            anchors.bottom: parent.bottom
            width: parent.width
            height: view.editing ? 24 : 0
            visible: view.editing
            color: Theme.p.code_head
            Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
            Text {  // the model's grey text (14.6): on or off, a pause - and its menu
                id: ghostStatus
                objectName: "ghostStatus"
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, parent.width / 2)
                elide: Text.ElideRight
                text: Ghost.state !== "" ? Ghost.state
                      : Ghost.everywhere && Ghost.inProject(Projects.currentId) ? "Дописывание: вкл ▾" : "Дописывание: выкл ▾"
                color: Ghost.paused !== "" ? Theme.p.warn : Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 11
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ghostMenu.openAt(ghostStatus) }
            }
            MenuPopup {
                id: ghostMenu
                menuWidth: 340
                wrapNotes: true
                items: [
                    { id: "project", name: "Дописывание в этом проекте", icon: "sparkle",
                      selected: Ghost.inProject(Projects.currentId) },
                    { id: "everywhere", name: "Дописывание везде", icon: "sparkle", selected: Ghost.everywhere }
                ].concat(Ghost.paused === "not_loaded" ? [{ id: "load", name: "Загрузить модель дописывания", icon: "cpu",
                         note: "Может выгрузить модель чата из видеокарты, если обе не помещаются рядом" }] : [])
                onChosen: (id) => id === "project" ? Ghost.setInProject(Projects.currentId, !Ghost.inProject(Projects.currentId))
                                 : id === "everywhere" ? Ghost.setEverywhere(!Ghost.everywhere) : Ghost.load()
                function openAt(item) { anchor = item; open() }
            }
            Text {
                objectName: "editorStatusText"
                x: 12
                anchors.verticalCenter: parent.verticalCenter
                width: ghostStatus.x - 24
                text: [Editor.position, Editor.status].filter(s => !!s).join("  ·  ")
                      + (Editor.hidden ? "  ·  ⚠ " + Editor.hidden : "")
                elide: Text.ElideRight
                color: Editor.hidden ? Theme.p.error : Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 11
            }
        }

        Popup {
            id: compareSheet
            objectName: "compareSheet"
            parent: Overlay.overlay
            anchors.centerIn: parent
            width: Math.min(900, (parent ? parent.width : 900) - 48)
            height: Math.min(640, (parent ? parent.height : 640) - 48)
            modal: true
            padding: 0
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
            background: Rectangle {
                radius: 10
                color: Theme.p.popup
                border.color: Theme.p.hairline_strong
                SoftShadow {}
            }
            contentItem: Item {
                Text {
                    x: 16
                    height: 40
                    verticalAlignment: Text.AlignVCenter
                    text: Editor.compareView.empty ? "Различий нет: на диске то же, что у вас"
                          : "Что изменит сохранение: на диске → у вас"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }
                CodeView {
                    y: 40
                    width: parent.width
                    height: parent.height - y
                    source: Editor.compareView
                    showNumbers: false
                }
            }
        }

        CodeView {
            id: code
            objectName: "codeView"
            y: fileHead.y + fileHead.height
            width: parent.width
            height: parent.height - fileHead.y - fileHead.height
            visible: !view.editing && (!!view.file.diffMode || (!view.file.binary && !view.file.missing))
            source: Projects.fileShown
            showNumbers: !view.file.diffMode
            gotoOffset: view.file.gotoOffset === undefined ? -1 : view.file.gotoOffset
            gotoSeq: view.file.gotoSeq || 0
        }

        Text {
            visible: view.file.diffMode ? !!view.file.diffEmpty : (!!view.file.binary || !!view.file.missing)
            anchors.centerIn: code
            width: Math.min(420, parent.width - 48)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: view.file.diffMode ? "Изменений нет: файл совпадает с последним коммитом (или изменения уже в индексе)."
                  : view.file.missing ? "Файла больше нет на диске." + (view.file.hasDiff ? " Его удаление — во вкладке «Изменения»." : "")
                  : "Двоичный файл (" + (view.file.sizeText || "") + ") — не показывается."
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
