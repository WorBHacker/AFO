import QtQuick
import QtQuick.Controls.Basic

// A question the controller asks (App.question): a title, the text, cancel / confirm. Answers go to App.answer().
Popup {
    id: dialog
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 460
    modal: true
    padding: 20
    closePolicy: Popup.CloseOnEscape
    onClosed: if (App.question.title) App.answer(false)

    Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }

    Connections {
        target: App
        function onQuestionChanged() {
            if (App.question.title) dialog.open()
            else if (dialog.opened) dialog.close()
        }
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
            text: App.question.title || ""
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Text {
            width: dialog.availableWidth
            text: App.question.text || ""
            wrapMode: Text.Wrap
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            lineHeight: 1.35
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            AbstractButton {
                id: cancel
                width: cancelText.implicitWidth + 28
                height: 32
                hoverEnabled: true
                onClicked: App.answer(false)
                background: Rectangle {
                    radius: 6
                    color: cancel.hovered ? Theme.p.item_hover : "transparent"
                    border.color: Theme.p.hairline_strong
                }
                contentItem: Text {
                    id: cancelText
                    text: "Отмена"
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            AbstractButton {
                id: confirm
                width: confirmText.implicitWidth + 28
                height: 32
                hoverEnabled: true
                onClicked: App.answer(true)
                background: Rectangle { radius: 6; color: confirm.hovered ? Theme.p.accent_hover : Theme.p.accent }
                contentItem: Text {
                    id: confirmText
                    text: App.question.confirm || "Да"
                    color: Theme.p.on_accent
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
}
