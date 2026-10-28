import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// Settings: the theme, the model advice's priority, the connections (D13), the hub's own actions, the version.
Sheet {
    id: sheet
    objectName: "settingsSheet"
    title: "Настройки"
    iconName: "sliders"

    SectionTitle { text: "Тема" }
    Segmented {
        width: parent.width
        options: [
            { id: "system", name: "Как в системе" },
            { id: "light", name: "Светлая" },
            { id: "dark", name: "Тёмная" }
        ]
        value: Theme.mode
        onPicked: (id) => App.setTheme(id)
    }

    SectionTitle { text: "Подбор модели: что важнее" }
    Segmented {
        objectName: "priority"
        width: parent.width
        options: [
            { id: "balanced", name: "Баланс" },
            { id: "quality", name: "Качество" },
            { id: "speed", name: "Скорость" },
            { id: "price", name: "Цена" }
        ]
        value: App.priority
        onPicked: (id) => App.setPriority(id)
    }

    SectionTitle { text: "Модели" }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Для каждой модели: показывать ли в выборе, своё имя, параметры ответа, системный промпт, цены."
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openModelSettings"
        iconName: "sliders"
        text: "Настройка моделей…"
        onClicked: { sheet.close(); App.openUi("model-settings") }
    }
    PlainButton {
        objectName: "openInstallModels"
        iconName: "download"
        text: "Установить локальную модель…"
        onClicked: { sheet.close(); App.openUi("install-models") }
    }

    SectionTitle { text: "Подключения" }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Ключи API (Anthropic, Gemini, поиск) и токен Telegram-бота — под паролем подключений. "
              + (App.keySet ? "Ключ Anthropic задан." : "Ключ Anthropic не задан — модели Claude по ключу недоступны.")
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openConnections"
        iconName: "key"
        text: "Открыть подключения…"
        onClicked: { sheet.close(); App.openUi("connections") }
    }

    SectionTitle { text: "Автономный агент" }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Программы, которыми агент может управлять: хаб находит их сам и спрашивает о каждой новой."
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openPrograms"
        iconName: "shield"
        text: "Программы для агента…"
        onClicked: { sheet.close(); App.openUi("programs") }
    }
    PlainButton {
        objectName: "openAgentAccess"
        iconName: "folder"
        text: "Папки и сайты для агента…"
        onClicked: { sheet.close(); App.openUi("agent-access") }
    }
    Row {
        objectName: "quietHours"
        spacing: 8
        Text {
            text: "Тихие часы: с"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Field {
            id: quietFrom
            objectName: "quietFrom"
            width: 70
            text: Schedules.quiet.from || "23:00"
            onEditingFinished: Schedules.setQuiet(Schedules.quiet.enabled !== false, quietFrom.text, quietTo.text)
        }
        Text {
            text: "до"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Field {
            id: quietTo
            objectName: "quietTo"
            width: 70
            text: Schedules.quiet.to || "08:00"
            onEditingFinished: Schedules.setQuiet(Schedules.quiet.enabled !== false, quietFrom.text, quietTo.text)
        }
        Segmented {
            objectName: "quietEnabled"
            options: [{ id: "on", name: "вкл" }, { id: "off", name: "выкл" }]
            value: Schedules.quiet.enabled === false ? "off" : "on"
            onPicked: (id) => Schedules.setQuiet(id === "on", quietFrom.text, quietTo.text)
        }
    }  // a time is saved when its field is left (the row has no room for a button of its own)
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: Schedules.quietText + "."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    SectionTitle { text: "Запуск" }
    Column {
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
            text: "Запускать хаб при входе в Windows"
            checked: !!App.autostart.login
            onToggled: App.setAutostart("login", checked)
        }
        Switch {
            objectName: "autostartLlamaSwap"
            text: "Хаб сам запускает llama-swap, если тот не отвечает"
            checked: App.autostart.llama_swap !== false
            onToggled: App.setAutostart("llama_swap", checked)
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            visible: App.autostart.llama_swap_script !== undefined && !App.autostart.llama_swap_found
            text: "Запуск llama-swap не найден: " + (App.autostart.llama_swap_script || "")
            color: Theme.p.warn
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        // where llama-swap is (10.3): chosen here, or LocalChat's default
        Repeater {
            model: [
                { id: "llama_swap_config", name: "Конфиг llama-swap", path: App.autostart.llama_swap_config || "",
                  found: App.autostart.llama_swap_config_found, source: App.autostart.llama_swap_config_source || "" },
                { id: "llama_swap_start", name: "Запуск llama-swap", path: App.autostart.llama_swap_script || "",
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
                          + (place.modelData.source === "chosen" ? " (выбран вами)"
                             : place.modelData.source === "env" ? " (задан переменной окружения)" : "")
                          + (place.modelData.found === false ? " — файла нет" : "")
                    color: place.modelData.found === false ? Theme.p.warn : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Row {
                    spacing: 6
                    PlainButton {
                        objectName: "choose_" + place.modelData.id
                        iconName: "folder"
                        text: "Выбрать…"
                        onClicked: { pathDialog.what = place.modelData.id; pathDialog.open() }
                    }
                    PlainButton {
                        visible: place.modelData.source === "chosen"
                        text: "По умолчанию"
                        onClicked: App.chooseLlamaSwap(place.modelData.id, "")
                    }
                }
            }
        }
        FileDialog {
            id: pathDialog
            property string what
            title: what === "llama_swap_config" ? "Конфиг llama-swap" : "Программа или скрипт запуска llama-swap"
            fileMode: FileDialog.OpenFile
            nameFilters: what === "llama_swap_config" ? ["Конфиг llama-swap (*.yaml *.yml)"]
                                                       : ["llama-swap или скрипт (*.exe *.cmd *.bat)"]
            onAccepted: App.chooseLlamaSwap(what, selectedFile.toString())
        }
    }

    SectionTitle { text: "Хаб" }
    Text {
        width: parent.width
        text: "Адрес 127.0.0.1" + App.hubPort + " · данные в " + App.dataDir
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Flow {
        width: parent.width
        spacing: 8
        PlainButton { iconName: "refresh"; text: "Обновить список моделей"; onClicked: App.syncModels() }
        PlainButton { iconName: "folder"; text: "Открыть папку данных"; onClicked: App.openFolder(App.dataDir) }
        PlainButton { iconName: "cpu"; text: "Выгрузить локальную модель"; onClicked: App.unloadLocal() }
        PlainButton { kind: "danger"; iconName: "power"; text: "Остановить хаб и выйти"; onClicked: App.stopHub() }
    }

    SectionTitle { text: "О программе" }
    Text {
        width: parent.width
        text: "LocalChat " + App.version + " · окно рисует процессор, видеопамять остаётся моделям"
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
