import QtQuick
import QtQuick.Controls.Basic

// The models' settings (D39): for the chosen model - shown in the picker or not, its own name, what it can do, its
// prices, the answer's parameters its provider takes (with the presets), the default reasoning depth and a system
// prompt; «Все модели» - the general system prompt. Save makes a new version; an earlier one comes back from the list.
Sheet {
    id: sheet
    objectName: "modelsSheet"
    title: "Модели"
    iconName: "sliders"
    width: Math.min(760, (parent ? parent.width : 960) * 0.92)

    function openFor(modelId) { ModelSetup.open(modelId); open() }

    component Label: Text {
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Note: Text {
        width: parent ? parent.width : 400
        wrapMode: Text.Wrap
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    // a text field with its label over it: the label stays when a value is typed
    component Labelled: Column {
        id: labelled
        property string fieldId
        property string label
        property string hint
        property alias field: input
        width: 170
        spacing: 4
        Label { text: labelled.label }
        Field {
            id: input
            objectName: "field_" + labelled.fieldId
            width: parent.width
            placeholderText: labelled.hint
            text: ModelSetup.draft[labelled.fieldId] || ""
            onEditingFinished: ModelSetup.setField(labelled.fieldId, text)
        }
    }
    component Prompt: Rectangle {
        id: prompt
        property string fieldId
        property string hint
        height: 110
        radius: 6
        color: Theme.p.card
        border.color: area.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            TextArea {
                id: area
                objectName: "area_" + prompt.fieldId
                placeholderText: prompt.hint
                placeholderTextColor: Theme.p.faint
                text: ModelSetup.draft[prompt.fieldId] || ""
                wrapMode: TextArea.Wrap
                selectByMouse: true
                color: Theme.p.text
                selectionColor: Theme.p.selection
                font.family: Theme.uiFont
                font.pixelSize: 13
                padding: 10
                background: null
                onEditingFinished: ModelSetup.setField(prompt.fieldId, text)
            }
        }
    }

    // ---------- which model ----------
    Row {
        width: parent.width
        spacing: 10
        PlainButton {
            id: chooser
            objectName: "chooseModelToSet"
            iconName: ModelSetup.general ? "layers" : "cpu"
            text: ModelSetup.title + "  ▾"
            onClicked: { menu.anchor = chooser; menu.open() }
        }
        Text {
            width: parent.width - chooser.width - 10
            anchors.verticalCenter: parent.verticalCenter
            text: ModelSetup.subtitle
            elide: Text.ElideRight
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }
    MenuPopup {
        id: menu
        menuWidth: 460
        items: ModelSetup.choices
        onChosen: (id) => ModelSetup.open(id)
        y: anchor ? anchor.mapToItem(parent, 0, 0).y + anchor.height + 6 : 0
    }

    // ---------- the general row ----------
    Column {
        visible: ModelSetup.general
        width: parent.width
        spacing: 8
        Note { text: "Этот промпт получает каждая модель хаба — в чатах, у агентов, в прокси OpenAI. После него идут промпт модели, инструкции чата и роль агента." }
        Prompt { width: parent.width; fieldId: "system_prompt"; hint: "Например: «Отвечай по-русски, коротко; код — с пояснением в одну строку»" }

        SectionTitle { text: "Роли — какая модель что делает" }
        Repeater {
            model: ModelSetup.roleRows
            delegate: Item {
                required property var modelData
                width: parent.width
                height: 44
                Column {
                    width: parent.width - roleButton.width - 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 1
                    Text {
                        text: modelData.name
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Text {
                        width: parent.width
                        text: modelData.hint
                        elide: Text.ElideRight
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                PlainButton {
                    id: roleButton
                    objectName: "role_" + modelData.id
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: 240
                    iconName: modelData.model ? "cpu" : ""
                    text: modelData.modelName
                    onClicked: { roleMenu.role = modelData.id; roleMenu.anchor = roleButton; roleMenu.open() }
                }
            }
        }
    }
    MenuPopup {
        id: roleMenu
        property string role
        menuWidth: 340
        items: ModelSetup.roleChoices
        onChosen: (id) => ModelSetup.setRole(role, id)
    }

    // ---------- a model ----------
    Column {
        visible: !ModelSetup.general
        width: parent.width
        spacing: 12

        SectionTitle { text: "В выборе модели" }
        Row {
            spacing: 12
            Segmented {
                objectName: "modelShown"
                options: [{ id: "shown", name: "Показывать" }, { id: "hidden", name: "Скрыть" }]
                value: ModelSetup.draft.hidden ? "hidden" : "shown"
                onPicked: (id) => ModelSetup.setFlag("hidden", id === "hidden")
            }
            Field {
                objectName: "field_name"
                width: 300
                placeholderText: "Своё имя (иначе — как у поставщика)"
                text: ModelSetup.draft.name || ""
                onEditingFinished: ModelSetup.setField("name", text)
            }
        }

        SectionTitle { text: "Возможности — если поставщик сообщает неверно" }
        Flow {
            width: parent.width
            spacing: 12
            Labelled { fieldId: "context"; label: "Контекст, токенов"; hint: "как у поставщика" }
            Column {
                spacing: 4
                Label { text: "Инструменты" }
                Segmented {
                    options: [{ id: "", name: "как есть" }, { id: "yes", name: "да" }, { id: "no", name: "нет" }]
                    value: ModelSetup.draft.tools || ""
                    onPicked: (id) => ModelSetup.setField("tools", id)
                }
            }
            Column {
                spacing: 4
                Label { text: "Картинки (зрение)" }
                Segmented {
                    options: [{ id: "", name: "как есть" }, { id: "yes", name: "да" }, { id: "no", name: "нет" }]
                    value: ModelSetup.draft.vision || ""
                    onPicked: (id) => ModelSetup.setField("vision", id)
                }
            }
        }

        SectionTitle { visible: !ModelSetup.local; text: "Цены, $ за 1 млн токенов — свои вместо поставщика (с датой)" }
        Flow {
            visible: !ModelSetup.local
            width: parent.width
            spacing: 12
            Repeater {
                model: ModelSetup.priceFields
                delegate: Labelled {
                    required property var modelData
                    width: 150
                    fieldId: modelData.id
                    label: modelData.name
                    hint: modelData.hint
                }
            }
        }

        SectionTitle { text: "Параметры ответа" }
        Note {
            visible: ModelSetup.accepts.length === 0
            text: "Поставщик этой модели не принимает параметров ответа — хаб их не отправит."
        }
        Row {
            visible: ModelSetup.accepts.indexOf("temperature") >= 0  // the presets are temperatures and samplers
            spacing: 10
            Label { text: "Заготовка"; anchors.verticalCenter: parent.verticalCenter }
            Segmented {
                objectName: "modelPreset"
                options: ModelSetup.presets
                value: ModelSetup.draft.preset || ""
                onPicked: (id) => ModelSetup.applyPreset(id)
            }
        }
        Flow {
            width: parent.width
            spacing: 12
            Repeater {
                model: ModelSetup.numberFields
                delegate: Labelled {
                    required property var modelData
                    width: 150
                    fieldId: modelData.id
                    label: modelData.name
                    hint: modelData.hint
                }
            }
        }
        Column {
            visible: ModelSetup.accepts.indexOf("stop") >= 0
            width: parent.width
            spacing: 4
            Label { text: "Стоп-последовательности — по одной на строку, до 4" }
            Prompt { width: parent.width; height: 64; fieldId: "stop"; hint: "Модель останавливается, дойдя до любой из них" }
        }
        Row {
            visible: ModelSetup.accepts.indexOf("thinking") >= 0
            spacing: 10
            Label { text: "Размышления"; anchors.verticalCenter: parent.verticalCenter }
            Segmented {
                objectName: "modelThinking"
                options: [{ id: "", name: "как у модели" }, { id: "on", name: "включены" }, { id: "off", name: "выключены" }]
                value: ModelSetup.draft.thinking || ""
                onPicked: (id) => ModelSetup.setField("thinking", id)
            }
        }
        Column {
            width: parent.width
            spacing: 4
            Label { text: "Глубина размышлений, когда выбираете эту модель" }
            Segmented {
                objectName: "modelEffort"
                width: parent.width
                options: ModelSetup.efforts
                value: ModelSetup.draft.effort || ""
                onPicked: (id) => ModelSetup.setField("effort", id)
            }
        }

        SectionTitle { text: "Системный промпт модели" }
        Prompt { width: parent.width; fieldId: "system_prompt"; hint: "Идёт после общего промпта и перед инструкциями чата" }
    }

    // ---------- saving, checking ----------
    Text {
        objectName: "modelSettingsError"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: ModelSetup.error
        color: Theme.p.error
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Row {
        spacing: 8
        PlainButton {
            objectName: "saveModelSettings"
            kind: "primary"
            iconName: "check"
            enabled: ModelSetup.dirty && !ModelSetup.loading
            text: "Сохранить"
            onClicked: ModelSetup.save()
        }
        PlainButton {
            visible: ModelSetup.dirty
            text: "Отменить изменения"
            onClicked: ModelSetup.discard()
        }
        PlainButton {
            objectName: "checkModel"
            visible: !ModelSetup.general
            enabled: !ModelSetup.checking
            iconName: "bolt"
            text: ModelSetup.dirty ? "Проверить (сохранённые)" : "Проверить"
            onClicked: ModelSetup.check()
        }
    }
    Text {
        objectName: "modelCheck"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: ModelSetup.checkText
        color: ModelSetup.checkText.indexOf("Не ") === 0 ? Theme.p.error : Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    SectionTitle { visible: ModelSetup.history.length > 0; text: "Версии" }
    Column {
        objectName: "modelVersions"
        width: parent.width
        spacing: 2
        Repeater {
            model: ModelSetup.history
            delegate: Item {
                required property var modelData
                width: parent.width
                height: 32
                Text {
                    width: parent.width - back.width - 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: "v" + modelData.version + " · " + modelData.when + " · " + modelData.what
                          + (modelData.current ? " · сейчас" : "")
                    elide: Text.ElideRight
                    color: modelData.current ? Theme.p.text : Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                PlainButton {
                    id: back
                    visible: !modelData.current
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitHeight: 26
                    text: "Вернуть"
                    onClicked: ModelSetup.revert(modelData.version)
                }
            }
        }
    }
}
