import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// The programs of this computer and what the autonomous agent may control (D43): search, a filter by decision, and
// per program "allowed" / "denied" (choosing the chosen one again takes the decision back). Not decided is no.
Sheet {
    id: sheet
    objectName: "programsSheet"
    title: "Программы для агента"
    iconName: "shield"
    width: Math.min(680, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Programs.load()

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Автономный агент сможет управлять только разрешёнными программами. Хаб сам находит программы "
              + "из меню «Пуск» и Microsoft Store и спрашивает о каждой новой."
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.3
    }

    Row {
        width: parent.width
        spacing: 8
        Field {
            id: search
            objectName: "programSearch"
            width: parent.width - scanButton.width - addButton.width - 16
            height: 32
            placeholderText: "Поиск программы"
            onTextEdited: Programs.setQuery(text)
        }
        PlainButton {
            id: scanButton
            objectName: "programsScan"
            iconName: "refresh"
            text: Programs.scanning ? "Ищу…" : "Найти новые"
            enabled: !Programs.scanning
            onClicked: Programs.scan()
        }
        PlainButton {
            id: addButton
            iconName: "plus"
            text: "Добавить .exe…"
            onClicked: exeDialog.open()
        }
    }

    Segmented {
        width: parent.width
        options: Programs.filters
        value: Programs.view
        onPicked: (id) => Programs.setView(id)
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: Programs.status
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Text {
        visible: Programs.empty
        width: parent.width
        topPadding: 16
        horizontalAlignment: Text.AlignHCenter
        text: Programs.scanning ? "Ищу программы…" : "Здесь ничего нет"
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    // a list of a few hundred programs: only the rows in view exist
    ListView {
        id: list
        objectName: "programList"
        width: parent.width
        height: Math.max(160, sheet.height - 60 - y - 36)
        clip: true
        model: Programs.rowModel
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ThinScrollBar {}
        delegate: Item {
            id: row
            required property string id
            required property string name
            required property string detail
            required property string path
            required property string decision
            required property bool fresh
            required property bool removed
            required property bool manual
            width: list.width
            height: 58
            opacity: removed ? 0.6 : 1

            Rectangle {
                anchors.fill: parent
                anchors.bottomMargin: 2
                radius: 6
                color: hover.hovered ? Theme.p.item_hover : "transparent"
                HoverHandler { id: hover }
            }
            Column {
                x: 10
                width: parent.width - choice.width - (remove.visible ? remove.width + 6 : 0) - 30
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Row {
                    width: parent.width
                    spacing: 6
                    Text {
                        width: Math.min(implicitWidth, parent.width - (newMark.visible ? newMark.width + 6 : 0))
                        text: row.name
                        elide: Text.ElideRight
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Text {
                        id: newMark
                        visible: row.fresh
                        text: "новая"
                        color: Theme.p.accent
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    text: row.detail
                    elide: Text.ElideRight
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Text {
                    width: parent.width
                    text: row.path
                    elide: Text.ElideMiddle
                    color: Theme.p.faint
                    font.family: Theme.monoFont
                    font.pixelSize: 11
                }
            }
            Segmented {
                id: choice
                anchors.right: remove.visible ? remove.left : parent.right
                anchors.rightMargin: remove.visible ? 6 : 8
                anchors.verticalCenter: parent.verticalCenter
                width: 220
                options: [{ id: "allow", name: "Разрешена" }, { id: "deny", name: "Запрещена" }]
                value: row.decision
                onPicked: (id) => Programs.decide(row.id, id === row.decision ? "" : id)
            }
            IconButton {
                id: remove
                visible: row.manual
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.verticalCenter: parent.verticalCenter
                iconName: "trash"
                tip: "Убрать из списка (добавлена вручную)"
                onClicked: Programs.removeProgram(row.id)
            }
        }
    }

    FileDialog {
        id: exeDialog
        title: "Программа для агента"
        nameFilters: ["Программы (*.exe)"]
        onAccepted: Programs.addProgram(selectedFile.toString())
    }
}
