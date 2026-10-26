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
    implicitHeight: card.height + 24

    function focusInput() { area.forceActiveFocus() }
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

    Rectangle {
        id: card
        x: (parent.width - composer.column) / 2
        y: 4
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
                wrapMode: TextArea.Wrap
                placeholderText: App.busyNow ? "Пока модель отвечает, можно набирать следующий вопрос…"
                                             : "Спросите что-нибудь…  Enter — отправить, Shift+Enter — новая строка"
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
                Keys.onPressed: (event) => {
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
            height: 34
            Row {
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
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
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
