import QtQuick
import QtQuick.Controls.Basic

// The conversation: a centred reading column; follows new output while you are at the bottom.
ListView {
    id: list
    readonly property int column: Math.min(780, width - 48)
    property bool follow: true
    property bool autoScroll: false
    property real reserve: 0  // the approval card's room at the top: the first messages scroll out from under it

    model: App.messageModel
    spacing: 28
    topMargin: 28 + reserve
    // a conversation shorter than the view sits below the card; a longer one stays where it is
    onTopMarginChanged: if (contentHeight + topMargin + bottomMargin <= height) contentY = -topMargin
    bottomMargin: 24
    clip: true
    // rows this far out of view stay laid out, so their heights are real: a list of estimates moves under the reader
    cacheBuffer: 20000
    // a row that leaves is used again for one that comes in, not made anew (2026-11-05: the longest frames while
    // scrolling a 1000-message chat at 2560×1400 went from ~25 ms to ~15); it forgets its old message (reused())
    reuseItems: true
    // no current row: the list's highlight - unused, but there - crept towards it (the first row) across estimated
    // heights after every answer, and the window drew ~60 frames a second for minutes (24.5)
    currentIndex: -1
    highlightFollowsCurrentItem: false
    boundsBehavior: Flickable.StopAtBounds
    flickDeceleration: 4000

    ScrollBar.vertical: ThinScrollBar {}

    delegate: Item {
        id: slot
        required property int index
        required property var model
        width: list.width
        height: loader.height
        // after a theme switch a row is rendered again once in sight - on screen or within a screen of it (24.3)
        readonly property bool inSight: y + height > list.contentY - list.height && y < list.contentY + 2 * list.height
        onInSightChanged: if (inSight) App.restyleRow(index)
        Component.onCompleted: if (inSight) App.restyleRow(index)
        Connections {
            target: App
            function onRestyling() { if (slot.inSight) App.restyleRow(slot.index) }
        }
        // the last row grows while an answer streams: its end stays in view
        onHeightChanged: if (list.follow && index === list.count - 1) Qt.callLater(list.toEnd)
        ListView.onReused: {
            if (loader.item && loader.item.reused)
                loader.item.reused()
            if (inSight)
                App.restyleRow(index)
        }
        Loader {
            id: loader
            x: (slot.width - list.column) / 2
            width: list.column
            // while the model resets, a delegate may outlive its row for a moment
            sourceComponent: !slot.model ? null
                             : slot.model.role === "user" ? userMessage
                             : slot.model.role === "hub" ? hubNote
                             : slot.model.role === "error" ? errorMessage : assistantMessage
            onLoaded: {
                item.row = Qt.binding(() => slot.model)
                item.rowIndex = Qt.binding(() => slot.index)
            }
        }
    }
    Component { id: userMessage; UserMessage {} }
    Component { id: hubNote; HubNote {} }
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
    // contentY itself (a new row, a corrected estimate): that never stops it. Nor does a move of the empty list -
    // nobody scrolls that: the approval card's room moved it while a chat loaded, and the chat opened at its top.
    property real lastY: 0
    onContentYChanged: {
        if (!autoScroll) {
            if (endGap() <= 40)
                follow = true
            else if (contentY < lastY - 0.5 && count > 0)
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
            text: qsTr("Новый разговор")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 18
            font.weight: Font.DemiBold
            anchors.horizontalCenter: parent.horizontalCenter
        }
        Text {
            text: qsTr("Выберите модель внизу и спросите что-нибудь")
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.horizontalCenter: parent.horizontalCenter
        }
    }
}
