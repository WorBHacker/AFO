import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

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
        if (App.chatQuestion.id && message.trim() !== "") {  // the model asked: the input answers it
            area.text = ""
            App.answerChat(message)
            return
        }
        if (Goal.steering && message.trim() !== "") {  // the goal works: a word to it, read at its next turn
            area.text = ""
            App.send(message)
            return
        }
        if (!App.busyNow) {
            if (message.trim() === "" && App.attachments.length === 0) return
            area.text = ""
        }
        App.send(message)
    }

    Connections {
        target: App
        function onDraftRestored(text) { if (area.text === "") area.text = text }
        // each chat has its own draft: another chat opened, its text in the input
        function onCurrentChanged() {
            const kept = App.draftFor(App.currentId)
            if (area.text !== kept) area.text = kept
        }
    }
    Component.onCompleted: area.text = App.draftFor(App.currentId)  // typed before the window closed or crashed

    // the model asks (its ask_user, Claude Code's question): the answers it offers, or one's own in the input
    Rectangle {
        id: asked
        objectName: "chatQuestion"
        visible: !!App.chatQuestion.id
        x: (parent.width - composer.column) / 2
        y: 4
        width: composer.column
        height: visible ? askedBody.height + 24 : 0
        radius: 8
        color: Theme.p.card
        border.color: Theme.p.warn
        Column {
            id: askedBody
            x: 14
            y: 12
            width: parent.width - 28
            spacing: 8
            Row {
                spacing: 8
                Icon { name: "chat"; size: 15; tint: Theme.p.warn; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: qsTr("Модель спрашивает")
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                text: App.chatQuestion.question || ""
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
            }
            Flow {
                width: parent.width
                spacing: 6
                visible: (App.chatQuestion.options || []).length > 0
                Repeater {
                    model: App.chatQuestion.options || []
                    delegate: PlainButton {
                        required property var modelData
                        required property int index
                        objectName: "chatAnswer_" + index
                        text: modelData.label
                        onClicked: App.answerChat(modelData.label)
                        ToolTip.visible: hovered && !!modelData.description
                        ToolTip.delay: 400
                        ToolTip.text: modelData.description || ""
                    }
                }
            }
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                text: (App.chatQuestion.options || []).length ? qsTr("Или напишите свой ответ в поле ниже — Enter отправит его")
                                                              : qsTr("Ответьте в поле ниже — Enter отправит ответ")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }

    // a project the draft names without @: attach it?
    Rectangle {
        id: suggestion
        objectName: "mentionSuggestion"
        visible: Mentions.suggestions.length > 0
        x: card.x
        y: asked.visible ? asked.height + 10 : 4
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
            // the names in the language's quotes: «a», «b» - “a”, “b”
            text: (Mentions.suggestions.length === 1 ? qsTr("В сообщении упомянут проект «%1» — подключить к чату?")
                                                     : qsTr("В сообщении упомянуты проекты «%1» — подключить к чату?"))
                  .arg(Mentions.suggestions.map(p => p.name).join(App.language === "ru" ? "», «" : "”, “"))
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
                text: qsTr("Подключить")
                onClicked: Mentions.attachSuggested()
            }
            IconButton {
                implicitWidth: 26
                implicitHeight: 26
                iconName: "close"
                tip: qsTr("Не предлагать в этом чате")
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
                text: Mentions.items.length && Mentions.items[0].kind === "project" ? qsTr("Enter — выбрать, Tab — файл проекта, Esc — закрыть")
                                                                                 : qsTr("Enter — выбрать, Esc — закрыть")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 11
            }
        }
    }

    FileDialog {
        id: fileDialog
        title: qsTr("Файлы и картинки для модели")
        fileMode: FileDialog.OpenFiles
        onAccepted: App.attachFiles(selectedFiles)
    }
    MenuPopup {
        id: modeMenu
        objectName: "chatModeMenu"
        menuWidth: 440
        wrapNotes: true
        items: App.chatModeItems
        onChosen: (id) => App.setChatMode(id)
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
        y: (suggestion.visible ? suggestion.y + suggestion.height + 6 : (asked.visible ? asked.height + 10 : 4))
        width: composer.column
        height: input.y + input.height + controls.height + 18
        radius: 8
        color: Theme.p.card
        border.color: dropHere.containsDrag ? Theme.p.accent : area.activeFocus ? Theme.p.hairline_strong : Theme.p.hairline

        // files dropped on the composer go with the next message (2026-10-29)
        DropArea {
            id: dropHere
            anchors.fill: parent
            onDropped: (drop) => { if (drop.hasUrls) App.attachFiles(drop.urls) }
        }

        // the files and pictures going with the next message
        Column {
            id: attached
            objectName: "attachments"
            visible: App.attachments.length > 0
            x: 10
            y: 8
            width: parent.width - 20
            spacing: 6
            Flow {
                width: parent.width
                spacing: 6
                Repeater {
                    model: App.attachments
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: fileRow.implicitWidth + 12
                        height: 28
                        radius: 6
                        color: Theme.p.item_hover
                        border.color: Theme.p.hairline
                        Row {
                            id: fileRow
                            x: 8
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6
                            Icon {
                                name: modelData.picture ? "image" : "file"
                                size: 14
                                tint: Theme.p.text_soft
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: modelData.name
                                width: Math.min(implicitWidth, 220)
                                elide: Text.ElideMiddle
                                color: Theme.p.text
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            Text {
                                text: modelData.size
                                color: Theme.p.faint
                                font.family: Theme.uiFont
                                font.pixelSize: 11
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            IconButton {
                                objectName: "removeAttachment_" + index
                                implicitWidth: 20
                                implicitHeight: 20
                                iconName: "close"
                                tip: qsTr("Не отправлять этот файл")
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: App.removeAttachment(index)
                            }
                        }
                    }
                }
            }
            Text {
                visible: App.attachWarning !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: App.attachWarning
                color: Theme.p.warn
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }

        ScrollView {
            id: input
            x: 6
            y: attached.visible ? attached.y + attached.height + 2 : 6
            width: parent.width - 12
            height: Math.min(200, Math.max(40, area.implicitHeight))
            ScrollBar.vertical: ThinScrollBar {}
            TextArea {
                id: area
                objectName: "composerInput"
                wrapMode: TextArea.Wrap
                placeholderText: Goal.steering ? qsTr("Цель в работе: напишите уточнение — модель прочтёт его в начале следующего хода, не прерывая этот")
                                 : App.busyNow ? qsTr("Пока модель отвечает, можно набирать следующий вопрос…")
                                 : qsTr("Спросите что-нибудь…  @ — проект или файл, /цель — работать до результата, Enter — отправить")
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
                onTextChanged: {
                    mentionsLook.restart()
                    App.keepDraft(area.text)
                }
                onCursorPositionChanged: mentionsLook.restart()
                Timer { id: mentionsLook; interval: 0; onTriggered: Mentions.typed(area.text, area.cursorPosition) }
                onActiveFocusChanged: if (!activeFocus) Mentions.close()
                Keys.onPressed: (event) => {
                    if (event.matches(StandardKey.Paste) && App.pasteImage()) {  // a picture or files: attached
                        event.accepted = true
                        return
                    }
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
                        if (!App.busyNow || App.chatQuestion.id || (Goal.steering && area.text.trim() !== ""))
                            composer.submit()
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
                    text: qsTr("Глубина: %1").arg(App.effortLabel)
                    onClicked: composer.effortMenuRequested(effortChip)
                }
                Chip {
                    id: modeChip
                    objectName: "chatModeChip"
                    iconName: "shield"
                    text: App.chatModeName
                    onClicked: { modeMenu.anchor = modeChip; modeMenu.open() }
                    ToolTip.visible: hovered && !modeMenu.visible
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Права модели в этом чате: что она делает без вопроса")
                }
                Chip {
                    id: goalChip
                    objectName: "goalChip"
                    iconName: "target"
                    menu: false
                    active: Goal.shown
                    text: Goal.shown ? qsTr("Цель: %1").arg(Goal.statusText) : qsTr("Цель")
                    onClicked: {
                        if (Goal.shown) {
                            Goal.toggle()
                            return
                        }
                        const wish = area.text
                        area.text = ""
                        Goal.openEditor(wish)
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: Goal.shown ? qsTr("Подробнее о цели: критерии, чего не хватает")
                                             : qsTr("Режим «Цель»: модель работает ход за ходом, пока хаб не убедится, что цель достигнута. То же — «/цель …» в поле")
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
                    ToolTip.text: qsTr("Вектор Strata, который убирает у модели отказы отвечать, — для этого чата")
                }
                Chip {
                    id: projectChip
                    objectName: "chatProjects"
                    readonly property var hidden: App.chatProjects.filter(p => p.hidden).map(p => p.name)
                    visible: App.chatProjectChoices.length > 0
                    iconName: App.chatProjects.length ? "folder" : "folder-plus"
                    active: App.chatProjects.length > 0
                    text: App.chatProjects.length ? App.chatProjects.map(p => p.name).join(", ") : qsTr("Проект")
                    onClicked: composer.projectMenuRequested(projectChip)
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: hidden.length
                                  ? qsTr("Облачная модель увидит только название: %1 (в настройках проекта облако не разрешено)").arg(hidden.join(", "))
                                  : App.chatProjects.length ? qsTr("Модель читает эти проекты инструментами хаба")
                                                            : qsTr("Прикрепить проект: модель сможет читать его файлы, git, терминалы")
                }
                Chip {
                    objectName: "contextChip"
                    visible: App.chatProjects.length > 0 && App.currentId !== ""
                    iconName: "layers"
                    menu: false
                    text: App.contextTokens > 0 ? qsTr("Контекст ≈ %1").arg(App.contextTokens) : qsTr("Контекст")
                    onClicked: composer.contextRequested()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: qsTr("Что хаб сообщает модели о проектах: слои, размер, закреплённые файлы")
                }
            }
            Row {
                id: sendRow
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 1
                spacing: 6
                IconButton {
                    objectName: "attachButton"
                    implicitWidth: 32
                    implicitHeight: 32
                    iconName: "clip"
                    tip: qsTr("Приложить файлы или картинки — их можно и перетащить в поле, и вставить Ctrl+V")
                    onClicked: fileDialog.open()
                }
                AbstractButton {
                    id: send
                    // stops the answer - unless the model asked and the input holds the user's answer to it, or the
                    // goal works and the input holds a word for it
                    readonly property bool stops: App.busyNow && area.text.trim() === "" || App.busyNow
                                                  && !App.chatQuestion.id && !Goal.steering
                    width: 32
                    height: 32
                    hoverEnabled: true
                    enabled: send.stops || area.text.trim() !== "" || App.attachments.length > 0
                    onClicked: composer.submit()
                    background: Rectangle {
                        radius: 6
                        color: send.stops ? "transparent"
                               : (send.enabled ? (send.hovered ? Theme.p.accent_hover : Theme.p.accent) : Theme.p.item_hover)
                        border.color: send.stops ? Theme.p.error : "transparent"
                    }
                    contentItem: Item {
                        Icon {
                            anchors.centerIn: parent
                            name: send.stops ? "stop" : "send"
                            tint: send.stops ? Theme.p.error : (send.enabled ? Theme.p.on_accent : Theme.p.faint)
                        }
                    }
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: send.stops ? qsTr("Остановить ответ") : Goal.steering ? qsTr("Передать уточнение цели (Enter)")
                                                                                  : qsTr("Отправить (Enter)")
                }
            }
        }
    }
}
