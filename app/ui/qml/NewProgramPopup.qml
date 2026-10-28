import QtQuick
import QtQuick.Controls.Basic

// A question at the top of the window (D43): the hub found a new program - may the autonomous agent control it?
// Allow, deny or decide later (later: no longer asked, still not allowed). After the first look there is one offer
// instead: "found N programs, choose". It does not dim the window and stays until answered.
Popup {
    id: popup
    objectName: "newProgramPopup"
    readonly property var current: Programs.pending.length > 0 ? Programs.pending[0] : null
    readonly property bool offering: current === null && Programs.offer > 0

    parent: Overlay.overlay
    width: Math.min(520, (parent ? parent.width : 960) - 32)
    x: parent ? (parent.width - width) / 2 : 0
    y: 60
    padding: 18
    modal: false
    closePolicy: Popup.NoAutoClose
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 100 } }

    // from Programs itself: this handler may run before `current` and `offering` follow the same signal. While the
    // open chat's approval card is up, the question waits: that chat stands still, a program does not
    property bool held: false  // the first-run wizard is up (10.2): the question waits for it
    onHeldChanged: follow()
    function follow() {
        const card = Approvals.cardCount > 0 && (App.mode === "chats" || App.mode === "agent")
        if ((Programs.pending.length > 0 || Programs.offer > 0) && !card && !held)
            open()
        else
            close()
    }
    Connections {
        target: Programs
        function onPendingChanged() { popup.follow() }
    }
    Connections {
        target: Approvals
        function onHereChanged() { popup.follow() }
    }
    Connections {
        target: App
        function onModeChanged() { popup.follow() }
    }
    Component.onCompleted: follow()

    background: Rectangle {
        radius: 10
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow {}
    }

    contentItem: Column {
        spacing: 10

        Row {
            spacing: 8
            Icon { name: "shield"; size: 16; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
            Text {
                text: popup.offering ? "Программы для автономного агента"
                      : Programs.pending.length > 1 ? "Новая программа · 1 из " + Programs.pending.length
                      : "Новая программа"
                color: Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 12
                font.weight: Font.DemiBold
                anchors.verticalCenter: parent.verticalCenter
            }
        }

        Column {
            visible: popup.current !== null
            width: parent.width
            spacing: 2
            Text {
                objectName: "newProgramName"
                width: parent.width
                text: popup.current ? popup.current.name : ""
                elide: Text.ElideRight
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: popup.current ? popup.current.detail : ""
                elide: Text.ElideRight
                color: Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: popup.current ? popup.current.path : ""
                elide: Text.ElideMiddle
                color: Theme.p.faint
                font.family: Theme.monoFont
                font.pixelSize: 11
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: popup.offering
                  ? "Найдено программ: " + Programs.offer + ". Выберите, какими может управлять автономный агент — "
                    + "пока ни одной нельзя. О новых программах хаб будет спрашивать сам."
                  : "Разрешить автономному агенту управлять этой программой? Пока не решено — нельзя."
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            lineHeight: 1.3
        }

        Row {
            visible: popup.current !== null
            spacing: 8
            PlainButton {
                objectName: "allowProgram"
                kind: "primary"
                iconName: "check"
                text: "Разрешить"
                onClicked: Programs.decide(popup.current.id, "allow")
            }
            PlainButton {
                objectName: "denyProgram"
                kind: "danger"
                iconName: "close"
                text: "Запретить"
                onClicked: Programs.decide(popup.current.id, "deny")
            }
            PlainButton {
                objectName: "laterProgram"
                text: "Решить позже"
                onClicked: Programs.later(popup.current.id)
            }
        }
        Row {
            visible: popup.offering
            spacing: 8
            PlainButton {
                objectName: "chooseProgramsOffer"
                kind: "primary"
                iconName: "shield"
                text: "Выбрать программы…"
                onClicked: Programs.openList()
            }
            PlainButton {
                text: "Позже"
                onClicked: Programs.dismissOffer()
            }
        }
        Text {
            visible: popup.current !== null
            text: "<a href=\"list\">Все программы…</a>"
            textFormat: Text.StyledText
            linkColor: Theme.p.link
            font.family: Theme.uiFont
            font.pixelSize: 12
            onLinkActivated: Programs.openList()
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
    }
}
