import QtQuick
import QtQuick.Controls.Basic

// The conversation: a centred reading column; follows new output while you are at the bottom.
ListView {
    id: list
    readonly property int column: Math.min(780, width - 48)
    property bool follow: true
    property bool autoScroll: false

    model: App.messageModel
    spacing: 28
    topMargin: 28
    bottomMargin: 24
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    flickDeceleration: 4000

    ScrollBar.vertical: ThinScrollBar {}

    delegate: Item {
        id: slot
        required property int index
        required property var model
        width: list.width
        height: loader.height
        Loader {
            id: loader
            x: (slot.width - list.column) / 2
            width: list.column
            // while the model resets, a delegate may outlive its row for a moment
            sourceComponent: !slot.model ? null
                             : slot.model.role === "user" ? userMessage
                             : slot.model.role === "error" ? errorMessage : assistantMessage
            onLoaded: {
                item.row = Qt.binding(() => slot.model)
                item.rowIndex = Qt.binding(() => slot.index)
            }
        }
    }
    Component { id: userMessage; UserMessage {} }
    Component { id: assistantMessage; AssistantMessage {} }
    Component { id: errorMessage; ErrorMessage {} }

    function toEnd() {
        autoScroll = true
        positionViewAtEnd()
        autoScroll = false
    }
    onContentYChanged: if (!autoScroll) follow = atYEnd || contentHeight <= height
    onContentHeightChanged: if (follow) Qt.callLater(toEnd)
    onCountChanged: if (follow) Qt.callLater(toEnd)
    Connections {
        target: App
        function onCurrentChanged() { list.follow = true }
    }

    Column {
        anchors.centerIn: parent
        visible: list.count === 0
        spacing: 10
        Icon { name: "chat"; size: 28; tint: Theme.p.faint; anchors.horizontalCenter: parent.horizontalCenter }
        Text {
            text: "Новый разговор"
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 18
            font.weight: Font.DemiBold
            anchors.horizontalCenter: parent.horizontalCenter
        }
        Text {
            text: "Выберите модель внизу и спросите что-нибудь"
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.horizontalCenter: parent.horizontalCenter
        }
    }
}
