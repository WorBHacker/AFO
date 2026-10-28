import QtQuick
import QtQuick.Controls.Basic

// The open chat's approval requests as a card over the top of its conversation (D38), as wide as the reading
// column: oldest first, "1 из N", the next one after an answer. The conversation under it neither moves nor dims.
// It slides in and out; a new request takes the keyboard unless the user is typing a message.
Item {
    id: banner
    objectName: "approvalBanner"
    property real column: 780
    property real room: 600  // the height the card may take: from its top to the composer
    // a project's session open in «Проекты» too: without its card a Claude Code session there seemed stuck (2026-10-28)
    readonly property bool wanted: Approvals.cardCount > 0 && (App.mode === "chats" || App.mode === "agent"
                                                                || App.showProjectChat)
    property var shown: ({})  // kept while the card fades out after the last answer
    signal finished()  // the last request is answered: the keyboard goes back to the composer

    width: column
    height: card.implicitHeight
    visible: opacity > 0
    opacity: wanted ? 1 : 0
    transform: Translate { y: banner.wanted ? 0 : -12; Behavior on y { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } } }
    Behavior on opacity { NumberAnimation { duration: 160 } }

    function follow() {
        const next = Approvals.card
        const had = shown && shown.id ? shown.id : ""
        if (next && next.id) {
            shown = next
            if (next.id !== had && (App.mode !== "projects" || App.showProjectChat) && !banner.typing())
                Qt.callLater(card.forceActiveFocus)
        } else if (had && card.activeFocus) {
            banner.finished()
        }
    }
    // a message being written keeps the keyboard: an empty composer or no text field gives it to the card
    function typing() {
        const item = Window.activeFocusItem
        return !!item && item.hasOwnProperty("cursorPosition") && item.text !== undefined && item.text !== ""
    }
    Connections {
        target: Approvals
        function onHereChanged() { banner.follow() }
    }
    Component.onCompleted: follow()

    SoftShadow {}
    ApprovalCard {
        id: card
        objectName: "chatApprovalCard"
        width: parent.width
        height: implicitHeight
        request: banner.shown
        position: Approvals.cardCount > 1 ? "1 из " + Approvals.cardCount : ""
        // all of it: up to half the window, and the card ends above the composer (the rest of it is ~300px)
        previewMax: Math.min((banner.parent ? banner.parent.height : 800) * 0.5, banner.room - 300)
    }
}
