import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Установка моделей» (10.1, D37): local models from Hugging Face into llama-swap with one button. The curated list
// says for each model the variant this computer runs best and whether it fits the video memory; «Варианты» shows
// them all; the search finds any GGUF model; «Установки» shows the downloads, their pause and the trial run.
Sheet {
    id: sheet
    objectName: "installSheet"
    title: "Установка моделей"
    iconName: "download"
    width: Math.min(820, (parent ? parent.width : 960) * 0.94)
    onAboutToShow: Installer.load()

    function tone(t) { return t === "ok" ? Theme.p.ok : t === "warn" ? Theme.p.warn : t === "error" ? Theme.p.error : Theme.p.muted }

    component Note: Text {
        width: parent ? parent.width : 600
        wrapMode: Text.Wrap
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 12
        lineHeight: 1.25
    }
    component Badge: Rectangle {
        property alias text: badgeText.text
        property color tint: Theme.p.muted
        width: badgeText.implicitWidth + 12
        height: 20
        radius: 4
        color: "transparent"
        border.color: tint
        Text {
            id: badgeText
            anchors.centerIn: parent
            color: parent.tint
            font.family: Theme.uiFont
            font.pixelSize: 11
        }
    }

    Note {
        objectName: "installSystem"
        text: Installer.systemText
    }
    Note {
        visible: Installer.problem !== ""
        text: Installer.problem
        color: Theme.p.error
    }

    Segmented {
        objectName: "installTabs"
        visible: Installer.repoName === ""
        width: parent.width
        options: [{ id: "catalog", name: "Подборка" }, { id: "search", name: "Поиск на Hugging Face" },
                  { id: "jobs", name: Installer.activeCount ? "Установки (" + Installer.activeCount + ")" : "Установки" }]
        value: Installer.tab
        onPicked: (id) => Installer.setTab(id)
    }

    // ---------- the curated list ----------
    Column {
        visible: Installer.repoName === "" && Installer.tab === "catalog"
        width: parent.width
        spacing: 10
        Segmented {
            width: parent.width
            options: Installer.purposes
            value: Installer.purpose
            onPicked: (id) => Installer.setPurpose(id)
        }
        Note {
            visible: Installer.catalogLoading || Installer.catalogError !== ""
            text: Installer.catalogLoading ? "Спрашиваю Hugging Face о моделях и прикидываю, что поместится…" : Installer.catalogError
            color: Installer.catalogError !== "" ? Theme.p.error : Theme.p.faint
        }
        Repeater {
            model: Installer.catalog
            delegate: Rectangle {
                id: card
                required property var modelData
                width: parent ? parent.width : 600
                height: cardColumn.height + 24
                radius: 8
                color: Theme.p.card
                border.color: Theme.p.hairline
                Column {
                    id: cardColumn
                    x: 14
                    y: 12
                    width: parent.width - 28
                    spacing: 6
                    Row {
                        width: parent.width
                        spacing: 8
                        Text {
                            text: card.modelData.name
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            text: card.modelData.purposes
                            color: Theme.p.muted
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Badge { visible: card.modelData.installed; text: "установлена"; tint: Theme.p.ok; anchors.verticalCenter: parent.verticalCenter }
                        Badge { visible: card.modelData.gated; text: "лицензия на сайте"; tint: Theme.p.warn; anchors.verticalCenter: parent.verticalCenter }
                    }
                    Note { text: card.modelData.about; color: Theme.p.text_soft }
                    Row {
                        spacing: 6
                        Rectangle { width: 8; height: 8; radius: 4; color: sheet.tone(card.modelData.tone); anchors.verticalCenter: parent.verticalCenter }
                        Text {
                            text: (card.modelData.offer ? card.modelData.offer + " — " : "") + card.modelData.words
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 8
                        PlainButton {
                            objectName: "installRecommended_" + card.modelData.id
                            kind: "primary"
                            iconName: "download"
                            text: "Установить"
                            visible: card.modelData.canInstall
                            enabled: !Installer.busy
                            onClicked: Installer.installRecommended(card.modelData.repo)
                        }
                        PlainButton {
                            text: "Варианты…"
                            onClicked: Installer.openRepo(card.modelData.repo)
                        }
                        Text {
                            visible: card.modelData.license !== ""
                            text: "лицензия " + card.modelData.license
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
            }
        }
    }

    // ---------- the search ----------
    Column {
        visible: Installer.repoName === "" && Installer.tab === "search"
        width: parent.width
        spacing: 8
        Note {
            text: "Любая модель в формате GGUF для llama.cpp. Подборка выше проверена; здесь — всё подряд, включая "
                  + "модели со снятыми ограничениями (они помечены): решайте сами, чему доверять."
            color: Theme.p.faint
        }
        Field {
            objectName: "installQuery"
            width: parent.width
            placeholderText: "Название модели, например qwen coder"
            onAccepted: Installer.search(text)
        }
        Note {
            visible: Installer.searching || Installer.searchError !== ""
            text: Installer.searching ? "Ищу на Hugging Face…" : Installer.searchError
            color: Installer.searchError !== "" ? Theme.p.error : Theme.p.faint
        }
        Repeater {
            model: Installer.found
            delegate: Rectangle {
                id: hit
                required property var modelData
                width: parent ? parent.width : 600
                height: 48
                radius: 6
                color: hitArea.containsMouse ? Theme.p.item_hover : "transparent"
                border.color: Theme.p.hairline
                MouseArea {
                    id: hitArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: Installer.openRepo(hit.modelData.repo)
                }
                Column {
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 24
                    spacing: 2
                    Row {
                        spacing: 8
                        Text {
                            text: hit.modelData.repo
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Repeater {
                            model: hit.modelData.badges
                            delegate: Badge {
                                required property var modelData
                                text: modelData
                                tint: Theme.p.warn
                            }
                        }
                    }
                    Text {
                        text: hit.modelData.note
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
            }
        }
    }

    // ---------- one repository's variants ----------
    Column {
        objectName: "installRepo"
        visible: Installer.repoName !== ""
        width: parent.width
        spacing: 10
        Row {
            spacing: 8
            PlainButton { iconName: "back"; text: "Назад"; onClicked: Installer.closeRepo() }
            Text {
                text: Installer.repoName
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 14
                font.weight: Font.DemiBold
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Note {
            visible: Installer.repoLoading || Installer.repoError !== ""
            text: Installer.repoLoading ? "Смотрю файлы модели и прикидываю память…" : Installer.repoError
            color: Installer.repoError !== "" ? Theme.p.error : Theme.p.faint
        }
        Note { visible: Installer.repo.notes !== undefined; text: Installer.repo.notes || "" }
        Note {
            visible: !!Installer.repo.unrestricted
            text: "У этой модели сняты ограничения (abliterated / uncensored): она не откажется от опасных просьб. Ставьте, только если понимаете зачем."
            color: Theme.p.warn
        }
        Note {
            visible: !!Installer.repo.gated
            text: "Модель закрыта: примите её лицензию на странице модели на huggingface.co и добавьте токен в «Подключениях»."
            color: Theme.p.warn
        }
        Segmented {
            objectName: "installProjector"
            visible: !!Installer.repo.projector
            width: parent.width
            options: [{ id: "on", name: Installer.repo.projectorText || "" }, { id: "off", name: "Без картинок" }]
            value: Installer.projector ? "on" : "off"
            onPicked: (id) => Installer.setProjector(id === "on")
        }
        Text {
            text: "Куда сохранить"
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Segmented {
            objectName: "installPlaces"
            width: parent.width
            options: Installer.drives
            value: Installer.folder === "" ? (Installer.drives.find(d => d.selected) || {}).id || "" : ""
            onPicked: (id) => Installer.setDrive(id)
        }
        Row {
            width: parent.width
            spacing: 8
            Field {
                id: ownFolder
                objectName: "installFolder"
                width: parent.width - browse.width - parent.spacing
                placeholderText: "Или своя папка, например F:\\Модели (необязательно)"
                text: Installer.folder
                onEditingFinished: Installer.setFolder(text)
            }
            PlainButton {
                id: browse
                objectName: "installBrowse"
                iconName: "folder"
                text: "Обзор…"
                onClicked: folderPick.open()
            }
        }
        FolderDialog {
            id: folderPick
            title: "Папка для моделей"
            onAccepted: Installer.setFolder(selectedFolder.toString())
        }
        Text {
            objectName: "installTarget"
            visible: Installer.target !== ""
            width: parent.width
            wrapMode: Text.WrapAnywhere
            text: "Сохранится в: " + Installer.target
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Repeater {
            model: Installer.variants
            delegate: Rectangle {
                id: variant
                required property var modelData
                width: parent ? parent.width : 600
                height: 50
                radius: 6
                color: variant.modelData.recommended ? Theme.p.item_selected : "transparent"
                border.color: variant.modelData.recommended ? Theme.p.accent : Theme.p.hairline
                Row {
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10
                    Rectangle { width: 8; height: 8; radius: 4; color: sheet.tone(variant.modelData.tone); anchors.verticalCenter: parent.verticalCenter }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: variant.width - 150
                        spacing: 2
                        Text {
                            text: variant.modelData.quant + " · " + variant.modelData.size
                                  + (variant.modelData.recommended ? "  — рекомендую"
                                     : variant.modelData.pick !== "" ? "  — " + variant.modelData.pick : "")
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            text: [variant.modelData.words, variant.modelData.bits].filter(s => s).join(" · ")
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                    }
                }
                PlainButton {
                    objectName: "installVariant_" + variant.modelData.quant
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    kind: variant.modelData.recommended ? "primary" : "plain"
                    iconName: "download"
                    text: "Установить"
                    enabled: !Installer.busy && variant.modelData.tone !== "error"
                    onClicked: Installer.install(variant.modelData.quant)
                }
            }
        }
    }

    // ---------- the installs ----------
    Column {
        visible: Installer.repoName === "" && Installer.tab === "jobs"
        width: parent.width
        spacing: 8
        Note {
            visible: Installer.jobs.length === 0
            text: "Установок пока нет. Выберите модель в «Подборке»."
            color: Theme.p.faint
        }
        Repeater {
            model: Installer.jobs
            delegate: Rectangle {
                id: job
                required property var modelData
                width: parent ? parent.width : 600
                height: jobColumn.height + 20
                radius: 8
                color: Theme.p.card
                border.color: Theme.p.hairline
                Column {
                    id: jobColumn
                    x: 14
                    y: 10
                    width: parent.width - 28
                    spacing: 6
                    Row {
                        spacing: 8
                        Text {
                            text: job.modelData.name
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                            font.weight: Font.DemiBold
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Badge {
                            text: job.modelData.statusWords
                            tint: job.modelData.status === "done" ? Theme.p.ok : job.modelData.status === "failed"
                                  ? Theme.p.error : job.modelData.live ? Theme.p.accent : Theme.p.muted
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Rectangle {
                        visible: job.modelData.progress !== ""
                        width: parent.width
                        height: 6
                        radius: 3
                        color: Theme.p.hairline
                        Rectangle {
                            width: parent.width * Math.min(job.modelData.percent, 100) / 100
                            height: parent.height
                            radius: 3
                            color: job.modelData.status === "downloading" ? Theme.p.accent : Theme.p.muted
                        }
                    }
                    Note { visible: job.modelData.progress !== ""; text: job.modelData.progress; color: Theme.p.faint }
                    Note { visible: job.modelData.trial !== ""; text: job.modelData.trial; color: Theme.p.ok }
                    Note { visible: job.modelData.error !== ""; text: job.modelData.error; color: Theme.p.error }
                    Row {
                        spacing: 8
                        Repeater {
                            model: job.modelData.actions
                            delegate: PlainButton {
                                required property var modelData
                                objectName: "installAct_" + modelData.id + "_" + job.modelData.id
                                kind: modelData.kind
                                text: modelData.label
                                onClicked: Installer.act(job.modelData.id, modelData.id)
                            }
                        }
                    }
                }
            }
        }
    }
}
