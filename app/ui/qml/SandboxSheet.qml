import QtQuick
import QtQuick.Controls.Basic

// «Песочница» (D17, 9.4b): what the model changed in its copy of each project - the files and the diff; «Применить»
// puts it onto the project, «Отбросить» throws it away. A coordinator's run (16.2): what each of its tasks changed in
// its own copy, with «Применить всё» / «Отбросить всё».
Sheet {
    id: sheet
    objectName: "sandboxSheet"
    title: qsTr("Правки в песочнице")
    iconName: "branch"
    width: Math.min(820, (parent ? parent.width : 960) * 0.94)
    onAboutToShow: Sandbox.load()

    // one project's changes: its name (with buttons when it is the chat's own), the files, the diff
    component Entry: Column {
        id: entry
        required property var modelData
        property bool own: true
        width: parent ? parent.width : 600
        spacing: 6
        Rectangle { width: parent.width; height: 1; color: Theme.p.hairline }
        Row {
            spacing: 8
            Text {
                text: entry.modelData.name
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
                anchors.verticalCenter: parent.verticalCenter
            }
            PlainButton {
                visible: entry.own
                objectName: "sandboxApply_" + entry.modelData.id
                kind: "primary"
                iconName: "check"
                text: qsTr("Применить")
                enabled: !Sandbox.busy
                onClicked: Sandbox.apply(entry.modelData.id)
            }
            PlainButton {
                visible: entry.own
                kind: "danger"
                iconName: "trash"
                text: qsTr("Отбросить")
                enabled: !Sandbox.busy
                onClicked: Sandbox.discard(entry.modelData.id)
            }
        }
        Text {
            visible: entry.modelData.error !== ""
            width: parent.width
            wrapMode: Text.Wrap
            text: entry.modelData.error
            color: Theme.p.error
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Repeater {
            model: entry.modelData.files
            delegate: Text {
                required property var modelData
                text: "• " + modelData
                color: Theme.p.text_soft
                font.family: Theme.monoFont
                font.pixelSize: 12
            }
        }
        Rectangle {
            width: parent.width
            height: Math.min(diff.implicitHeight + 16, 420)
            radius: 6
            color: Theme.p.card
            border.color: Theme.p.hairline
            ScrollView {
                anchors.fill: parent
                anchors.margins: 8
                clip: true
                Column {
                    id: diff
                    Repeater {
                        model: entry.modelData.lines
                        delegate: Text {
                            required property var modelData
                            text: modelData.text === "" ? " " : modelData.text
                            textFormat: Text.PlainText
                            color: modelData.mark === "add" ? Theme.p.ok : modelData.mark === "del" ? Theme.p.error
                                 : modelData.mark === "hunk" ? Theme.p.accent : Theme.p.text_soft
                            font.family: Theme.monoFont
                            font.pixelSize: 12
                        }
                    }
                }
            }
        }
        Text {
            objectName: "sandboxMore"
            visible: entry.modelData.more !== ""
            width: parent.width
            wrapMode: Text.Wrap
            text: entry.modelData.more
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: Sandbox.taskCount ? qsTr("Подзадачи координатора писали каждая в свою копию проекта (git worktree, своя ветка). «Применить всё» переносит их правки в проекты в том порядке, в каком шли подзадачи; до этого проекты не меняются.")
            : Sandbox.count ? qsTr("Модель писала в отдельную копию проекта (git worktree, своя ветка). Проект не менялся, пока вы не нажмёте «Применить».")
            : qsTr("Правок пока нет.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Row {
        visible: Sandbox.taskCount > 0
        spacing: 8
        PlainButton {
            objectName: "sandboxApplyAll"
            kind: "primary"
            iconName: "check"
            text: qsTr("Применить всё")
            enabled: !Sandbox.busy
            onClicked: Sandbox.applyAll()
        }
        PlainButton {
            objectName: "sandboxDiscardAll"
            kind: "danger"
            iconName: "trash"
            text: qsTr("Отбросить всё")
            enabled: !Sandbox.busy
            onClicked: Sandbox.discardAll()
        }
    }
    Repeater {
        model: Sandbox.tasks
        delegate: Entry { own: false }
    }
    Repeater {
        model: Sandbox.projects
        delegate: Entry {}
    }
}
