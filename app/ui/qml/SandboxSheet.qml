import QtQuick
import QtQuick.Controls.Basic

// «Песочница» (D17, 9.4b): what the model changed in its copy of each project - the files and the diff; «Применить»
// puts it onto the project, «Отбросить» throws it away. A coordinator's run (16.2): what each of its tasks changed in
// its own copy, with «Применить всё» / «Отбросить всё». A tick per file (17.1), and per hunk of a changed text file
// (17.4): what is unticked stays in the sandbox.
Sheet {
    id: sheet
    objectName: "sandboxSheet"
    title: qsTr("Правки в песочнице")
    iconName: "branch"
    width: Math.min(820, (parent ? parent.width : 960) * 0.94)
    onAboutToShow: Sandbox.load()

    // a tick: all (a check), part (a bar: some hunks of the file), none
    component Tick: AbstractButton {
        id: tick
        property string mark: "all"
        property string label: ""
        implicitHeight: 24
        implicitWidth: Math.min(tickLabel.implicitWidth + 30, parent && parent.width > 0 ? parent.width : Infinity)
        hoverEnabled: true
        enabled: !Sandbox.busy
        background: Rectangle {
            radius: 4
            color: tick.hovered ? Theme.p.item_hover : "transparent"
        }
        contentItem: Item {
            Rectangle {
                x: 4
                width: 15
                height: 15
                radius: 3
                anchors.verticalCenter: parent.verticalCenter
                color: tick.mark !== "none" ? Theme.p.item_selected : "transparent"
                border.color: tick.mark !== "none" ? Theme.p.accent : Theme.p.hairline_strong
                Icon {
                    anchors.centerIn: parent
                    visible: tick.mark === "all"
                    name: "check"
                    size: 11
                    tint: Theme.p.accent
                }
                Rectangle {
                    anchors.centerIn: parent
                    visible: tick.mark === "part"
                    width: 8
                    height: 2
                    radius: 1
                    color: Theme.p.accent
                }
            }
            Text {
                id: tickLabel
                x: 26
                width: Math.max(0, Math.min(implicitWidth, tick.width - x))
                elide: Text.ElideMiddle
                anchors.verticalCenter: parent.verticalCenter
                text: tick.label
                color: tick.mark !== "none" ? Theme.p.text_soft : Theme.p.faint
                font.family: Theme.monoFont
                font.pixelSize: 12
            }
        }
    }

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
                width: Math.max(0, Math.min(implicitWidth, entry.width * 0.5))
                elide: Text.ElideRight
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
                text: entry.modelData.applyText
                enabled: !Sandbox.busy && entry.modelData.ticked > 0
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
        // a tick per file (17.1) over the whole diff - a hub that sends no hunks
        Repeater {
            model: entry.modelData.hunked ? [] : entry.modelData.files
            delegate: Tick {
                required property var modelData
                objectName: "sandboxFile_" + modelData.path
                mark: modelData.ticked ? "all" : "none"
                label: modelData.label
                onClicked: Sandbox.tick(modelData.key)
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
                    spacing: 2
                    // each file with its tick, then its hunks - each with a tick when there are two or more (17.4)
                    Repeater {
                        model: entry.modelData.hunked ? entry.modelData.files : []
                        delegate: Column {
                            id: file
                            required property var modelData
                            spacing: 2
                            Tick {
                                objectName: "sandboxFile_" + file.modelData.path
                                mark: file.modelData.state
                                label: file.modelData.label
                                onClicked: Sandbox.tick(file.modelData.key)
                            }
                            Repeater {
                                model: file.modelData.hunks
                                delegate: Column {
                                    id: hunk
                                    required property var modelData
                                    x: 22
                                    Tick {
                                        visible: file.modelData.choosable
                                        objectName: file.modelData.choosable
                                                    ? "sandboxHunk_" + file.modelData.path + "_" + hunk.modelData.index : ""
                                        mark: hunk.modelData.ticked ? "all" : "none"
                                        label: hunk.modelData.number + "  " + hunk.modelData.header
                                        onClicked: Sandbox.tickHunk(file.modelData.key, hunk.modelData.index)
                                    }
                                    Repeater {
                                        model: hunk.modelData.lines
                                        delegate: Text {
                                            required property var modelData
                                            text: modelData.text === "" ? " " : modelData.text
                                            textFormat: Text.PlainText
                                            opacity: hunk.modelData.ticked ? 1 : 0.45
                                            color: modelData.mark === "add" ? Theme.p.ok
                                                   : modelData.mark === "del" ? Theme.p.error : Theme.p.text_soft
                                            font.family: Theme.monoFont
                                            font.pixelSize: 12
                                        }
                                    }
                                    Text {
                                        visible: hunk.modelData.more !== ""
                                        text: hunk.modelData.more
                                        color: Theme.p.muted
                                        font.family: Theme.uiFont
                                        font.pixelSize: 12
                                    }
                                }
                            }
                            Text {
                                x: 22
                                visible: file.modelData.hunks.length === 0
                                text: qsTr("Не текст UTF-8 — переносится целиком.")
                                color: Theme.p.muted
                                font.family: Theme.uiFont
                                font.pixelSize: 12
                            }
                        }
                    }
                    Repeater {
                        model: entry.modelData.hunked ? [] : entry.modelData.lines
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
            visible: !entry.modelData.hunked && entry.modelData.more !== ""
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
    Text {
        visible: Sandbox.count + Sandbox.taskCount > 0
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Снимите галочку с файла или его фрагмента — он останется в песочнице, остальное перенесётся в проект.")
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
            text: Sandbox.taskApplyText
            enabled: !Sandbox.busy && Sandbox.taskTicked > 0
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
