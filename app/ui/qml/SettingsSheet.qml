import QtQuick
import QtQuick.Controls.Basic

// Settings: a button for each category, with how it stands now; each opens a sheet of its own, whose arrow comes
// back here (2026-11-05, the user's word: «сделай кнопку и отдельную окну для каждого категории»).
Sheet {
    id: sheet
    objectName: "settingsSheet"
    title: qsTr("Настройки")
    iconName: "sliders"

    readonly property bool engineMissing: App.autostart.llama_swap_config_source === "default"
        && App.autostart.llama_swap_config_found === false && App.autostart.llama_swap_script_source === "default"
        && App.autostart.llama_swap_found === false
    readonly property bool swapLost: (App.autostart.llama_swap_script !== undefined && !App.autostart.llama_swap_found)
        || App.autostart.llama_swap_config_found === false

    function themeName(mode) {
        return mode === "light" ? qsTr("Светлая") : mode === "dark" ? qsTr("Тёмная") : qsTr("Как в системе")
    }
    function priorityName(id) {
        return id === "quality" ? qsTr("Качество") : id === "speed" ? qsTr("Скорость")
             : id === "price" ? qsTr("Цена") : qsTr("Баланс")
    }

    // one category: its icon, its name, how it stands; «warn» - something there wants the person
    component Category: AbstractButton {
        id: category
        property string iconName
        property string summary
        property bool warn: false
        width: parent ? parent.width : 400
        implicitHeight: 58
        hoverEnabled: true
        background: Rectangle {
            radius: 8
            color: category.hovered ? Theme.p.item_hover : Theme.p.card
            border.color: Theme.p.hairline
        }
        contentItem: Item {
            Icon {
                id: mark
                x: 14
                anchors.verticalCenter: parent.verticalCenter
                name: category.iconName
                size: 18
                tint: category.warn ? Theme.p.warn : Theme.p.text_soft
            }
            Column {
                x: mark.x + mark.width + 14
                width: parent.width - x - 36
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Text {
                    width: parent.width
                    elide: Text.ElideRight
                    text: category.text
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    elide: Text.ElideRight
                    text: category.summary
                    color: category.warn ? Theme.p.warn : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
            Icon {
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                name: "chevron-right"
                size: 14
                tint: Theme.p.faint
            }
        }
    }

    Category {
        objectName: "settings_look"
        iconName: "sun"
        text: qsTr("Язык и вид")
        summary: ((App.languages.find(l => l.id === App.language) || {}).name || "") + " · " + sheet.themeName(Theme.mode)
        onClicked: { sheet.close(); App.openUi("settings-look") }
    }
    Category {
        objectName: "settings_models"
        iconName: "layers"
        text: qsTr("Модели")
        summary: qsTr("Подбор: %1 · настройка, установка, мастер").arg(sheet.priorityName(App.priority))
        onClicked: { sheet.close(); App.openUi("settings-models") }
    }
    Category {
        objectName: "settings_connections"
        iconName: "key"
        text: qsTr("Подключения")
        summary: App.keySet ? qsTr("Ключи API и токены под паролем · ключ Anthropic задан")
                            : qsTr("Ключи API и токены под паролем · ключ Anthropic не задан")
        onClicked: { sheet.close(); App.openUi("connections") }
    }
    Category {
        objectName: "settings_agent"
        iconName: "agent"
        text: qsTr("Автономный агент")
        summary: Schedules.quiet.enabled === false ? qsTr("Программы, папки и сайты · тихие часы выключены")
                 : qsTr("Программы, папки и сайты · тихие часы %1–%2").arg(Schedules.quiet.from || "23:00")
                                                                     .arg(Schedules.quiet.to || "08:00")
        onClicked: { sheet.close(); App.openUi("settings-agent") }
    }
    Category {
        objectName: "settings_startup"
        iconName: "power"
        text: qsTr("Запуск")
        warn: sheet.engineMissing || sheet.swapLost
        summary: sheet.engineMissing ? qsTr("Движок локальных моделей ещё не установлен")
                 : sheet.swapLost ? qsTr("llama-swap: файла нет — укажите, где он")
                 : App.autostart.login ? qsTr("AFO — при входе в Windows, в трей · llama-swap на месте")
                 : qsTr("Хаб — вместе с окном · llama-swap на месте")
        onClicked: { sheet.close(); App.openUi("settings-startup") }
    }
    Category {
        objectName: "settings_hub"
        iconName: "cpu"
        text: qsTr("Хаб")
        summary: qsTr("127.0.0.1%1 · проверка системы, хранение копий").arg(App.hubPort)
        onClicked: { sheet.close(); App.openUi("settings-hub") }
    }
    Category {
        objectName: "settings_transfer"
        iconName: "external"
        text: qsTr("Перенос настроек")
        summary: qsTr("Все настройки — в файл и обратно, без ключей")
        onClicked: { sheet.close(); App.openUi("settings-transfer") }
    }
    Category {
        objectName: "settings_about"
        iconName: "sparkle"
        text: qsTr("О программе")
        warn: Release.canDownload || Release.canInstall
        summary: Release.canDownload || Release.canInstall ? qsTr("Есть AFO %1 — у вас %2").arg(Release.latest).arg(App.version)
                                                           : qsTr("AFO %1 · обновления").arg(App.version)
        onClicked: { sheet.close(); App.openUi("settings-about") }
    }
}
