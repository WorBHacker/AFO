import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// Settings: the theme, the model advice's priority, the connections (D13), the hub's own actions, all the settings
// into a file and back (D46, 13.10), the version.
Sheet {
    id: sheet
    objectName: "settingsSheet"
    title: qsTr("Настройки")
    iconName: "sliders"

    // both words, whatever the window's language: whoever cannot read one finds the other (10.5)
    SectionTitle { text: "Язык · Language" }  // i18n: keep
    Segmented {
        objectName: "language"
        width: parent.width
        options: App.languages
        value: App.language
        onPicked: (id) => App.setLanguage(id)
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Окно откроется заново на выбранном языке; разговоры и агенты в хабе продолжат работу.")
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    SectionTitle { text: qsTr("Тема") }
    Segmented {
        width: parent.width
        options: [
            { id: "system", name: qsTr("Как в системе") },
            { id: "light", name: qsTr("Светлая") },
            { id: "dark", name: qsTr("Тёмная") }
        ]
        value: Theme.mode
        onPicked: (id) => App.setTheme(id)
    }

    SectionTitle { text: qsTr("Подбор модели: что важнее") }
    Segmented {
        objectName: "priority"
        width: parent.width
        options: [
            { id: "balanced", name: qsTr("Баланс") },
            { id: "quality", name: qsTr("Качество") },
            { id: "speed", name: qsTr("Скорость") },
            { id: "price", name: qsTr("Цена") }
        ]
        value: App.priority
        onPicked: (id) => App.setPriority(id)
    }

    SectionTitle { text: qsTr("Модели") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Для каждой модели: показывать ли в выборе, своё имя, параметры ответа, системный промпт, цены.")
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openModelSettings"
        iconName: "sliders"
        text: qsTr("Настройка моделей…")
        onClicked: { sheet.close(); App.openUi("model-settings") }
    }
    PlainButton {
        objectName: "openInstallModels"
        iconName: "download"
        text: qsTr("Установить локальную модель…")
        onClicked: { sheet.close(); App.openUi("install-models") }
    }
    PlainButton {
        objectName: "openWizard"
        iconName: "sparkle"
        text: qsTr("Мастер первого запуска…")
        onClicked: { sheet.close(); App.openUi("setup") }
    }

    SectionTitle { text: qsTr("Подключения") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: (App.keySet ? qsTr("Ключи API (Anthropic, Gemini, поиск) и токен Telegram-бота — под паролем подключений. Ключ Anthropic задан.")
                          : qsTr("Ключи API (Anthropic, Gemini, поиск) и токен Telegram-бота — под паролем подключений. Ключ Anthropic не задан — модели Claude по ключу недоступны."))
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openConnections"
        iconName: "key"
        text: qsTr("Открыть подключения…")
        onClicked: { sheet.close(); App.openUi("connections") }
    }

    SectionTitle { text: qsTr("Автономный агент") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Программы, которыми агент может управлять: хаб находит их сам и спрашивает о каждой новой.")
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    PlainButton {
        objectName: "openPrograms"
        iconName: "shield"
        text: qsTr("Программы для агента…")
        onClicked: { sheet.close(); App.openUi("programs") }
    }
    PlainButton {
        objectName: "openAgentAccess"
        iconName: "folder"
        text: qsTr("Папки и сайты для агента…")
        onClicked: { sheet.close(); App.openUi("agent-access") }
    }
    Row {
        objectName: "quietHours"
        spacing: 8
        Text {
            text: qsTr("Тихие часы: с")
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
            text: qsTr("до")
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
            options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }]
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

    SectionTitle { text: qsTr("Запуск") }
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
            text: qsTr("Запускать хаб при входе в Windows")
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
            text: qsTr("Запуск llama-swap не найден: %1").arg(App.autostart.llama_swap_script || "")
            color: Theme.p.warn
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        // where llama-swap is (10.3): chosen here, or AFO's default
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
                             : place.modelData.source === "env" ? qsTr(" (задан переменной окружения)") : "")
                          + (place.modelData.found === false ? qsTr(" — файла нет") : "")
                    color: place.modelData.found === false ? Theme.p.warn : Theme.p.muted
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

    SectionTitle { text: qsTr("Хаб") }
    Text {
        width: parent.width
        text: qsTr("Адрес 127.0.0.1%1 · данные в %2").arg(App.hubPort).arg(App.dataDir)
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Flow {
        width: parent.width
        spacing: 8
        PlainButton { iconName: "refresh"; text: qsTr("Обновить список моделей"); onClicked: App.syncModels() }
        PlainButton { iconName: "folder"; text: qsTr("Открыть папку данных"); onClicked: App.openFolder(App.dataDir) }
        PlainButton {
            objectName: "openCheck"
            iconName: "activity"
            text: qsTr("Проверить систему…")
            onClicked: { sheet.close(); App.openUi("check-system") }
        }
        PlainButton { iconName: "cpu"; text: qsTr("Выгрузить локальную модель"); onClicked: App.unloadLocal() }
        PlainButton { kind: "danger"; iconName: "power"; text: qsTr("Остановить хаб и выйти"); onClicked: App.stopHub() }
    }
    // D30: snapshots of changed files, the agent's screen snapshots, terminal logs - a term and a size per kind
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Хранение: копии файлов до и после правок моделей (для отката), снимки экрана агента и журналы терминалов. Старше срока или сверх предела — удаляются, сначала самые старые; разговоры, задачи и расходы хранятся всегда.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Flow {
        width: parent.width
        spacing: 8
        Text { text: qsTr("Хранить, дней"); color: Theme.p.text_soft; font.family: Theme.uiFont; font.pixelSize: 13; height: 34; verticalAlignment: Text.AlignVCenter }
        Field {
            id: keepDays
            objectName: "retentionDays"
            width: 72
            text: App.retention.days || ""
            onEditingFinished: App.setRetention(text, keepGb.text)
        }
        Text { text: qsTr("не больше, ГБ каждого вида"); color: Theme.p.text_soft; font.family: Theme.uiFont; font.pixelSize: 13; height: 34; verticalAlignment: Text.AlignVCenter }
        Field {
            id: keepGb
            objectName: "retentionGb"
            width: 72
            text: App.retention.maxGb || ""
            onEditingFinished: App.setRetention(keepDays.text, text)
        }
        PlainButton {
            objectName: "retentionPrune"
            iconName: "trash"
            text: qsTr("Очистить старое сейчас")
            onClicked: App.pruneKept()
        }
    }
    Text {
        objectName: "retentionUsage"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: App.retention.usage ? qsTr("Сейчас: %1").arg(App.retention.usage) : ""
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    SectionTitle { text: qsTr("Перенос настроек") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Все настройки одним файлом: модели, бюджеты, проекты и их правила, агенты, расписания, папки и сайты агента, тихие часы, подключения без ключей, MCP-серверы, тема окна. Ключей, паролей и разговоров в файле нет. Перед загрузкой видно, что изменится.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Flow {
        width: parent.width
        spacing: 8
        PlainButton {
            objectName: "exportSettings"
            iconName: "external"
            text: qsTr("Сохранить все настройки в файл…")
            onClicked: settingsOut.open()
        }
        PlainButton {
            objectName: "importSettings"
            iconName: "download"
            text: qsTr("Загрузить из файла…")
            onClicked: settingsIn.open()
        }
    }
    FileDialog {
        id: settingsOut
        title: qsTr("Сохранить все настройки")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [qsTr("Настройки AFO (*.json)")]
        onAccepted: Transfer.exportTo(selectedFile.toString())
    }
    FileDialog {
        id: settingsIn
        title: qsTr("Загрузить настройки")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Настройки AFO (*.json)")]
        onAccepted: if (Transfer.openFile(selectedFile.toString())) { sheet.close(); App.openUi("transfer") }
    }

    SectionTitle { text: qsTr("О программе") }
    Text {
        width: parent.width
        text: qsTr("AFO %1 · окно рисует процессор, видеопамять остаётся моделям").arg(App.version)
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    // AFO's own updates (10.4d): the newest release on GitHub, downloaded on a click, installed after a question
    Text {
        objectName: "releaseText"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Release.text
        color: Release.failed ? Theme.p.warn : Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Text {
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Release.note
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Rectangle {
        visible: Release.downloading
        width: parent.width
        height: 4
        radius: 2
        color: Theme.p.hairline_strong
        Rectangle { width: parent.width * Release.progress; height: parent.height; radius: 2; color: Theme.p.accent }
    }
    Flow {
        width: parent.width
        spacing: 8
        PlainButton {
            objectName: "releaseCheck"
            visible: !Release.busy
            iconName: "refresh"
            text: qsTr("Проверить обновления")
            onClicked: Release.check()
        }
        PlainButton {
            objectName: "releaseDownload"
            visible: Release.canDownload
            iconName: "download"
            text: qsTr("Скачать AFO %1").arg(Release.latest)
            onClicked: Release.download()
        }
        PlainButton {
            visible: Release.downloading
            text: qsTr("Остановить загрузку")
            onClicked: Release.cancel()
        }
        PlainButton {
            objectName: "releaseInstall"
            visible: Release.canInstall
            iconName: "sparkle"
            text: qsTr("Установить и перезапустить…")
            onClicked: Release.install()
        }
        PlainButton {
            visible: Release.notesUrl !== ""
            iconName: "external"
            text: qsTr("Что нового")
            onClicked: App.openLink(Release.notesUrl)
        }
    }
    Row {
        spacing: 8
        Text {
            text: qsTr("Проверять обновления сами")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Segmented {
            objectName: "releaseAuto"
            options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }]
            value: Release.auto ? "on" : "off"
            onPicked: (id) => Release.setAuto(id === "on")
        }
    }
}
