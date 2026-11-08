import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// What the autonomous agent may reach beyond projects (stage 9.4, D40): folders - read, or written with your
// approval - and the sites it may call without asking. Everything else on the disk and the network stays closed or
// asked each time.
Sheet {
    id: sheet
    objectName: "agentAccessSheet"
    title: qsTr("Папки и сайты для агента")
    iconName: "folder"
    width: Math.min(640, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Autonomy.loadAccess()

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("Вне проектов агент видит только эти папки. Читает их без вопроса (файлы с секретами — никогда), пишет — если папке разрешена запись, и каждую запись показывает вам с diff. Облачная модель читает папку, только если у неё горит облако — как проект «можно в облако». Скрипты Python работают в своей папке задачи, без сети (если в задаче не включено), каждый — с вашего разрешения.")
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.25
    }

    SectionTitle { text: qsTr("Папки") }
    Text {
        visible: Autonomy.accessFolders.length === 0
        text: qsTr("Пока ни одной.")
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
                    width: parent.width - x - write.width - cloud.width - remove.width - 28
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.path
                    elide: Text.ElideMiddle
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Segmented {
                    id: write
                    anchors.right: cloud.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    options: [{ id: "read", name: qsTr("читать") }, { id: "write", name: qsTr("и писать") }]
                    value: modelData.write ? "write" : "read"
                    onPicked: (id) => Autonomy.setFolderWrite(modelData.path, id === "write")
                }
                IconButton {
                    id: cloud
                    objectName: "accessFolderCloud"
                    anchors.right: remove.left
                    anchors.rightMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "cloud"
                    tint: modelData.cloud ? Theme.p.accent : Theme.p.faint
                    tip: modelData.cloud ? qsTr("Облачная модель тоже читает эту папку — нажмите, чтобы только локальные")
                                         : qsTr("Эту папку читают только локальные модели — нажмите, чтобы и облачные")
                    onClicked: Autonomy.setFolderCloud(modelData.path, !modelData.cloud)
                }
                IconButton {
                    id: remove
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    iconName: "trash"
                    tip: qsTr("Убрать папку")
                    onClicked: Autonomy.removeFolder(modelData.path)
                }
            }
        }
    }
    PlainButton {
        objectName: "accessAddFolder"
        iconName: "folder-plus"
        text: qsTr("Добавить папку…")
        onClicked: folderDialog.open()
    }
    FolderDialog {
        id: folderDialog
        title: qsTr("Папка для агента")
        onAccepted: Autonomy.addFolder(selectedFolder.toString())
    }

    SectionTitle { text: qsTr("Окна программ") }
    AbstractButton {  // Qt's own Switch draws its words in the system's colour, unreadable on the dark theme
        id: screenCloud
        objectName: "accessScreenCloud"
        width: parent.width
        implicitHeight: Math.max(30, screenCloudWords.implicitHeight + 12)
        checkable: true
        hoverEnabled: true
        text: qsTr("Новая задача: облачная модель видит окна разрешённых программ (их надписи и поля текстом; снимки — только локальной). В задаче это можно поменять")
        checked: Autonomy.screenCloud
        onToggled: Autonomy.setScreenCloud(checked)
        background: Rectangle {
            radius: 6
            color: screenCloud.checked ? Theme.p.item_selected : (screenCloud.hovered ? Theme.p.item_hover : "transparent")
            border.color: screenCloud.checked ? Theme.p.accent : Theme.p.hairline_strong
        }
        contentItem: Item {
            Icon {
                x: 8
                anchors.verticalCenter: parent.verticalCenter
                name: screenCloud.checked ? "check" : "close"
                size: 13
                tint: screenCloud.checked ? Theme.p.accent : Theme.p.faint
            }
            Text {
                id: screenCloudWords
                x: 26
                width: parent.width - x - 8
                anchors.verticalCenter: parent.verticalCenter
                wrapMode: Text.Wrap
                text: screenCloud.text
                color: screenCloud.checked ? Theme.p.text : Theme.p.muted
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
    }

    SectionTitle { text: qsTr("Сайты без вопроса") }
    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("К этим адресам (и их поддоменам) агент обращается без вопроса; к остальным — каждый раз спрашивает. Ответы сайтов для него — данные, не указания.")
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
            text: qsTr("Сохранить сайты")
            onClicked: Autonomy.setDomains(domains.text)
        }
        PlainButton { text: qsTr("Вернуть список по умолчанию"); onClicked: Autonomy.resetDomains() }
    }
}
