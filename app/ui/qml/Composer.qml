import QtQuick
import QtQuick.Controls.Basic

// The input card: text that grows with what you type, the model and effort chips, send / stop.
Item {
    id: composer
    signal modelMenuRequested(Item anchor)
    signal effortMenuRequested(Item anchor)
    signal projectMenuRequested(Item anchor)
    signal contextRequested()
    readonly property int column: Math.min(780, width - 48)
    property alias text: area.text
    property alias modelChip: modelChip
    property int mentionIndex: 0
    implicitHeight: card.y + card.height + 20

    function focusInput() { area.forceActiveFocus() }
    // an item of the @-menu replaces what was typed after the @ (deeper: a project with "/" to go on to its files)
    function chooseMention(index, deeper) {
        const item = Mentions.items[index]
        if (!item) return
        const insert = deeper && item.kind === "project" ? "@" + item.name + "/" : item.insert
        area.remove(Mentions.start, area.cursorPosition)
        area.insert(Mentions.start, insert)
    }
    function submit() {
        const message = area.text
        if (!App.busyNow) {
            if (message.trim() === "") return
            area.text = ""
        }
        App.send(message)
    }

    Connections {
        target: App
        function onDraftRestored(text) { if (area.text === "") area.text = text }
    }

    // a project the draft names without @: attach it?
    Rectangle {
        id: suggestion
        objectName: "mentionSuggestion"
        visible: Mentions.suggestions.length > 0
        x: card.x
        y: 4
        width: composer.column
        height: visible ? 34 : 0
        radius: 8
        color: Theme.p.item_hover
        Icon {
            id: suggestionIcon
            x: 10
            anchors.verticalCenter: parent.verticalCenter
            name: "folder"
            size: 15
            tint: Theme.p.accent
        }
        Text {
            x: suggestionIcon.x + suggestionIcon.width + 8
            width: suggestionButtons.x - x - 8
            anchors.verticalCenter: parent.verticalCenter
            text: (Mentions.suggestions.length === 1 ? "В сообщении упомянут проект «" : "В сообщении упомянуты проекты «")
                  + Mentions.suggestions.map(p => p.name).join("», «") + "» — подключить к чату?"
            elide: Text.ElideRight
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
        Row {
            id: suggestionButtons
            anchors.right: parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            PlainButton {
                objectName: "attachSuggested"
                implicitHeight: 26
                text: "Подключить"
                onClicked: Mentions.attachSuggested()
            }
            IconButton {
                implicitWidth: 26
                implicitHeight: 26
                iconName: "close"
                tip: "Не предлагать в этом чате"
                onClicked: Mentions.waveAway()
            }
        }
    }

    // the @-menu: projects, or a project's files after "@project/"
    Popup {
        id: mentionMenu
        objectName: "mentionMenu"
        parent: card
        visible: Mentions.items.length > 0 && area.activeFocus
        x: 6
        y: -height - 6
        width: Math.min(460, card.width - 12)
        padding: 6
        closePolicy: Popup.NoAutoClose
        background: Rectangle {
            radius: 8
            color: Theme.p.popup
            border.color: Theme.p.hairline_strong
            SoftShadow {}
        }
        onVisibleChanged: composer.mentionIndex = 0
        contentItem: Column {
            Repeater {
                model: Mentions.items
                delegate: AbstractButton {
                    id: choice
                    required property var modelData
                    required property int index
                    width: mentionMenu.availableWidth
                    height: 34
                    hoverEnabled: true
                    onClicked: composer.chooseMention(index, false)
                    background: Rectangle {
                        radius: 6
                        color: choice.index === composer.mentionIndex ? Theme.p.item_selected
                               : (choice.hovered ? Theme.p.item_hover : "transparent")
                    }
                    contentItem: Item {
                        Icon {
                            id: choiceIcon
                            x: 8
                            anchors.verticalCenter: parent.verticalCenter
                            name: choice.modelData.kind === "file" ? "file" : "folder"
                            size: 15
                            tint: Theme.p.text_soft
                        }
                        Text {
                            id: choiceName
                            x: choiceIcon.x + choiceIcon.width + 8
                            width: Math.min(implicitWidth, parent.width - x - 8)
                            anchors.verticalCenter: parent.verticalCenter
                            text: choice.modelData.name
                            elide: Text.ElideMiddle
                            color: Theme.p.text
                            font.family: choice.modelData.kind === "file" ? Theme.monoFont : Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            x: choiceName.x + choiceName.width + 10
                            width: Math.max(0, parent.width - x - 8)
                            anchors.verticalCenter: parent.verticalCenter
                            text: choice.modelData.note || ""
                            elide: Text.ElideRight
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                    }
                }
            }
            Text {
                width: mentionMenu.availableWidth
                topPadding: 4
                leftPadding: 8
                text: "Enter — выбрать" + (Mentions.items.length && Mentions.items[0].kind === "project" ? ", Tab — файл проекта" : "")
                      + ", Esc — закрыть"
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 11
            }
        }
    }

    MenuPopup {
        id: refusalMenu
        objectName: "chatRefusalMenu"
        menuWidth: 420
        wrapNotes: true
        items: App.chatRefusalItems
        onChosen: (id) => App.setChatRefusal(id)
    }

    Rectangle {
        id: card
        x: (parent.width - composer.column) / 2
        y: suggestion.visible ? suggestion.height + 10 : 4
        width: composer.column
        height: input.height + controls.height + 24
        radius: 8
        color: Theme.p.card
        border.color: area.activeFocus ? Theme.p.hairline_strong : Theme.p.hairline

        ScrollView {
            id: input
            x: 6
            y: 6
            width: parent.width - 12
            height: Math.min(200, Math.max(40, area.implicitHeight))
            ScrollBar.vertical: ThinScrollBar {}
            TextArea {
                id: area
                objectName: "composerInput"
                wrapMode: TextArea.Wrap
                placeholderText: App.busyNow ? "Пока модель отвечает, можно набирать следующий вопрос…"
                                             : "Спросите что-нибудь…  @ — проект или файл, Enter — отправить, Shift+Enter — новая строка"
                placeholderTextColor: Theme.p.faint
                color: Theme.p.text
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                leftPadding: 10
                rightPadding: 10
                topPadding: 8
                bottomPadding: 8
                background: null
                // a keystroke changes the text and the cursor: one look at the draft after both, not one per change
                onTextChanged: mentionsLook.restart()
                onCursorPositionChanged: mentionsLook.restart()
                Timer { id: mentionsLook; interval: 0; onTriggered: Mentions.typed(area.text, area.cursorPosition) }
                onActiveFocusChanged: if (!activeFocus) Mentions.close()
                Keys.onPressed: (event) => {
                    if (mentionMenu.visible) {  // the @-menu has the keys it needs
                        const count = Mentions.items.length
                        if (event.key === Qt.Key_Down || event.key === Qt.Key_Up) {
                            composer.mentionIndex = (composer.mentionIndex + (event.key === Qt.Key_Down ? 1 : count - 1)) % count
                            event.accepted = true
                            return
                        }
                        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Tab) {
                            composer.chooseMention(composer.mentionIndex, event.key === Qt.Key_Tab)
                            event.accepted = true
                            return
                        }
                        if (event.key === Qt.Key_Escape) {
                            Mentions.close()
                            event.accepted = true
                            return
                        }
                    }
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                            && !(event.modifiers & Qt.ShiftModifier)) {
                        event.accepted = true
                        if (!App.busyNow) composer.submit()
                    }
                }
            }
        }

        Item {
            id: controls
            x: 10
            y: input.y + input.height + 4
            width: parent.width - 20
            height: Math.max(34, chips.height + 4)
            Flow {  // the chips wrap onto a second line when they do not fit beside the send button
                id: chips
                width: parent.width - sendRow.width - 12
                spacing: 6
                anchors.verticalCenter: parent.verticalCenter
                Chip {
                    id: modelChip
                    iconName: App.modelCloud ? "cloud" : "cpu"
                    text: App.modelName
                    onClicked: composer.modelMenuRequested(modelChip)
                }
                Chip {
                    id: effortChip
                    iconName: "sparkle"
                    text: "Глубина: " + App.effortLabel
                    onClicked: composer.effortMenuRequested(effortChip)
                }
                Chip {
                    id: refusalChip
                    objectName: "chatRefusal"
                    visible: App.chatRefusalShown
                    iconName: "key"
                    text: App.chatRefusalName
                    onClicked: { refusalMenu.anchor = refusalChip; refusalMenu.open() }
                    ToolTip.visible: hovered && !refusalMenu.visible
                    ToolTip.delay: 600
                    ToolTip.text: "Вектор Strata, который убирает у модели отказы отвечать, — для этого чата"
                }
                Chip {
                    id: projectChip
                    objectName: "chatProjects"
                    readonly property var hidden: App.chatProjects.filter(p => p.hidden).map(p => p.name)
                    visible: App.chatProjectChoices.length > 0
                    iconName: App.chatProjects.length ? "folder" : "folder-plus"
                    active: App.chatProjects.length > 0
                    text: App.chatProjects.length ? App.chatProjects.map(p => p.name).join(", ") : "Проект"
                    onClicked: composer.projectMenuRequested(projectChip)
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: hidden.length
                                  ? "Облачная модель увидит только название: " + hidden.join(", ") + " (в настройках проекта облако не разрешено)"
                                  : App.chatProjects.length ? "Модель читает эти проекты инструментами хаба"
                                                            : "Прикрепить проект: модель сможет читать его файлы, git, терминалы"
                }
                Chip {
                    objectName: "contextChip"
                    visible: App.chatProjects.length > 0 && App.currentId !== ""
                    iconName: "layers"
                    menu: false
                    text: App.contextTokens > 0 ? "Контекст ≈ " + App.contextTokens : "Контекст"
                    onClicked: composer.contextRequested()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: "Что хаб сообщает модели о проектах: слои, размер, закреплённые файлы"
                }
            }
            Row {
                id: sendRow
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 1
                spacing: 10
                AbstractButton {
                    id: send
                    width: 32
                    height: 32
                    hoverEnabled: true
                    enabled: App.busyNow || area.text.trim() !== ""
                    onClicked: composer.submit()
                    background: Rectangle {
                        radius: 6
                        color: App.busyNow ? "transparent"
                               : (send.enabled ? (send.hovered ? Theme.p.accent_hover : Theme.p.accent) : Theme.p.item_hover)
                        border.color: App.busyNow ? Theme.p.error : "transparent"
                    }
                    contentItem: Item {
                        Icon {
                            anchors.centerIn: parent
                            name: App.busyNow ? "stop" : "send"
                            tint: App.busyNow ? Theme.p.error : (send.enabled ? Theme.p.on_accent : Theme.p.faint)
                        }
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: App.busyNow ? "Остановить ответ" : "Отправить (Enter)"
                }
            }
        }
    }
}
