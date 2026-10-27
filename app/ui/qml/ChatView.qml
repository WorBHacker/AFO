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
    // rows this far out of view stay laid out, so their heights are real: a list of estimates moves under the reader
    cacheBuffer: 20000
    boundsBehavior: Flickable.StopAtBounds
    flickDeceleration: 4000

    ScrollBar.vertical: ThinScrollBar {}

    delegate: Item {
        id: slot
        required property int index
        required property var model
        width: list.width
        height: loader.height
        // the last row grows while an answer streams: its end stays in view
        onHeightChanged: if (list.follow && index === list.count - 1) Qt.callLater(list.toEnd)
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

    // How far the last message's bottom is below the view (Infinity: that row is not laid out at all).
    function endGap() {
        const last = itemAtIndex(count - 1)
        return last ? last.y + last.height + bottomMargin - height - contentY : Infinity
    }
    // Keeps the last message's bottom in view by moving just the missing distance. It follows the last row's growth
    // and new rows, not contentHeight, and never asks positionViewAtEnd() when that row is laid out: the heights of
    // rows out of view are estimates, positionViewAtEnd() changed them, contentHeight changed, and it ran again -
    // forever, the window busy even with nothing streaming (a long chat with tool calls).
    function toEnd() {
        autoScroll = true
        const gap = endGap()
        if (gap === Infinity) {
            if (count > 0)
                positionViewAtEnd()
        } else if (gap > 1) {
            contentY += gap
        }
        autoScroll = false
    }
    // Only the reader scrolling up stops the following; being at the end again starts it. The list also moves
    // contentY itself (a new row, a corrected estimate): that never stops it.
    property real lastY: 0
    onContentYChanged: {
        if (!autoScroll) {
            if (endGap() <= 40)
                follow = true
            else if (contentY < lastY - 0.5)
                follow = false
        }
        lastY = contentY
    }
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
