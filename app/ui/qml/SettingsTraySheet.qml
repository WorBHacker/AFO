import QtQuick
import QtQuick.Controls.Basic

// Settings → «Трей и видеокарта» (43.7, the user's word 2026-11-13): what the window's cross does, AFO at Windows'
// sign-in, the tray menu's items; and «Видеокарта — моделям» - programs that are not games go to the integrated
// graphics through Windows' own «Графика» setting (from their next start), each program's place and why, the person's
// choice for one, «Вернуть как было» - the only way back: the programs stay there after «Выход» too.
Sheet {
    id: sheet
    objectName: "settingsTray"
    title: qsTr("Трей и видеокарта")
    iconName: "bolt"
    onOpened: Gpu.load()

    Column {
        id: tray
        width: parent.width
        spacing: 6
        component Switch: AbstractButton {
            id: toggle
            checkable: true
            implicitHeight: 30
            implicitWidth: label.implicitWidth + 40
            hoverEnabled: true
            background: Rectangle {
                radius: 6
                color: toggle.checked ? Theme.p.item_selected : (toggle.hovered ? Theme.p.item_hover : "transparent")
                border.color: toggle.checked ? Theme.p.accent : Theme.p.hairline_strong
            }
            contentItem: Item {
                Icon {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    name: toggle.checked ? "check" : "close"
                    size: 13
                    tint: toggle.checked ? Theme.p.accent : Theme.p.faint
                }
                Text {
                    id: label
                    x: 26
                    anchors.verticalCenter: parent.verticalCenter
                    text: toggle.text
                    color: toggle.checked ? Theme.p.text : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
            }
        }
        component Note: Text {
            width: tray.width
            wrapMode: Text.Wrap
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }

        Switch {
            objectName: "closeToTray"
            text: qsTr("Крестик прячет окно в трей — AFO работает дальше")
            checked: App.closeToTray
            onToggled: App.setCloseToTray(checked)
        }
        Note {
            visible: !App.hasTray
            text: qsTr("Значка в трее сейчас нет — крестик закрывает окно.")
            color: Theme.p.warn
        }
        Switch {
            objectName: "trayLogin"
            text: qsTr("Запускать AFO при входе в Windows — сразу в трей")
            checked: !!App.autostart.login
            onToggled: App.setAutostart("login", checked)
        }
        Note { text: qsTr("«Выход» в меню трея останавливает всё: окно, хаб, модели и llama-swap, который запускает AFO.") }
        SectionTitle { text: qsTr("В меню трея") }
        Flow {
            width: parent.width
            spacing: 6
            Repeater {
                model: [
                    { id: "projects", name: qsTr("Проекты") },
                    { id: "model", name: qsTr("Загруженная модель") },
                    { id: "quiet", name: qsTr("Тихие часы") },
                    { id: "gpu", name: qsTr("Видеокарта — моделям") }
                ]
                delegate: Switch {
                    required property var modelData
                    objectName: "trayItem_" + modelData.id
                    text: modelData.name
                    checked: !!App.trayItems[modelData.id]
                    onToggled: App.setTrayItem(modelData.id, checked)
                }
            }
        }

        SectionTitle { text: qsTr("Видеокарта — моделям") }
        Note { objectName: "gpuSummary"; text: Gpu.summary; color: Gpu.available ? Theme.p.text_soft : Theme.p.muted }
        Switch {
            objectName: "gpuOn"
            visible: Gpu.available
            text: qsTr("Переводить неигровые программы на встроенную графику")
            checked: Gpu.on
            onToggled: Gpu.setOn(checked)
        }
        Note {
            visible: Gpu.available
            text: qsTr("Это настройка Windows «Графика»: программа переходит на встроенную графику со следующего своего запуска, и видеопамять видеокарты остаётся моделям. Игры и программы, которым нужна видеокарта, остаются на ней. Переведённые программы остаются на встроенной графике и после выхода из AFO — вернуть их можно только здесь.")
        }
        Repeater {
            model: Gpu.programs
            delegate: Item {
                id: row
                required property var modelData
                width: tray.width
                height: Math.max(30, words.implicitHeight + 8)
                Column {
                    id: words
                    anchors.left: parent.left
                    anchors.right: choose.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        width: parent.width
                        elide: Text.ElideMiddle
                        text: row.modelData.name + " · " + row.modelData.size
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: row.modelData.text
                        color: row.modelData.where === "waits" ? Theme.p.warn : Theme.p.muted
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                PlainButton {
                    id: choose
                    objectName: "gpuChoose"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    visible: Gpu.available && row.modelData.why !== "windows"
                    text: row.modelData.offerText
                    onClicked: Gpu.choose(row.modelData.exe, row.modelData.offer)
                }
            }
        }
        PlainButton {
            objectName: "gpuGiveBack"
            visible: Gpu.programs.some(p => p.where === "moved" || p.where === "waits")
            iconName: "history"
            text: qsTr("Вернуть как было")
            onClicked: Gpu.giveBack()
        }
    }
}
