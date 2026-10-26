import QtQuick
import QtQuick.Controls.Basic

// Settings: the theme, the Anthropic key (D13), the hub's own actions, the version.
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

    SectionTitle { text: "Ключ Anthropic (модели Claude)" }
    Row {
        spacing: 8
        Rectangle {
            width: 7; height: 7; radius: 3.5
            color: App.keySet ? Theme.p.ok : Theme.p.faint
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: App.keySet ? "Задан · хранится в диспетчере учётных данных Windows"
                             : "Не задан — модели Claude недоступны"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
    Row {
        width: parent.width
        spacing: 8
        Field {
            id: key
            width: parent.width - save.width - 8
            echoMode: TextInput.Password
            placeholderText: App.keySet ? "Новый ключ, чтобы заменить" : "sk-ant-…"
            onAccepted: save.clicked()
        }
        PlainButton {
            id: save
            kind: "primary"
            text: "Сохранить"
            enabled: key.text.trim() !== ""
            onClicked: { App.setAnthropicKey(key.text); key.text = "" }
        }
    }
    PlainButton {
        visible: App.keySet
        kind: "danger"
        iconName: "trash"
        text: "Удалить ключ"
        onClicked: App.deleteAnthropicKey()
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
