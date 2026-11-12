import QtQuick
import QtQuick.Controls.Basic

// Settings → «Модели»: what the model advice weighs most, and the way to each model's settings, to installing a
// local model and to the first-run wizard.
Sheet {
    id: sheet
    objectName: "settingsModels"
    title: qsTr("Модели")
    iconName: "layers"

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

    // 38.3: what a model may not count on of the computer's memory - the installer, the wizard and the search judge by
    // it; the hub's own figures until the user changes them
    onOpened: Installer.loadReserve()
    SectionTitle { text: qsTr("Запас памяти") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Сколько памяти локальная модель не займёт: её держат Windows и другие программы. Больше запас — модели предлагаются поменьше, зато не замедляются.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Flow {
        width: parent.width
        spacing: 8
        Text { text: qsTr("Видеопамять, ГБ"); color: Theme.p.text_soft; font.family: Theme.uiFont; font.pixelSize: 13; height: 34; verticalAlignment: Text.AlignVCenter }
        Field {
            id: reserveVram
            objectName: "reserveVram"
            width: 64
            text: Installer.reserve.vram || ""
            onEditingFinished: Installer.setReserve(text, reserveRam.text)
        }
        Text { text: qsTr("ОЗУ, ГБ"); color: Theme.p.text_soft; font.family: Theme.uiFont; font.pixelSize: 13; height: 34; verticalAlignment: Text.AlignVCenter }
        Field {
            id: reserveRam
            objectName: "reserveRam"
            width: 64
            text: Installer.reserve.ram || ""
            onEditingFinished: Installer.setReserve(reserveVram.text, text)
        }
        PlainButton {
            objectName: "reserveDefault"
            visible: !!Installer.reserve.vram && !Installer.reserve.isDefault
            text: qsTr("По умолчанию")
            onClicked: Installer.resetReserve()
        }
    }
    Text {
        width: parent.width
        visible: !!Installer.reserve.defaults
        text: Installer.reserve.defaults || ""
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
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
}
