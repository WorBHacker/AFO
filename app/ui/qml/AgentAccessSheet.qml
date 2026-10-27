import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// What the autonomous agent may reach beyond projects (stage 9.4, D40): folders - read, or written with your
// approval - and the sites it may call without asking. Everything else on the disk and the network stays closed or
// asked each time.
Sheet {
    id: sheet
    objectName: "agentAccessSheet"
    title: "Папки и сайты для агента"
    iconName: "folder"
    width: Math.min(640, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Autonomy.loadAccess()

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Вне проектов агент видит только эти папки. Читает их без вопроса (файлы с секретами — никогда), пишет — если папке разрешена запись, и каждую запись показывает вам с diff. Скрипты Python работают в своей папке задачи, без сети (если в задаче не включено), каждый — с вашего разрешения."
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.25
    }

    SectionTitle { text: "Папки" }
    Text {
        visible: Autonomy.accessFolders.length === 0
        text: "Пока ни одной."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Column {
        objectName: "accessFolders"
        width: parent.width
        spacing: 4
        Repeater {
            model: Autonomy.accessFolders
            delegate: Item {
                required property var modelData
                width: parent.width
                height: 36
                Icon { x: 4; anchors.verticalCenter: parent.verticalCenter; name: "folder"; tint: Theme.p.muted }
                Text {
                    x: 28
                    width: parent.width - x - write.width - remove.width - 20
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.path
                    elide: Text.ElideMiddle
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Segmented {
                    id: write
                    anchors.right: remove.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    options: [{ id: "read", name: "читать" }, { id: "write", name: "и писать" }]
                    value: modelData.write ? "write" : "read"
                    onPicked: (id) => Autonomy.setFolderWrite(modelData.path, id === "write")
                }
                IconButton {
                    id: remove
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "trash"
                    tip: "Убрать папку"
                    onClicked: Autonomy.removeFolder(modelData.path)
                }
            }
        }
    }
    PlainButton {
        objectName: "accessAddFolder"
        iconName: "folder-plus"
        text: "Добавить папку…"
        onClicked: folderDialog.open()
    }
    FolderDialog {
        id: folderDialog
        title: "Папка для агента"
        onAccepted: Autonomy.addFolder(selectedFolder.toString())
    }

    SectionTitle { text: "Сайты без вопроса" }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "К этим адресам (и их поддоменам) агент обращается без вопроса; к остальным — каждый раз спрашивает. Ответы сайтов для него — данные, не указания."
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Rectangle {
        width: parent.width
        height: 110
        radius: 6
        color: Theme.p.card
        border.color: domains.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: domains
                objectName: "accessDomains"
                text: Autonomy.domainsText
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                font.family: Theme.monoFont
                font.pixelSize: 12
                padding: 10
                background: null
            }
        }
    }
    Row {
        spacing: 8
        PlainButton {
            objectName: "accessSaveDomains"
            kind: "primary"
            iconName: "check"
            text: "Сохранить сайты"
            onClicked: Autonomy.setDomains(domains.text)
        }
        PlainButton { text: "Вернуть список по умолчанию"; onClicked: Autonomy.resetDomains() }
    }
}
