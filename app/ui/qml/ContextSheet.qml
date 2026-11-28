import QtQuick
import QtQuick.Controls.Basic

// The context inspector (D22): what the hub gives the model about this chat's projects - each block with its size
// and text - and what the next messages will carry: the map and the live state on or off, pinned files.
Sheet {
    id: sheet
    objectName: "contextSheet"
    title: qsTr("Контекст чата")
    iconName: "layers"
    width: Math.min(620, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: App.loadContext()

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        lineHeight: 1.3
        text: qsTr("Что хаб сообщает модели о проектах этого чата. Изменения действуют со следующего сообщения: отправленное остаётся в беседе как было — так модель и её кэш видят одно и то же начало.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Text {
        text: App.contextTokens > 0 ? qsTr("В беседе ≈ %1 токенов контекста").arg(App.contextTokens) : qsTr("Контекста пока нет: он появится с первым сообщением")
        color: Theme.p.text
        font.family: Theme.uiFont
        font.pixelSize: 13
        font.weight: Font.DemiBold
    }

    // a project on or off the map: a tick, its name, what it is to the chat
    component MapTick: AbstractButton {
        id: tick
        property bool on: false
        property string note: ""
        width: parent ? parent.width : implicitWidth
        implicitHeight: 28
        hoverEnabled: true
        background: Rectangle { radius: 4; color: tick.hovered ? Theme.p.item_hover : "transparent" }
        contentItem: Item {
            Rectangle {
                id: box
                x: 4
                width: 16
                height: 16
                radius: 4
                anchors.verticalCenter: parent.verticalCenter
                color: tick.on ? Theme.p.accent : "transparent"
                border.color: tick.on ? Theme.p.accent : Theme.p.hairline_strong
                Icon { anchors.centerIn: parent; visible: tick.on; name: "check"; size: 12; tint: Theme.p.on_accent }
            }
            Text {
                id: tickName
                x: 30
                width: Math.max(0, Math.min(implicitWidth, parent.width - x - tickNote.implicitWidth - 12))
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                text: tick.text
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            Text {
                id: tickNote
                x: tickName.x + tickName.width + 8
                anchors.verticalCenter: parent.verticalCenter
                text: tick.note
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }

    SectionTitle { text: qsTr("Что идёт со следующими сообщениями") }
    Row {
        spacing: 8
        OnOff {
            objectName: "contextMap"
            text: qsTr("Карта проектов")
            checked: !!App.contextSettings.map
            onToggled: App.setContextLayer("map", checked)
        }
        OnOff {
            objectName: "contextLive"
            text: qsTr("Что происходит сейчас")
            checked: !!App.contextSettings.live
            onToggled: App.setContextLayer("live", checked)
        }
    }
    // which projects the map tells the model of («Стабилизация», portion 5: not every project of AFO)
    Column {
        objectName: "contextMapProjects"
        visible: !!App.contextSettings.map && App.contextMapRows.length > 0
        width: parent.width
        spacing: 2
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            lineHeight: 1.3
            text: App.contextSettings.map_chosen ? qsTr("На карте — проекты, которые вы отметили.")
                  : qsTr("На карте — проекты этого чата и связанные с ними. Отметьте другие, если они касаются чата.")
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Repeater {
            model: App.contextMapRows
            delegate: MapTick {
                required property var modelData
                objectName: "mapProject_" + modelData.name
                on: modelData.on
                text: modelData.name
                note: modelData.note
                onClicked: App.toggleMapProject(modelData.id)
            }
        }
        PlainButton {
            objectName: "mapDefault"
            visible: !!App.contextSettings.map_chosen
            text: qsTr("Как по умолчанию")
            onClicked: App.resetMapProjects()
        }
    }

    SectionTitle { text: qsTr("Права модели в этом чате") }
    Segmented {
        objectName: "chatMode"
        width: parent.width
        options: [{ id: "read_only", name: qsTr("Только чтение") }, { id: "ask", name: qsTr("Спрашивать") },
                  { id: "sandbox", name: qsTr("Песочница") }, { id: "trusted", name: qsTr("Доверенный") }]
        value: App.contextSettings.mode || "ask"
        onPicked: (id) => App.setChatMode(id)
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: App.contextSettings.mode === "read_only" ? qsTr("Модель только читает: правки файлов она предложит словами.")
              : App.contextSettings.mode === "trusted" ? qsTr("Модель правит файлы и запускает команды в прикреплённых проектах без вопроса. Удаление и необратимое не делает, пока вы не разрешите. Каждую правку можно откатить в обзоре проекта.")
              : App.contextSettings.mode === "sandbox" ? qsTr("Модель правит без вопроса, но не сам проект, а его копию (git worktree от последнего коммита, своя ветка). Потом вы смотрите изменения и применяете их к проекту или отбрасываете. Нужен git-проект.")
              : qsTr("Каждую правку файла модель показывает карточкой над чатом с diff и ждёт ответа.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    SectionTitle { text: qsTr("Инструкции этого чата") }
    Rectangle {
        width: parent.width
        height: 96
        radius: 6
        color: Theme.p.card
        border.color: chatInstructions.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: chatInstructions
                objectName: "chatInstructions"
                placeholderText: qsTr("Только для этого чата — после общего промпта и промпта модели. Например: «пиши тесты на pytest»")
                placeholderTextColor: Theme.p.faint
                text: App.contextSettings.instructions || ""
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                selectionColor: Theme.p.selection
                font.family: Theme.uiFont
                font.pixelSize: 13
                padding: 10
                background: null
                onEditingFinished: App.setChatInstructions(text)
            }
        }
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Ответ в этом чате")
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    Segmented {
        objectName: "chatPreset"
        width: parent.width
        options: [{ id: "", name: qsTr("как у модели") }, { id: "precise", name: qsTr("Точно") }, { id: "balanced", name: qsTr("Баланс") },
                  { id: "creative", name: qsTr("Творчески") }]
        value: App.contextSettings.preset || ""
        onPicked: (id) => App.setChatPreset(id)
    }

    SectionTitle { text: qsTr("Закреплённые файлы") }
    Text {
        visible: !App.contextSettings.pinned || App.contextSettings.pinned.length === 0
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Нет. Закрепить файл — кнопка со скрепкой в просмотре файла проекта: его начало (до 200 строк) будет в контексте, и снова — когда файл изменится.")
        color: Theme.p.faint
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Repeater {
        model: App.contextSettings.pinned || []
        delegate: Item {
            id: pin
            required property var modelData
            required property int index
            width: parent ? parent.width : 0
            height: 30
            Icon { anchors.verticalCenter: parent.verticalCenter; name: "pin"; size: 14; tint: Theme.p.muted }
            Text {
                x: 22
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, parent.width - x - 36)
                elide: Text.ElideMiddle
                text: pin.modelData.name
                color: Theme.p.text_soft
                font.family: Theme.monoFont
                font.pixelSize: 12
            }
            IconButton {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                implicitWidth: 24
                implicitHeight: 24
                iconName: "close"
                tip: qsTr("Открепить")
                onClicked: App.unpinFile(pin.index)
            }
        }
    }

    SectionTitle { text: qsTr("Что модель уже получила") }
    Repeater {
        model: App.contextBlocks
        delegate: Column {
            id: block
            required property var modelData
            required property int index
            width: parent ? parent.width : 0
            spacing: 4
            AbstractButton {
                id: head
                width: parent.width
                height: 32
                hoverEnabled: true
                onClicked: App.toggleContextBlock(block.index)
                background: Rectangle { radius: 6; color: head.hovered ? Theme.p.item_hover : "transparent" }
                contentItem: Item {
                    Icon {
                        x: 6
                        anchors.verticalCenter: parent.verticalCenter
                        name: block.modelData.open ? "chevron-down" : "chevron-right"
                        size: 13
                        tint: Theme.p.faint
                    }
                    Text {
                        x: 26
                        width: Math.max(0, Math.min(implicitWidth, parent.width - x - 130))
                        elide: Text.ElideRight
                        anchors.verticalCenter: parent.verticalCenter
                        text: block.modelData.label + (block.modelData.local ? qsTr("  · только локальным моделям") : "")
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("≈ %1 ток.").arg(block.modelData.tokens) + (block.modelData.sent > 1 ? " · ×" + block.modelData.sent : "")
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                }
            }
            Rectangle {
                visible: !!block.modelData.open
                width: parent.width
                height: Math.min(320, text.implicitHeight + 16)
                radius: 6
                color: Theme.p.code_bg
                border.color: Theme.p.hairline
                Flickable {
                    id: scroller
                    anchors.fill: parent
                    anchors.margins: 8
                    contentWidth: text.width
                    contentHeight: text.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ThinScrollBar {}
                    ScrollBar.horizontal: ThinScrollBar {}
                    TextEdit {
                        id: text
                        width: Math.max(implicitWidth, scroller.width)
                        readOnly: true
                        selectByMouse: true
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.NoWrap
                        text: block.modelData.text
                        color: Theme.p.text_soft
                        selectionColor: Theme.p.selection
                        font.family: Theme.monoFont
                        font.pixelSize: 12
                    }
                }
            }
        }
    }
}
