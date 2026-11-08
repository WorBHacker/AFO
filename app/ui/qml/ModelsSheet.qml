import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// The models' settings (D39): for the chosen model - shown in the picker or not, its own name, what it can do, its
// prices, the answer's parameters its provider takes (with the presets), the default reasoning depth and a system
// prompt; «Все модели» - the general system prompt. Save makes a new version; an earlier one comes back from the list.
Sheet {
    id: sheet
    objectName: "modelsSheet"
    title: qsTr("Модели")
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
    // a launch setting of llama-swap's config: the same field, its value from the launch draft
    component LaunchField: Column {
        id: launchField
        property string fieldId
        property string label
        property string hint
        width: 150
        spacing: 4
        Label { text: launchField.label }
        Field {
            objectName: "launch_" + launchField.fieldId
            width: parent.width
            placeholderText: launchField.hint
            text: ModelSetup.launchDraft[launchField.fieldId] || ""
            onEditingFinished: ModelSetup.setLaunch(launchField.fieldId, text)
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
            maxWidth: parent.width * 0.6
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
        below: true
    }

    // ---------- the general row ----------
    Column {
        visible: ModelSetup.general
        width: parent.width
        spacing: 8
        Note { text: qsTr("Этот промпт получает каждая модель хаба — в чатах, у агентов, в прокси OpenAI. После него идут промпт модели, инструкции чата и роль агента.") }
        Prompt { width: parent.width; fieldId: "system_prompt"; hint: qsTr("Например: «Отвечай по-русски, коротко; код — с пояснением в одну строку»") }

        SectionTitle { text: qsTr("Роли — какая модель что делает") }
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
    Row {
        visible: ModelSetup.general
        spacing: 8
        PlainButton {
            objectName: "exportModelSettings"
            iconName: "external"
            text: qsTr("Сохранить настройки всех моделей в файл…")
            onClicked: exportDialog.open()
        }
        PlainButton {
            objectName: "importModelSettings"
            iconName: "folder"
            text: qsTr("Загрузить из файла…")
            onClicked: importDialog.open()
        }
    }
    FileDialog {
        id: exportDialog
        title: qsTr("Сохранить настройки моделей")
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: [qsTr("Настройки моделей (*.json)")]
        onAccepted: ModelSetup.exportTo(selectedFile.toString())
    }
    FileDialog {
        id: importDialog
        title: qsTr("Загрузить настройки моделей")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Настройки моделей (*.json)")]
        onAccepted: ModelSetup.importFrom(selectedFile.toString())
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

        SectionTitle { text: qsTr("В выборе модели") }
        Row {
            spacing: 12
            Segmented {
                objectName: "modelShown"
                options: [{ id: "shown", name: qsTr("Показывать") }, { id: "hidden", name: qsTr("Скрыть") }]
                value: ModelSetup.draft.hidden ? "hidden" : "shown"
                onPicked: (id) => ModelSetup.setFlag("hidden", id === "hidden")
            }
            Field {
                objectName: "field_name"
                width: 300
                placeholderText: qsTr("Своё имя (иначе — как у поставщика)")
                text: ModelSetup.draft.name || ""
                onEditingFinished: ModelSetup.setField("name", text)
            }
        }
        Row {  // 25.5: the user's word on its Russian - the advice and the cards' writer pass it by
            spacing: 12
            Segmented {
                objectName: "modelRussian"
                options: [{ id: "good", name: qsTr("По-русски пишет хорошо") },
                          { id: "weak", name: qsTr("С ошибками") }]
                value: ModelSetup.draft.weak_russian ? "weak" : "good"
                onPicked: (id) => ModelSetup.setFlag("weak_russian", id === "weak")
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                width: 300
                wrapMode: Text.Wrap
                text: qsTr("С ошибками — хаб не советует её для чатов и не берёт для карточек проектов")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }

        SectionTitle { text: qsTr("Возможности — если поставщик сообщает неверно") }
        Flow {
            width: parent.width
            spacing: 12
            Labelled { fieldId: "context"; label: qsTr("Контекст, токенов"); hint: qsTr("как у поставщика") }
            Column {
                spacing: 4
                Label { text: qsTr("Инструменты") }
                Segmented {
                    options: [{ id: "", name: qsTr("как есть") }, { id: "yes", name: qsTr("да") }, { id: "no", name: qsTr("нет") }]
                    value: ModelSetup.draft.tools || ""
                    onPicked: (id) => ModelSetup.setField("tools", id)
                }
            }
            Column {
                spacing: 4
                Label { text: qsTr("Картинки (зрение)") }
                Segmented {
                    options: [{ id: "", name: qsTr("как есть") }, { id: "yes", name: qsTr("да") }, { id: "no", name: qsTr("нет") }]
                    value: ModelSetup.draft.vision || ""
                    onPicked: (id) => ModelSetup.setField("vision", id)
                }
            }
        }

        SectionTitle { visible: !ModelSetup.local; text: qsTr("Цены, $ за 1 млн токенов — свои вместо поставщика (с датой)") }
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

        SectionTitle { text: qsTr("Параметры ответа") }
        Note {
            visible: ModelSetup.accepts.length === 0
            text: qsTr("Поставщик этой модели не принимает параметров ответа — хаб их не отправит.")
        }
        Row {
            visible: ModelSetup.accepts.indexOf("temperature") >= 0  // the presets are temperatures and samplers
            spacing: 10
            Label { text: qsTr("Заготовка"); anchors.verticalCenter: parent.verticalCenter }
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
            Label { text: qsTr("Стоп-последовательности — по одной на строку, до 4") }
            Prompt { width: parent.width; height: 64; fieldId: "stop"; hint: qsTr("Модель останавливается, дойдя до любой из них") }
        }
        Row {
            visible: ModelSetup.accepts.indexOf("thinking") >= 0
            spacing: 10
            Label { text: qsTr("Размышления"); anchors.verticalCenter: parent.verticalCenter }
            Segmented {
                objectName: "modelThinking"
                options: [{ id: "", name: qsTr("как у модели") }, { id: "on", name: qsTr("включены") }, { id: "off", name: qsTr("выключены") }]
                value: ModelSetup.draft.thinking || ""
                onPicked: (id) => ModelSetup.setField("thinking", id)
            }
        }
        // a Strata model whose abliteration is a vector applied while it runs: switched per answer (2026-10-28)
        Column {
            visible: ModelSetup.accepts.indexOf("refusal_vector") >= 0
            width: parent.width
            spacing: 4
            Row {
                spacing: 10
                Label { text: qsTr("Снятие отказов (abliterated)"); anchors.verticalCenter: parent.verticalCenter }
                Segmented {
                    objectName: "modelRefusalVector"
                    options: [{ id: "", name: qsTr("как у модели") }, { id: "on", name: qsTr("включено") }, { id: "off", name: qsTr("выключено") }]
                    value: ModelSetup.draft.refusal_vector || ""
                    onPicked: (id) => ModelSetup.setField("refusal_vector", id)
                }
            }
            Text {
                width: parent.width
                wrapMode: Text.Wrap
                text: qsTr("Strata накладывает на модель вектор, который убирает отказы отвечать. Выключено — обычная модель, как её выпустили: на некоторые темы может отказаться. «Как у модели» — включено. Модель не перезагружается; у новой задачи агента можно выбрать своё.")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
        Column {
            width: parent.width
            spacing: 4
            Label { text: qsTr("Глубина размышлений, когда выбираете эту модель") }
            Segmented {
                objectName: "modelEffort"
                width: parent.width
                options: ModelSetup.efforts
                value: ModelSetup.draft.effort || ""
                onPicked: (id) => ModelSetup.setField("effort", id)
            }
        }

        SectionTitle { text: qsTr("Системный промпт модели") }
        Prompt { width: parent.width; fieldId: "system_prompt"; hint: qsTr("Идёт после общего промпта и перед инструкциями чата") }

        // ---------- for the experienced: how much context the model gets ----------
        Text {
            objectName: "budgetsToggle"
            text: "<a href=\"x\">" + (budgets.visible ? "▾ " : "▸ ") + qsTr("Для опытных: бюджеты контекста и порции инструментов") + "</a>"
            textFormat: Text.StyledText
            linkColor: Theme.p.link
            font.family: Theme.uiFont
            font.pixelSize: 13
            onLinkActivated: budgets.visible = !budgets.visible
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
        Column {
            id: budgets
            objectName: "budgetFields"
            visible: false
            width: parent.width
            spacing: 8
            Note { text: qsTr("Сколько контекста модель получает с каждым сообщением (слои L0–L4) и сколько отдают за раз её инструменты. Пусто — как у всех моделей; облачным больше — дороже каждый шаг.") }
            Flow {
                width: parent.width
                spacing: 12
                Repeater {
                    model: ModelSetup.budgetFields
                    delegate: Labelled {
                        required property var modelData
                        width: 200
                        fieldId: modelData.id
                        label: modelData.name
                        hint: modelData.hint
                    }
                }
            }
        }

        // ---------- how llama-swap starts it (local models) ----------
        Column {
            objectName: "launchSection"
            visible: ModelSetup.launch.engine !== undefined
            width: parent.width
            spacing: 10
            SectionTitle { text: qsTr("Запуск в llama-swap — config.yaml") }
            Note {
                visible: ModelSetup.launch.engine === "strata"
                text: qsTr("Запускается через Strata по профилю %1 — его настройки меняет сама Strata. Здесь — только выгрузка по времени.").arg(ModelSetup.launch.profile || "")
            }
            Note {
                visible: ModelSetup.launch.editable === true
                text: "llama.cpp · " + (ModelSetup.launch.file || "") + (ModelSetup.launch.mmproj ? qsTr(" · с проектором картинок") : "")
            }
            Note {  // a block the window does not edit: a one-line command, or one the hub cannot read
                visible: !!ModelSetup.launch.problem
                text: ModelSetup.launch.problem || ""
            }
            Segmented {
                objectName: "launchPreset"
                visible: ModelSetup.launch.editable === true
                width: parent.width
                options: [{ id: "fast", name: qsTr("Быстрее") }, { id: "balanced", name: qsTr("Баланс") },
                          { id: "quality", name: qsTr("Качественнее") }]
                value: ModelSetup.launchPreset
                onPicked: (id) => ModelSetup.applyLaunchPreset(id)
            }
            Note {
                visible: ModelSetup.launch.editable === true
                text: qsTr("Быстрее — модель помнит последние ~8 тыс. токенов разговора и целиком на видеокарте; Баланс — ~32 тыс.; Качественнее — до ~64 тыс. и кэш без сжатия, но больше видеопамяти. Ниже — точные настройки и оценка, поместится ли.")
            }
            Flow {
                visible: ModelSetup.launch.editable === true
                width: parent.width
                spacing: 12
                LaunchField { fieldId: "context"; label: qsTr("Контекст, токенов"); hint: qsTr("например 32768") }
                LaunchField { fieldId: "parallel"; label: qsTr("Параллельных запросов"); hint: "1" }
                Column {
                    spacing: 4
                    Label { text: "Flash attention" }
                    Segmented {
                        options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }, { id: "auto", name: qsTr("авто") }]
                        value: ModelSetup.launchDraft.flash_attn || "auto"
                        onPicked: (id) => ModelSetup.setLaunch("flash_attn", id)
                    }
                }
            }
            Row {
                visible: ModelSetup.launch.editable === true
                spacing: 10
                Label { text: qsTr("Слои на видеокарте"); anchors.verticalCenter: parent.verticalCenter }
                Segmented {
                    objectName: "launchLayers"
                    options: [{ id: "all", name: qsTr("все") }, { id: "auto", name: qsTr("сколько поместится") }, { id: "n", name: qsTr("число") }]
                    value: ModelSetup.launchDraft.gpu_mode || "all"
                    onPicked: (id) => ModelSetup.setLaunch("gpu_mode", id)
                }
                Field {
                    visible: ModelSetup.launchDraft.gpu_mode === "n"
                    width: 90
                    placeholderText: qsTr("слоёв")
                    text: ModelSetup.launchDraft.gpu_n || ""
                    onEditingFinished: ModelSetup.setLaunch("gpu_n", text)
                }
            }
            Row {
                visible: ModelSetup.launch.editable === true
                spacing: 10
                Label { text: qsTr("Сжатие KV-кэша"); anchors.verticalCenter: parent.verticalCenter }
                Segmented {
                    objectName: "launchCache"
                    options: ModelSetup.cacheChoices
                    value: ModelSetup.launchDraft.cache_type || "f16"
                    onPicked: (id) => ModelSetup.setLaunch("cache_type", id)
                }
            }
            LaunchField { fieldId: "ttl_min"; label: qsTr("Выгружать без запросов, мин"); hint: qsTr("0 — никогда"); width: 210 }
            // launch settings by measurement (15.2): llama-bench of the same build, the fastest written and checked
            Column {
                objectName: "tuneSection"
                visible: ModelSetup.launch.editable === true
                width: parent.width
                spacing: 6
                Label { text: qsTr("Подбор по замерам") }
                Text {
                    objectName: "tuneText"
                    width: parent.width
                    wrapMode: Text.Wrap
                    text: ModelSetup.tuneText
                    color: ModelSetup.tuneTone === "error" ? Theme.p.error : ModelSetup.tuneTone === "ok" ? Theme.p.ok : Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Row {
                    spacing: 8
                    PlainButton {
                        objectName: "tuneModel"
                        enabled: !ModelSetup.tuneBusy && !ModelSetup.launchDirty
                        iconName: "activity"
                        text: qsTr("Подобрать по замерам")
                        onClicked: ModelSetup.tune()
                    }
                    PlainButton {
                        enabled: !ModelSetup.tuneBusy
                        text: qsTr("Для всех моделей")
                        onClicked: ModelSetup.tuneAll()
                    }
                    PlainButton {
                        visible: ModelSetup.tuneBusy
                        text: qsTr("Остановить")
                        onClicked: ModelSetup.tuneCancel()
                    }
                }
                Row {
                    spacing: 8
                    Text {
                        text: qsTr("Подбирать сами — после установки модели и обновления llama.cpp")
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Segmented {
                        objectName: "tuneAuto"
                        options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }]
                        value: ModelSetup.tuneAuto ? "on" : "off"
                        onPicked: (id) => ModelSetup.setTuneAuto(id === "on")
                    }
                }
            }
            Text {
                objectName: "launchEstimate"
                visible: text !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: ModelSetup.launchEstimate
                color: ModelSetup.launchEstimateTone === "error" ? Theme.p.error : ModelSetup.launchEstimateTone === "warn" ? Theme.p.warn : Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
            Row {
                spacing: 8
                PlainButton {
                    objectName: "saveLaunch"
                    kind: "primary"
                    iconName: "check"
                    enabled: ModelSetup.launchDirty && !ModelSetup.launchBusy
                    text: qsTr("Записать в config.yaml")
                    onClicked: ModelSetup.saveLaunch()
                }
                PlainButton {
                    objectName: "trialLaunch"
                    enabled: !ModelSetup.launchBusy
                    iconName: "bolt"
                    text: qsTr("Проверить запуск")
                    onClicked: ModelSetup.trialLaunch()
                }
            }
            Text {
                objectName: "launchStatus"
                visible: text !== ""
                width: parent.width
                wrapMode: Text.Wrap
                text: ModelSetup.launchStatus
                color: ModelSetup.launchFailed ? Theme.p.error : Theme.p.text_soft
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
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
            text: qsTr("Сохранить")
            onClicked: ModelSetup.save()
        }
        PlainButton {
            visible: ModelSetup.dirty
            text: qsTr("Отменить изменения")
            onClicked: ModelSetup.discard()
        }
        PlainButton {
            objectName: "checkModel"
            visible: !ModelSetup.general
            enabled: !ModelSetup.checking
            iconName: "bolt"
            text: ModelSetup.dirty ? qsTr("Проверить (сохранённые)") : qsTr("Проверить")
            onClicked: ModelSetup.check()
        }
    }
    Text {
        objectName: "modelCheck"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: ModelSetup.checkText
        color: ModelSetup.checkFailed ? Theme.p.error : Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    SectionTitle { visible: ModelSetup.history.length > 0; text: qsTr("Версии") }
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
                          + (modelData.current ? qsTr(" · сейчас") : "")
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
                    text: qsTr("Вернуть")
                    onClicked: ModelSetup.revert(modelData.version)
                }
            }
        }
    }
}
