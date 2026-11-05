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
