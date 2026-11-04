import QtQuick
import QtQuick.Controls.Basic

// Over the composer of a chat with a goal (D44, 11.3): what the goal is, how it stands, the turn and the limits, what
// the last check found; the criteria with ticks, what is missing and the judge's word unfold below. «Пауза»,
// «Продолжить» (at the limit - with higher limits), «Снять»; a met goal - «Скрыть».
Rectangle {
    id: bar
    objectName: "goalBar"
    readonly property color tone: Goal.status === "active" ? Theme.p.accent
                                  : Goal.status === "achieved" ? Theme.p.ok
                                  : Goal.status === "paused" ? Theme.p.faint : Theme.p.warn
    height: body.implicitHeight + 16
    color: Theme.p.card
    border.color: Theme.p.hairline

    Column {
        id: body
        x: 16
        y: 8
        width: parent.width - 32
        spacing: 4

        Item {
            width: parent.width
            height: 30
            Icon {
                id: mark
                name: "target"
                size: 16
                tint: bar.tone
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                id: what
                x: mark.width + 10
                width: Math.min(implicitWidth, parent.width - x - state.width - buttons.width - 24)
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Цель: %1").arg(Goal.objective)
                elide: Text.ElideRight
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
            Rectangle {
                id: state
                objectName: "goalState"
                x: what.x + what.width + 10
                anchors.verticalCenter: parent.verticalCenter
                width: stateText.implicitWidth + 16
                height: 20
                radius: 10
                color: "transparent"
                border.color: bar.tone
                Text {
                    id: stateText
                    anchors.centerIn: parent
                    text: Goal.statusText
                    color: bar.tone
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
            }
            Row {
                id: buttons
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 4
                PlainButton {
                    objectName: "goalResume"
                    visible: ["paused", "blocked", "budget"].indexOf(Goal.status) >= 0
                    implicitHeight: 26
                    iconName: "play"
                    text: Goal.status === "budget" ? qsTr("Продолжить с запасом") : qsTr("Продолжить")
                    onClicked: Goal.resume()
                    ToolTip.visible: hovered
                    ToolTip.delay: 600
                    ToolTip.text: Goal.status === "budget" ? qsTr("Пределы поднимутся на половину, и работа пойдёт дальше")
                                                           : qsTr("Следующий ход начнётся сейчас")
                }
                IconButton {
                    objectName: "goalPause"
                    visible: Goal.status === "active"
                    iconName: "pause"
                    tip: qsTr("Пауза: идущий ход доработает, следующий не начнётся")
                    onClicked: Goal.pause()
                }
                IconButton {
                    objectName: "goalClear"
                    visible: Goal.status !== "achieved"
                    iconName: "close"
                    tip: qsTr("Снять цель")
                    onClicked: Goal.clear()
                }
                PlainButton {
                    objectName: "goalDismiss"
                    visible: Goal.status === "achieved"
                    implicitHeight: 26
                    text: qsTr("Скрыть")
                    onClicked: Goal.dismiss()
                }
                IconButton {
                    objectName: "goalDetails"
                    iconName: Goal.expanded ? "chevron-down" : "chevron-right"
                    tip: Goal.expanded ? qsTr("Свернуть") : qsTr("Критерии, чего не хватает, слово проверяющей модели")
                    onClicked: Goal.toggle()
                }
            }
        }
        Text {
            objectName: "goalLine"
            x: mark.width + 10
            width: parent.width - x
            elide: Text.ElideRight
            text: Goal.progress + "  ·  " + Goal.lastCheck + (Goal.detail ? " — " + Goal.detail : "")
                  + (Goal.waitingWords ? qsTr("  ·  уточнений ждут: %1").arg(Goal.waitingWords) : "")
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Column {
            objectName: "goalDetailsBody"
            visible: Goal.expanded
            x: mark.width + 10
            width: parent.width - x
            spacing: 4
            topPadding: 6
            Repeater {
                model: Goal.criteria
                delegate: Row {
                    required property var modelData
                    spacing: 8
                    width: parent.width
                    Icon {
                        name: modelData.met ? "check" : "close"
                        size: 13
                        tint: modelData.met ? Theme.p.ok : Theme.p.faint
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        width: parent.width - 24
                        wrapMode: Text.Wrap
                        text: modelData.words ? qsTr("словами: %1").arg(modelData.text) : modelData.text
                        color: Theme.p.text_soft
                        font.family: modelData.words ? Theme.uiFont : Theme.monoFont
                        font.pixelSize: 12
                    }
                }
            }
            Text {
                visible: Goal.missing.length > 0
                width: parent.width
                wrapMode: Text.Wrap
                topPadding: 4
                text: qsTr("Не хватает:\n%1").arg(Goal.missing.map(m => "• " + m).join("\n"))
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
            Text {
                visible: Goal.judgeWord !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: qsTr("Проверяющая модель: «%1»").arg(Goal.judgeWord)
                color: Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
                font.italic: true
            }
        }
    }
}
