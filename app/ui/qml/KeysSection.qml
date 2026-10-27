import QtQuick
import QtQuick.Controls.Basic

// The user's own API keys for the agent's web requests (9.4b), in Connections: each tied to its site and header. The
// hub adds a key to the requests to its site only; the model never sees it.
Column {
    id: section
    objectName: "keysSection"
    spacing: 8
    readonly property bool unlocked: Services.state === "open"

    component Note: Text {
        width: parent.width
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
    SectionTitle { text: "Ключи API для агента" }
    Note {
        text: "Для сервисов, к которым агент обращается сам (погода, задачи, CRM): ключ уходит только на свой сайт "
              + "в указанном заголовке, модель его не видит, а сайт, который вернул бы ключ в ответе, его не покажет."
    }
    Repeater {
        model: AgentKeys.items
        delegate: Row {
            required property var modelData
            width: section.width
            spacing: 8
            Rectangle {
                width: 7; height: 7; radius: 3.5
                color: modelData.set ? Theme.p.ok : Theme.p.faint
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                width: parent.width - 120
                elide: Text.ElideRight
                text: modelData.name + " · " + modelData.host + " · " + modelData.note
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
                anchors.verticalCenter: parent.verticalCenter
            }
            PlainButton {
                visible: section.unlocked
                kind: "danger"
                iconName: "trash"
                text: "Удалить"
                onClicked: AgentKeys.remove(modelData.name)
            }
        }
    }
    Flow {
        visible: section.unlocked
        width: parent.width
        spacing: 6
        Field { id: name; objectName: "keyName"; width: 120; placeholderText: "Имя: weather" }
        Field { id: host; objectName: "keyHost"; width: 200; placeholderText: "Сайт: api.weather.example" }
        Field { id: header; width: 130; placeholderText: "Authorization" }
        Field { id: prefix; width: 80; text: "Bearer " }
        Field { id: value; objectName: "keyValue"; width: 220; echoMode: TextInput.Password; placeholderText: "Ключ" }
        PlainButton {
            objectName: "keySave"
            kind: "primary"
            text: "Сохранить ключ"
            enabled: name.text.trim() !== "" && host.text.trim() !== ""
            onClicked: { AgentKeys.save(name.text, host.text, header.text, prefix.text, value.text); value.text = "" }
        }
    }
    Text {
        visible: AgentKeys.error !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: AgentKeys.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
