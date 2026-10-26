import QtQuick
import QtQuick.Controls.Basic

// The open project's settings: its name, a one-line description (the ecosystem map shows it to models, D22) and
// whether cloud models may see it (D10). Secret files and secrets in text are hidden from models either way.
Sheet {
    id: sheet
    objectName: "projectSheet"
    title: "Проект"
    iconName: "folder"
    onAboutToShow: {
        nameField.text = Projects.currentName
        descriptionField.text = Projects.currentDescription
        cloud.value = Projects.currentCloud ? "cloud" : "local"
    }

    Text {
        width: parent.width
        text: Projects.currentPath
        elide: Text.ElideMiddle
        color: Theme.p.faint
        font.family: Theme.monoFont
        font.pixelSize: 12
    }

    SectionTitle { text: "Название" }
    Field { id: nameField; width: parent.width }

    SectionTitle { text: "Что это за проект — одной строкой" }
    Field {
        id: descriptionField
        objectName: "projectDescription"
        width: parent.width
        placeholderText: "например: бэкенд магазина на FastAPI, REST на :8000"
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Эта строка попадает в карту экосистемы, которую видят модели, — по ней они понимают, о каком проекте речь."
        color: Theme.p.faint
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    SectionTitle { text: "Какие модели могут видеть проект" }
    Segmented {
        id: cloud
        objectName: "projectCloud"
        width: parent.width
        options: [{ id: "local", name: "Только локальные" }, { id: "cloud", name: "Локальные и облачные" }]
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: cloud.value === "cloud"
              ? "Код, файлы и состояние проекта могут уходить облачным моделям (Claude и другие), когда вы их выбираете."
              : "Облачные модели увидят только название проекта. Всё остальное — только локальным моделям на этом компьютере."
        color: Theme.p.muted
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "В любом случае модели не получают секретные файлы (.env, закрытые ключи, хранилища паролей — в дереве они с замком), а ключи и пароли в тексте заменяются на [REDACTED]."
        color: Theme.p.faint
        lineHeight: 1.3
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Row {
        spacing: 8
        topPadding: 8
        PlainButton {
            kind: "primary"
            text: "Сохранить"
            onClicked: {
                Projects.saveSettings(nameField.text, descriptionField.text, cloud.value === "cloud")
                sheet.close()
            }
        }
        PlainButton { text: "Отмена"; onClicked: sheet.close() }
    }
}
