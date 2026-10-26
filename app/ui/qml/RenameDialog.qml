import QtQuick
import QtQuick.Controls.Basic

// Rename a chat: the current title selected, Enter saves, Esc cancels.
Popup {
    id: dialog
    objectName: "renameDialog"
    property string chatId

    function openFor(id) {
        chatId = id
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 420
    padding: 20
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
    onOpened: {
        title.text = App.chatTitle(chatId)
        title.selectAll()
        title.forceActiveFocus()
    }
    function save() {
        App.renameChat(chatId, title.text)
        close()
    }

    background: Rectangle {
        radius: 10
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow { corner: 10 }
    }
    contentItem: Column {
        spacing: 14
        Text {
            text: "Переименовать чат"
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Field {
            id: title
            width: dialog.availableWidth
            onAccepted: dialog.save()
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            PlainButton { text: "Отмена"; onClicked: dialog.close() }
            PlainButton { kind: "primary"; text: "Сохранить"; enabled: title.text.trim() !== ""; onClicked: dialog.save() }
        }
    }
}
