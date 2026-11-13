import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// Settings → «Запуск»: the hub at Windows' sign-in, llama-swap started by the hub, and where llama-swap's config and
// its start are (10.3): chosen here, the running one's (found), or AFO's default.
Sheet {
    id: sheet
    objectName: "settingsStartup"
    title: qsTr("Запуск")
    iconName: "power"

    Column {
        id: startup
        // no llama-swap anywhere yet - none chosen, none running, AFO's own engine not installed: one calm line
        readonly property bool engineMissing: App.autostart.llama_swap_config_source === "default"
            && App.autostart.llama_swap_config_found === false && App.autostart.llama_swap_script_source === "default"
            && App.autostart.llama_swap_found === false
        width: parent.width
        spacing: 6
        component Switch: AbstractButton {
            id: toggle
            checkable: true
            implicitHeight: 30
            implicitWidth: label.implicitWidth + 40
            hoverEnabled: true
            background: Rectangle {
                radius: 6
                color: toggle.checked ? Theme.p.item_selected : (toggle.hovered ? Theme.p.item_hover : "transparent")
                border.color: toggle.checked ? Theme.p.accent : Theme.p.hairline_strong
            }
            contentItem: Item {
                Icon {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    name: toggle.checked ? "check" : "close"
                    size: 13
                    tint: toggle.checked ? Theme.p.accent : Theme.p.faint
                }
                Text {
                    id: label
                    x: 26
                    anchors.verticalCenter: parent.verticalCenter
                    text: toggle.text
                    color: toggle.checked ? Theme.p.text : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
            }
        }
        Switch {
            objectName: "autostartLogin"
            text: qsTr("Запускать AFO при входе в Windows — сразу в трей")
            checked: !!App.autostart.login
            onToggled: App.setAutostart("login", checked)
        }
        Switch {
            objectName: "autostartLlamaSwap"
            text: qsTr("Хаб сам запускает llama-swap, если тот не отвечает")
            checked: App.autostart.llama_swap !== false
            onToggled: App.setAutostart("llama_swap", checked)
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            visible: App.autostart.llama_swap_script !== undefined && !App.autostart.llama_swap_found
                     && !startup.engineMissing
            text: qsTr("Запуск llama-swap не найден: %1").arg(App.autostart.llama_swap_script || "")
            color: Theme.p.warn
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Text {
            objectName: "engineMissing"
            visible: startup.engineMissing
            width: parent.width
            wrapMode: Text.Wrap
            text: qsTr("Движок локальных моделей ещё не установлен: его скачает и запустит мастер первого запуска. Если llama-swap у вас уже есть — запустите его или укажите его конфиг и программу ниже.")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        PlainButton {
            visible: startup.engineMissing
            iconName: "sparkle"
            text: qsTr("Мастер первого запуска…")
            onClicked: { sheet.close(); App.openUi("setup") }
        }
        Repeater {
            model: [
                { id: "llama_swap_config", name: qsTr("Конфиг llama-swap"), path: App.autostart.llama_swap_config || "",
                  found: App.autostart.llama_swap_config_found, source: App.autostart.llama_swap_config_source || "" },
                { id: "llama_swap_start", name: qsTr("Запуск llama-swap"), path: App.autostart.llama_swap_script || "",
                  found: App.autostart.llama_swap_found, source: App.autostart.llama_swap_script_source || "" }
            ]
            delegate: Column {
                id: place
                required property var modelData
                width: parent ? parent.width : 400
                spacing: 4
                Text {
                    width: parent.width
                    wrapMode: Text.WrapAnywhere
                    text: place.modelData.name + ": " + (place.modelData.path || "—")
                          + (place.modelData.source === "chosen" ? qsTr(" (выбран вами)")
                             : place.modelData.source === "env" ? qsTr(" (задан переменной окружения)")
                             : place.modelData.source === "found" ? qsTr(" (у запущенного llama-swap)") : "")
                          + (place.modelData.found === false && !startup.engineMissing ? qsTr(" — файла нет") : "")
                    color: place.modelData.found === false && !startup.engineMissing ? Theme.p.warn : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Row {
                    spacing: 6
                    PlainButton {
                        objectName: "choose_" + place.modelData.id
                        iconName: "folder"
                        text: qsTr("Выбрать…")
                        onClicked: { pathDialog.what = place.modelData.id; pathDialog.open() }
                    }
                    PlainButton {
                        visible: place.modelData.source === "chosen"
                        text: qsTr("По умолчанию")
                        onClicked: App.chooseLlamaSwap(place.modelData.id, "")
                    }
                }
            }
        }
        FileDialog {
            id: pathDialog
            property string what
            title: what === "llama_swap_config" ? qsTr("Конфиг llama-swap") : qsTr("Программа или скрипт запуска llama-swap")
            fileMode: FileDialog.OpenFile
            nameFilters: what === "llama_swap_config" ? [qsTr("Конфиг llama-swap (*.yaml *.yml)")]
                                                       : [qsTr("llama-swap или скрипт (*.exe *.cmd *.bat)")]
            onAccepted: App.chooseLlamaSwap(what, selectedFile.toString())
        }
    }
}
