import QtQuick
import QtQuick.Controls.Basic

// The project card's editor (D24): the model's draft or the confirmed card, edited by hand and confirmed as a new
// version; earlier versions can be brought back.
Sheet {
    id: sheet
    objectName: "cardSheet"
    title: qsTr("Карточка проекта «%1»").arg(Projects.currentName)
    iconName: "edit"
    width: Math.min(760, (parent ? parent.width : 960) * 0.92)
    property bool fromDraft: false
    readonly property var card: Projects.card  // one copy per change, not one per binding

    function openWith(text, draft) {
        fromDraft = draft
        editor.text = text
        open()
    }

    Text {
        width: parent.width
        wrapMode: Text.Wrap
        lineHeight: 1.3
        text: sheet.fromDraft
              ? qsTr("Черновик от модели «%1». Проверьте факты, поправьте и подтвердите — карточка станет тем, что модели узнают о проекте первым делом.").arg(sheet.card.draftModel)
              : qsTr("Сейчас — версия %1. Подтверждённая правка станет новой версией; старые остаются.").arg(sheet.card.version)
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Text {
        objectName: "cardChangedSections"
        visible: sheet.fromDraft && sheet.card.confirmed && sheet.card.changedSections.length > 0
        width: parent.width
        wrapMode: Text.Wrap
        text: qsTr("По сравнению с версией %1 изменены разделы: %2").arg(sheet.card.version).arg(sheet.card.changedSections.join(", "))
        color: Theme.p.accent
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Rectangle {
        width: parent.width
        height: 420
        radius: 6
        color: Theme.p.card
        border.color: editor.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            anchors.margins: 2
            ScrollBar.vertical: ThinScrollBar {}
            TextArea {
                id: editor
                objectName: "cardEditor"
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                selectionColor: Theme.p.selection
                selectedTextColor: Theme.p.text
                font.family: Theme.monoFont
                font.pixelSize: 13
                padding: 10
                background: null
            }
        }
    }

    Row {
        spacing: 8
        PlainButton {
            kind: "primary"
            iconName: "check"
            text: qsTr("Подтвердить")
            enabled: editor.text.trim() !== ""
            onClicked: { Projects.confirmCard(editor.text); sheet.close() }
        }
        PlainButton { text: qsTr("Отмена"); onClicked: sheet.close() }
        PlainButton {
            visible: sheet.fromDraft
            kind: "danger"
            text: qsTr("Выбросить черновик")
            onClicked: { Projects.dropCardDraft(); sheet.close() }
        }
    }

    SectionTitle { visible: sheet.card.versions.length > 0; text: qsTr("Версии") }
    Repeater {
        model: sheet.card.versions
        delegate: Item {
            id: version
            required property var modelData
            width: parent ? parent.width : 0
            height: 32
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: version.modelData.model_id
                      ? qsTr("версия %1 · %2 · черновик %3").arg(version.modelData.version).arg(version.modelData.when).arg(version.modelData.model_id)
                      : qsTr("версия %1 · %2 · вручную").arg(version.modelData.version).arg(version.modelData.when)
                color: version.modelData.version === sheet.card.version ? Theme.p.text : Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            PlainButton {
                visible: version.modelData.version !== sheet.card.version
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Вернуть")
                onClicked: { Projects.restoreCard(version.modelData.version); sheet.close() }
            }
        }
    }
}
