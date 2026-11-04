import QtQuick
import QtQuick.Controls.Basic

// Over the composer of a «Песочница» chat (D17, 9.4b): how many files the model changed in its copy of the project,
// and the way to them.
Rectangle {
    id: bar
    objectName: "sandboxBar"
    signal reviewRequested()
    height: 36
    color: Theme.p.card
    border.color: Theme.p.hairline

    Row {
        x: 16
        width: parent.width - 32
        anchors.verticalCenter: parent.verticalCenter
        spacing: 10
        Icon { name: "branch"; size: 15; tint: Theme.p.accent; anchors.verticalCenter: parent.verticalCenter }
        Text {
            width: parent.width - review.width - 40
            elide: Text.ElideRight
            text: Sandbox.summary
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
        }
        PlainButton {
            id: review
            objectName: "sandboxReview"
            visible: Sandbox.count > 0
            text: qsTr("Посмотреть правки")
            anchors.verticalCenter: parent.verticalCenter
            onClicked: bar.reviewRequested()
        }
    }
}
