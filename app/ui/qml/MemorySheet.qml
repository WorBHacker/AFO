import QtQuick
import QtQuick.Controls.Basic

// The agent's memory (D40, Q24): the facts the hub keeps for good - where each came from and when, personal or not,
// allowed for a cloud model or not - corrected or deleted here; and the skills, plans of finished tasks kept for
// similar goals. Everything stays on this computer.
Sheet {
    id: sheet
    objectName: "memorySheet"
    title: "Память агента"
    iconName: "layers"
    width: Math.min(760, (parent ? parent.width : 960) * 0.92)
    onAboutToShow: Memory.load()

    component Toggle: AbstractButton {
        id: toggle
        property bool on: false
        implicitHeight: 24
        implicitWidth: label.implicitWidth + 26
        hoverEnabled: true
        background: Rectangle { radius: 5; color: toggle.on ? Theme.p.item_selected : (toggle.hovered ? Theme.p.item_hover : "transparent")
                                border.color: toggle.on ? Theme.p.accent : Theme.p.hairline }
        contentItem: Text {
            id: label
            text: toggle.text
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            color: toggle.on ? Theme.p.text : Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        text: "Агент запоминает факты сам; каждый виден здесь — исправьте или удалите, если неверно. Личные данные (контакты, адреса, телефоны) он сохраняет только с вашего разрешения и никогда не отдаёт облачной модели. Всё хранится на этом компьютере."
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.25
    }
    Field {
        objectName: "memorySearch"
        width: parent.width
        placeholderText: "Найти в памяти"
        onTextEdited: Memory.search(text)
    }

    SectionTitle { text: "Факты" }
    Row {
        width: parent.width
        spacing: 8
        Field {
            id: newFact
            objectName: "memoryNewFact"
            width: parent.width - addButton.width - 8
            placeholderText: "Запомнить самим: например, «отчёты — в папку D:\\Отчёты»"
            onAccepted: addButton.clicked()
        }
        PlainButton {
            id: addButton
            objectName: "memoryAdd"
            iconName: "plus"
            text: "Запомнить"
            onClicked: { Memory.addFact(newFact.text, "", false); newFact.text = "" }
        }
    }
    Text {
        visible: Memory.facts.length === 0
        text: Memory.query ? "Ничего не найдено." : "Пока ничего не запомнено."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Column {
        objectName: "memoryFacts"
        width: parent.width
        spacing: 6
        Repeater {
            model: Memory.facts
            delegate: Rectangle {
                id: factRow
                required property var modelData
                property bool editing: false
                width: parent.width
                height: factBody.height + 20
                radius: 6
                color: Theme.p.card
                border.color: modelData.personal ? Theme.p.warn : Theme.p.hairline
                Column {
                    id: factBody
                    x: 12
                    y: 10
                    width: parent.width - 24 - trash.width
                    spacing: 6
                    Text {
                        visible: !factRow.editing
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: factRow.modelData.text
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        TapHandler { onDoubleTapped: { factEdit.text = factRow.modelData.text; factRow.editing = true } }
                    }
                    Field {
                        id: factEdit
                        visible: factRow.editing
                        width: parent.width
                        onAccepted: { Memory.saveFact(factRow.modelData.id, text); factRow.editing = false }
                        onEditingFinished: factRow.editing = false
                    }
                    Row {
                        spacing: 6
                        Text {
                            text: factRow.modelData.meta
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Toggle {
                            text: "личное"
                            on: factRow.modelData.personal
                            onClicked: Memory.setFlag(factRow.modelData.id, "personal", !on)
                        }
                        Toggle {
                            visible: !factRow.modelData.personal
                            text: "облачной модели можно"
                            on: factRow.modelData.cloudOk
                            onClicked: Memory.setFlag(factRow.modelData.id, "cloud_ok", !on)
                        }
                        Toggle {
                            text: "изменить"
                            onClicked: { factEdit.text = factRow.modelData.text; factRow.editing = true; factEdit.forceActiveFocus() }
                        }
                    }
                }
                IconButton {
                    id: trash
                    anchors.right: parent.right
                    anchors.rightMargin: 6
                    y: 6
                    iconName: "trash"
                    tip: "Забыть"
                    onClicked: Memory.deleteFact(factRow.modelData.id)
                }
            }
        }
    }

    SectionTitle { text: "Навыки — планы удачных задач" }
    Text {
        visible: Memory.skills.length === 0
        width: parent.width
        wrapMode: Text.Wrap
        text: "Пока нет. Когда задача агента достигнет цели, в её отчёте будет «Сохранить как навык»: похожая цель получит этот план подсказкой."
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Column {
        objectName: "memorySkills"
        width: parent.width
        spacing: 6
        Repeater {
            model: Memory.skills
            delegate: Rectangle {
                required property var modelData
                width: parent.width
                height: skillBody.height + 20
                radius: 6
                color: Theme.p.card
                border.color: Theme.p.hairline
                Column {
                    id: skillBody
                    x: 12
                    y: 10
                    width: parent.width - 60
                    spacing: 4
                    Text {
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: modelData.name
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        wrapMode: Text.Wrap
                        text: modelData.steps
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Text {
                        text: modelData.meta
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                IconButton {
                    anchors.right: parent.right
                    anchors.rightMargin: 6
                    y: 6
                    iconName: "trash"
                    tip: "Удалить навык"
                    onClicked: Memory.deleteSkill(modelData.id)
                }
            }
        }
    }
}
