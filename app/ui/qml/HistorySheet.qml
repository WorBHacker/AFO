import QtQuick
import QtQuick.Controls.Basic

// «История файла» (stage 26.2): every version of the file the hub kept, whoever changed it - on the left, newest
// first; on the right the chosen one's diff: what changed in it, or what taking it back would change in the file
// now. «Вернуть эту версию» goes through the hub, the way a save goes (14.1).
Popup {
    id: sheet
    objectName: "historySheet"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1000, (parent ? parent.width : 1000) - 48)
    height: Math.min(680, (parent ? parent.height : 680) - 48)
    modal: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onClosed: FileHistory.close()
    background: Rectangle {
        radius: 10
        color: Theme.p.popup
        border.color: Theme.p.hairline_strong
        SoftShadow {}
    }

    Connections {
        target: FileHistory
        function onCloseRequested() { sheet.close() }
    }

    contentItem: Item {
        Item {
            id: head
            width: parent.width
            height: 56
            Icon {
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                name: "history"
                size: 18
                tint: Theme.p.text
            }
            Column {
                x: 44
                width: Math.max(0, headButtons.x - x - 12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Text {
                    text: qsTr("История файла")
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }
                Text {
                    width: parent.width
                    text: FileHistory.path
                    elide: Text.ElideMiddle
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
            Row {
                id: headButtons
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Segmented {
                    objectName: "historyMode"
                    visible: FileHistory.rows.length > 0
                    anchors.verticalCenter: parent.verticalCenter
                    options: [{ id: "previous", name: qsTr("Что изменилось") },
                              { id: "current", name: qsTr("С файлом сейчас") }]
                    value: FileHistory.mode
                    onPicked: (id) => FileHistory.setMode(id)
                }
                PlainButton {
                    objectName: "historyRestore"
                    visible: FileHistory.rows.length > 0
                    enabled: FileHistory.canRestore
                    anchors.verticalCenter: parent.verticalCenter
                    kind: "primary"
                    iconName: "history"
                    text: FileHistory.busy ? qsTr("Возвращаю…") : qsTr("Вернуть эту версию")
                    onClicked: FileHistory.restore()
                }
                IconButton {
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "close"
                    tip: qsTr("Закрыть (Esc)")
                    onClicked: sheet.close()
                }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
        }

        ListView {
            id: versions
            objectName: "historyList"
            y: head.height
            width: 290
            height: parent.height - y
            clip: true
            model: FileHistory.rows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ThinScrollBar {}
            delegate: AbstractButton {
                id: entry
                required property var modelData
                width: versions.width
                height: 50
                hoverEnabled: true
                onClicked: FileHistory.pick(modelData.id)
                background: Rectangle {
                    color: entry.modelData.selected ? Theme.p.item_selected
                           : entry.hovered ? Theme.p.item_hover : "transparent"
                    Rectangle {
                        visible: entry.modelData.selected
                        width: 2
                        height: parent.height
                        color: Theme.p.accent
                    }
                }
                contentItem: Item {
                    Column {
                        x: 14
                        width: parent.width - x - (nowChip.visible ? nowChip.width + 18 : 10)
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            width: parent.width
                            text: entry.modelData.when
                            color: entry.modelData.deleted ? Theme.p.muted : Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                            font.weight: entry.modelData.selected ? Font.DemiBold : Font.Normal
                        }
                        Text {
                            width: parent.width
                            text: [entry.modelData.who, entry.modelData.size].filter(s => !!s).join(" · ")
                            elide: Text.ElideRight
                            color: entry.modelData.deleted ? Theme.p.warn : Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                    }
                    Rectangle {
                        id: nowChip
                        visible: entry.modelData.current
                        anchors.right: parent.right
                        anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        width: nowText.implicitWidth + 12
                        height: 18
                        radius: 9
                        color: "transparent"
                        border.color: Theme.p.accent
                        Text {
                            id: nowText
                            anchors.centerIn: parent
                            text: qsTr("сейчас")
                            color: Theme.p.accent
                            font.family: Theme.uiFont
                            font.pixelSize: 10
                        }
                    }
                }
            }
        }
        Rectangle { x: versions.width; y: head.height; width: 1; height: parent.height - y; color: Theme.p.hairline }

        Text {
            id: note
            objectName: "historyNote"
            x: versions.width + 16
            y: head.height + 10
            width: parent.width - x - 16
            visible: FileHistory.rows.length > 0
            text: FileHistory.note
            elide: Text.ElideRight
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        CodeView {
            id: code
            objectName: "historyView"
            x: versions.width + 1
            y: note.y + note.height + 10
            width: parent.width - x
            height: parent.height - y
            visible: FileHistory.rows.length > 0 && !!FileHistory.view.key && !FileHistory.view.empty
            source: FileHistory.view
            reader: "history"
            showNumbers: false
        }
        Text {
            objectName: "historyEmpty"
            anchors.centerIn: FileHistory.rows.length > 0 ? code : parent
            width: Math.min(440, (FileHistory.rows.length > 0 ? code.width : parent.width) - 48)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            visible: !!text
            text: FileHistory.state === "loading" && FileHistory.rows.length === 0 ? qsTr("Читаю…")
                  : FileHistory.empty ? FileHistory.empty
                  : FileHistory.view.empty ? (FileHistory.mode === "current" ? qsTr("Эта версия совпадает с файлом сейчас.")
                                                                             : qsTr("В этой версии содержимое не изменилось."))
                  : ""
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
        }
    }
}
