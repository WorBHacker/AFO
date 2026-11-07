import QtQuick
import QtQuick.Controls.Basic

// Settings → «Хаб»: its address and data, its own actions, how long what it keeps for undoing stays (D30).
Sheet {
    id: sheet
    objectName: "settingsHub"
    title: qsTr("Хаб")
    iconName: "cpu"

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
    SectionTitle { text: qsTr("Хранение") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Хранение: копии файлов — до и после правок моделей и все версии файлов проектов (для отката и «Истории файла»), снимки экрана агента, журналы терминалов и картинки из «Нарисовать». Старше срока или сверх предела — удаляются, сначала самые старые; разговоры, задачи и расходы хранятся всегда.")
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
}
