import QtQuick
import QtQuick.Controls.Basic

// Settings → «Язык и вид»: the window's language (it opens again in it, 10.5) and its theme.
Sheet {
    id: sheet
    objectName: "settingsLook"
    title: qsTr("Язык и вид")
    iconName: "sun"

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
}
