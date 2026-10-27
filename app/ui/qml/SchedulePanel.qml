import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// The new task's «Когда» (9.7, Q23): now, by time (every day, on days of the week, every N hours), or on an event (a
// new file in a folder, a message to the bot). A schedule may control the computer only if allowed here.
Column {
    id: panel
    objectName: "schedulePanel"
    spacing: 8
    readonly property var d: Schedules.draft

    component Label: Text {
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Hint: Text {
        width: panel.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Label { text: "Когда" }
    Segmented {
        objectName: "scheduleWhen"
        options: Schedules.whenOptions
        value: panel.d.when
        onPicked: (id) => Schedules.setField("when", id)
    }

    // ---------- by time ----------
    Column {
        visible: panel.d.when === "time"
        width: parent.width
        spacing: 8
        Segmented {
            objectName: "scheduleEvery"
            options: Schedules.everyOptions
            value: panel.d.every
            onPicked: (id) => Schedules.setField("every", id)
        }
        Flow {
            visible: panel.d.every === "weekdays"
            spacing: 4
            Repeater {
                model: Schedules.dayNames
                delegate: Chip {
                    required property var modelData
                    required property int index
                    menu: false
                    text: modelData
                    active: panel.d.dayFlags[index]
                    onClicked: Schedules.toggleDay(index + 1)
                }
            }
        }
        Row {
            spacing: 8
            Label { text: panel.d.every === "hours" ? "Каждые, ч" : "В"; anchors.verticalCenter: parent.verticalCenter }
            Field {
                objectName: panel.d.every === "hours" ? "scheduleHours" : "scheduleAt"
                width: 90
                text: panel.d.every === "hours" ? panel.d.hours : panel.d.at
                placeholderText: panel.d.every === "hours" ? "6" : "09:00"
                onEditingFinished: Schedules.setField(panel.d.every === "hours" ? "hours" : "at", text)
            }
        }
    }

    // ---------- on an event ----------
    Column {
        visible: panel.d.when === "event"
        width: parent.width
        spacing: 8
        Segmented {
            objectName: "scheduleEvent"
            options: Schedules.eventOptions
            value: panel.d.event
            onPicked: (id) => Schedules.setField("event", id)
        }
        Row {
            visible: panel.d.event === "file"
            width: parent.width
            spacing: 8
            Field {
                objectName: "scheduleFolder"
                width: parent.width - pattern.width - choose.width - 16
                text: panel.d.folder
                placeholderText: "Папка, например D:\\Входящие"
                onEditingFinished: Schedules.setField("folder", text)
            }
            Field {
                id: pattern
                objectName: "schedulePattern"
                width: 90
                text: panel.d.pattern
                placeholderText: "*.pdf"
                onEditingFinished: Schedules.setField("pattern", text)
            }
            PlainButton { id: choose; iconName: "folder"; text: "Выбрать…"; onClicked: folderDialog.open() }
        }
        Field {
            visible: panel.d.event === "telegram"
            objectName: "scheduleContains"
            width: parent.width
            text: panel.d.contains
            placeholderText: "Слова в сообщении (пусто — любое сообщение боту)"
            onEditingFinished: Schedules.setField("contains", text)
        }
        Hint {
            text: panel.d.event === "file"
                  ? "Путь нового файла агент получит как данные. Файлы, появившиеся в тихие часы, он разберёт утром."
                  : "Сообщение вашему Telegram-боту (из привязанного чата) запустит задачу; текст агент получит как данные."
        }
    }

    Row {
        visible: panel.d.when !== "now"
        spacing: 10
        Label { text: "Управлять компьютером"; anchors.verticalCenter: parent.verticalCenter }
        Segmented {
            objectName: "scheduleComputer"
            options: [{ id: "no", name: "нельзя" }, { id: "yes", name: "можно" }]
            value: panel.d.computer ? "yes" : "no"
            onPicked: (id) => Schedules.setComputer(id === "yes")
        }
    }
    Hint {
        visible: panel.d.when !== "now"
        text: Schedules.quietText + ". Подтверждения задача спросит как обычно — карточкой и в Telegram."
    }
    Text {
        objectName: "scheduleError"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Schedules.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    FolderDialog {
        id: folderDialog
        title: "Папка, в которой ждать новые файлы"
        onAccepted: Schedules.chooseFolder(selectedFolder.toString())
    }
}
