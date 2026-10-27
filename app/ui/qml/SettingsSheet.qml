import QtQuick
import QtQuick.Controls.Basic

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
