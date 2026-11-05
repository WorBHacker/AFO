import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// Settings → «Перенос настроек» (D46, 13.10): every setting into one file, and back - what changes is shown first.
Sheet {
    id: sheet
    objectName: "settingsTransfer"
    title: qsTr("Перенос настроек")
    iconName: "external"

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
}
