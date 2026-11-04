import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Установка моделей» (10.1, D37): local models from Hugging Face into llama-swap with one button. The curated list
// says for each model the variant this computer runs best and whether it fits the video memory; «Варианты» shows
// them all; the search finds any GGUF model; «Установки» shows the downloads, their pause and the trial run.
Sheet {
    id: sheet
    objectName: "installSheet"
    title: qsTr("Установка моделей")
    iconName: "download"
    width: Math.min(820, (parent ? parent.width : 960) * 0.94)

    // «Установить» - and once pressed, in its place: «Пауза» / «Продолжить» and ✕ to cancel (2026-10-28, the user's
    // word); an installed one says so
    component JobButtons: Row {
        id: jb
        property var job: ({})
        property string name
        property bool primary: false
        property bool can: true
        signal install()
        spacing: 6
        PlainButton {
            objectName: jb.name
            visible: !jb.job.id
            kind: jb.primary ? "primary" : "plain"
            iconName: "download"
            text: qsTr("Установить")
            enabled: !Installer.busy && jb.can
            onClicked: jb.install()
        }
        PlainButton {
            objectName: jb.name + "_" + (jb.job.primary || "")
            visible: !!jb.job.primary
            kind: jb.job.primary === "resume" ? "primary" : "plain"
            iconName: jb.job.primary === "pause" ? "pause" : "play"
            text: jb.job.primaryLabel || ""
            onClicked: Installer.act(jb.job.id, jb.job.primary)
        }
        IconButton {
            objectName: jb.name + "_" + (jb.job.dismiss || "")
            visible: !!jb.job.dismiss
            implicitWidth: 32
            implicitHeight: 32
            iconName: "close"
            tint: Theme.p.error
            hoverTint: Theme.p.error
            tip: jb.job.dismiss === "cancel" ? qsTr("Отменить установку") : qsTr("Убрать из списка")
            onClicked: Installer.act(jb.job.id, jb.job.dismiss)
        }
        Text {
            visible: !!jb.job.id && !jb.job.primary && !jb.job.dismiss
            text: jb.job.installed ? qsTr("✓ установлена") : (jb.job.line || "")
            color: jb.job.installed ? Theme.p.ok : Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    // how an install goes, under its row: a thin bar
    component JobBar: Rectangle {
        property var job: ({})
        visible: !!job.live
        height: 3
        radius: 1.5
        color: Theme.p.hairline
        Rectangle {
            width: parent.width * Math.max(0, Math.min(1, (parent.job.percent || 0) / 100))
            height: parent.height
            radius: 1.5
            color: parent.job.status === "paused" ? Theme.p.warn : Theme.p.accent
        }
    }
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
        options: [{ id: "catalog", name: qsTr("Подборка") }, { id: "search", name: qsTr("Поиск на Hugging Face") },
                  { id: "local", name: qsTr("На этом компьютере") }, { id: "jobs", name: Installer.activeCount ? qsTr("Установки (%1)").arg(Installer.activeCount) : qsTr("Установки") }]
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
            text: Installer.catalogLoading ? qsTr("Спрашиваю Hugging Face о моделях и прикидываю, что поместится…") : Installer.catalogError
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
                        Badge { visible: card.modelData.installed; text: qsTr("установлена"); tint: Theme.p.ok; anchors.verticalCenter: parent.verticalCenter }
                        Badge { visible: card.modelData.gated; text: qsTr("лицензия на сайте"); tint: Theme.p.warn; anchors.verticalCenter: parent.verticalCenter }
                    }
                    Note { text: card.modelData.about; color: Theme.p.text_soft }
                    Row {
                        spacing: 6
                        Rectangle { width: 8; height: 8; radius: 4; color: sheet.tone(card.modelData.tone); anchors.verticalCenter: parent.verticalCenter }
                        Text {
                            text: (card.modelData.offer ? card.modelData.offer + " — " : "")
                                  + (card.modelData.job.live ? card.modelData.job.line : card.modelData.words)
                            color: card.modelData.job.live ? Theme.p.accent : Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    JobBar { job: card.modelData.job; width: Math.min(parent.width, 360) }
                    Row {
                        spacing: 8
                        JobButtons {
                            name: "installRecommended_" + card.modelData.id
                            job: card.modelData.job
                            primary: true
                            visible: card.modelData.canInstall || !!card.modelData.job.id
                            onInstall: Installer.installRecommended(card.modelData.repo)
                        }
                        PlainButton {
                            text: qsTr("Варианты…")
                            onClicked: Installer.openRepo(card.modelData.repo)
                        }
                        Text {
                            visible: card.modelData.license !== ""
                            text: qsTr("лицензия %1").arg(card.modelData.license)
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
            text: qsTr("Любая модель в формате GGUF для llama.cpp. Подборка выше проверена; здесь — всё подряд, включая модели со снятыми ограничениями (они помечены): решайте сами, чему доверять.")
            color: Theme.p.faint
        }
        Field {
            objectName: "installQuery"
            width: parent.width
            placeholderText: qsTr("Название модели, например qwen coder")
            onAccepted: Installer.search(text)
        }
        Note {
            visible: Installer.searching || Installer.searchError !== ""
            text: Installer.searching ? qsTr("Ищу на Hugging Face…") : Installer.searchError
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

    // ---------- the models already on this computer (15.1) ----------
    Column {
        objectName: "installLocal"
        visible: Installer.repoName === "" && Installer.tab === "local"
        width: parent.width
        spacing: 8
        Note { text: Installer.scanText; color: Theme.p.faint }
        Row {
            spacing: 8
            PlainButton {
                objectName: "scanDrives"
                visible: !Installer.scanning
                iconName: "search"
                text: qsTr("Искать на всех дисках")
                onClicked: Installer.scanLocal()
            }
            PlainButton {
                visible: Installer.scanning
                text: qsTr("Остановить поиск")
                onClicked: Installer.stopScan()
            }
            PlainButton {
                objectName: "addModelFile"
                iconName: "folder"
                text: qsTr("Добавить файл…")
                onClicked: modelFile.open()
            }
        }
        FileDialog {
            id: modelFile
            title: qsTr("Файл модели GGUF")
            fileMode: FileDialog.OpenFile
            nameFilters: [qsTr("Модели GGUF (*.gguf)")]
            onAccepted: Installer.addFile(selectedFile.toString())
        }
        Repeater {
            model: Installer.localModels
            delegate: Rectangle {
                id: local
                required property var modelData
                width: parent ? parent.width : 600
                height: localColumn.height + 16
                radius: 6
                color: "transparent"
                border.color: Theme.p.hairline
                Column {
                    id: localColumn
                    x: 12
                    y: 8
                    width: parent.width - 24 - localAction.width - 12
                    spacing: 2
                    Text {
                        width: parent.width
                        elide: Text.ElideRight
                        text: local.modelData.name
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                    }
                    Row {
                        spacing: 6
                        Text {
                            text: local.modelData.note
                            color: Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                        Text {
                            visible: local.modelData.words !== ""
                            text: "· " + local.modelData.words
                            color: local.modelData.tone === "ok" ? Theme.p.ok : local.modelData.tone === "error"
                                   ? Theme.p.error : Theme.p.warn
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                    }
                    Text {
                        width: parent.width
                        elide: Text.ElideMiddle
                        text: local.modelData.path
                        color: Theme.p.faint
                        font.family: Theme.uiFont
                        font.pixelSize: 11
                    }
                }
                Item {
                    id: localAction
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(addButton.implicitWidth, inConfig.implicitWidth)
                    height: addButton.implicitHeight
                    PlainButton {
                        id: addButton
                        objectName: "addLocal_" + local.modelData.name
                        anchors.right: parent.right
                        visible: local.modelData.inConfig === ""
                        text: qsTr("Добавить")
                        onClicked: Installer.addLocal(local.modelData.path, local.modelData.projector)
                    }
                    Text {
                        id: inConfig
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        visible: local.modelData.inConfig !== ""
                        text: qsTr("в llama-swap: %1").arg(local.modelData.inConfig)
                        color: Theme.p.ok
                        font.family: Theme.uiFont
                        font.pixelSize: 12
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
            PlainButton { iconName: "back"; text: qsTr("Назад"); onClicked: Installer.closeRepo() }
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
            text: Installer.repoLoading ? qsTr("Смотрю файлы модели и прикидываю память…") : Installer.repoError
            color: Installer.repoError !== "" ? Theme.p.error : Theme.p.faint
        }
        Note { visible: Installer.repo.notes !== undefined; text: Installer.repo.notes || "" }
        Note {
            visible: !!Installer.repo.unrestricted
            text: qsTr("У этой модели сняты ограничения (abliterated / uncensored): она не откажется от опасных просьб. Ставьте, только если понимаете зачем.")
            color: Theme.p.warn
        }
        Note {
            visible: !!Installer.repo.gated
            text: qsTr("Модель закрыта: примите её лицензию на странице модели на huggingface.co и добавьте токен в «Подключениях».")
            color: Theme.p.warn
        }
        Segmented {
            objectName: "installProjector"
            visible: !!Installer.repo.projector
            width: parent.width
            options: [{ id: "on", name: Installer.repo.projectorText || "" }, { id: "off", name: qsTr("Без картинок") }]
            value: Installer.projector ? "on" : "off"
            onPicked: (id) => Installer.setProjector(id === "on")
        }
        Text {
            text: qsTr("Куда сохранить")
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
                placeholderText: qsTr("Или своя папка, например F:\\Модели (необязательно)")
                text: Installer.folder
                onEditingFinished: Installer.setFolder(text)
            }
            PlainButton {
                id: browse
                objectName: "installBrowse"
                iconName: "folder"
                text: qsTr("Обзор…")
                onClicked: folderPick.open()
            }
        }
        FolderDialog {
            id: folderPick
            title: qsTr("Папка для моделей")
            onAccepted: Installer.setFolder(selectedFolder.toString())
        }
        Text {
            objectName: "installTarget"
            visible: Installer.target !== ""
            width: parent.width
            wrapMode: Text.WrapAnywhere
            text: qsTr("Сохранится в: %1").arg(Installer.target)
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
                                  + (variant.modelData.recommended ? qsTr("  — рекомендую")
                                     : variant.modelData.pick !== "" ? "  — " + variant.modelData.pick : "")
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                        }
                        Text {
                            width: parent.width
                            elide: Text.ElideRight
                            text: variant.modelData.job.live || variant.modelData.job.status === "failed"
                                  ? variant.modelData.job.line
                                  : [variant.modelData.words, variant.modelData.bits].filter(s => s).join(" · ")
                            color: variant.modelData.job.live ? Theme.p.accent
                                   : variant.modelData.job.status === "failed" ? Theme.p.error : Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                    }
                }
                JobButtons {
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    name: "installVariant_" + variant.modelData.quant
                    job: variant.modelData.job
                    primary: variant.modelData.recommended
                    can: variant.modelData.tone !== "error"
                    onInstall: Installer.install(variant.modelData.quant)
                }
                JobBar {
                    job: variant.modelData.job
                    x: 12
                    width: parent.width - 24
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 4
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
            text: qsTr("Установок пока нет. Выберите модель в «Подборке».")
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
