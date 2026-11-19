import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Режиссёр» (stage 50.9): a film of scenes, in the Studio beside «Вручную». Left: the product's photo, the idea, the
// length, the shape, «Собрать сценарий»; the centre: the scenes' cards - the clip's first frame, what happens,
// «Изменить», «Переснять» -, the player (a scene's clip or the joined film), «Снять всё», «Собрать ролик». The hub
// makes the film (hub/director.py); Films (app/ui/film_view.py) asks and shows. (53.6) «Звук фильма…»: the sheet of
// the sound added at the join - the narrator, the music, the parts of the engine of sound.
Item {
    id: director
    objectName: "directorView"
    readonly property bool narrow: width < 860
    onVisibleChanged: if (visible) Films.load()

    component Note: Text {
        width: parent ? parent.width : 300
        wrapMode: Text.Wrap
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 12
        lineHeight: 1.25
    }
    component Label: Text {
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
        font.weight: Font.DemiBold
    }
    component Area: Rectangle {  // a text of several lines
        id: area
        property alias text: edit.text
        property alias placeholder: edit.placeholderText
        property alias edit: edit
        radius: 6
        color: Theme.p.card
        border.color: edit.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
        ScrollView {
            anchors.fill: parent
            anchors.margins: 2
            TextArea {
                id: edit
                wrapMode: TextEdit.Wrap
                placeholderTextColor: Theme.p.faint
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 13
                background: null
            }
        }
    }

    FileDialog {
        id: photoDialog
        title: qsTr("Фото товара")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")]
        onAccepted: Films.choosePhoto(selectedFile.toString())
    }
    FileDialog {
        id: saveDialog
        title: qsTr("Сохранить ролик")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Видео MP4 (*.mp4)")]
        defaultSuffix: "mp4"
        onAccepted: Films.saveAs(selectedFile.toString())
    }
    MenuPopup {  // the films: a new one, then the newest first
        id: filmMenu
        menuWidth: 340
        below: true
        items: Films.filmChoices
        onChosen: (id) => Films.pick(id)
    }
    FileDialog {  // 53.6: the user's own music
        id: musicDialog
        title: qsTr("Файл музыки")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Звук (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus)")]
        onAccepted: Films.chooseMusic(selectedFile.toString())
    }
    MenuPopup {  // 53.6: the narrator's voices, «Без диктора» first
        id: voiceMenu
        objectName: "voiceMenu"
        menuWidth: 300
        below: true
        items: Films.voiceChoices
        onChosen: (id) => Films.setVoice(id)
    }

    // 53.6: the sound added to the film when it is joined - the narrator, the music - and the parts of the engine
    // of sound that make it: each fetched once, by a click here. What is chosen goes to the hub at once; the
    // music's description - when the sheet closes.
    Popup {
        id: soundSheet
        objectName: "soundSheet"

        function openUp() {
            musicWords.text = Films.musicWords
            open()
        }

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, parent ? parent.width - 40 : 560)
        padding: 20
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        onClosed: Films.setMusicWords(musicWords.text)
        Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
        background: Rectangle {
            radius: 10
            color: Theme.p.popup
            border.color: Theme.p.hairline_strong
            SoftShadow { corner: 10 }
        }
        contentItem: Column {
            spacing: 10
            Text {
                text: qsTr("Звук фильма")
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            Label { text: qsTr("Диктор") }
            Chip {
                id: voiceChip
                objectName: "filmVoice"
                text: Films.voiceName
                enabled: !Films.busy
                onClicked: { voiceMenu.anchor = voiceChip; voiceMenu.open() }
            }
            Note {
                width: soundSheet.availableWidth
                text: qsTr("Текст диктора пишет режиссёр вместе со сценарием — по реплике на сцену, на языке идеи. У готового сценария впишите реплики сами («Изменить» у сцены) или перепишите сценарий.")
                color: Theme.p.faint
            }
            Label { text: qsTr("Музыка") }
            Segmented {
                objectName: "filmMusic"
                width: soundSheet.availableWidth
                enabled: !Films.busy
                options: Films.musics
                value: Films.music
                onPicked: (id) => {
                    if (id === "file" && !Films.hasMusicFile)
                        musicDialog.open()
                    else
                        Films.setMusic(id)
                }
            }
            Area {
                id: musicWords
                edit.objectName: "filmMusicWords"
                visible: Films.music === "made"
                enabled: !Films.busy
                width: soundSheet.availableWidth
                height: 48
                placeholder: qsTr("Какая музыка, по-английски: calm warm piano, slow. Пусто — режиссёр предложит сам вместе со сценарием")
            }
            Row {
                visible: Films.music === "file"
                spacing: 10
                Text {
                    objectName: "filmMusicFile"
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, soundSheet.availableWidth - 170)
                    elide: Text.ElideMiddle
                    text: Films.musicFileName
                    color: Theme.p.text_soft
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                PlainButton {
                    objectName: "filmMusicOther"
                    enabled: !Films.busy
                    text: qsTr("Другой файл…")
                    onClicked: musicDialog.open()
                }
            }
            Note {
                width: soundSheet.availableWidth
                text: qsTr("Музыка идёт под всем роликом и становится тише, пока говорит диктор. Свой файл короче ролика повторяется.")
                color: Theme.p.faint
            }
            Label { text: qsTr("Что для этого нужно") }
            Repeater {
                model: Films.soundParts
                delegate: Item {
                    id: part
                    required property var modelData
                    objectName: "soundPart_" + part.modelData.id
                    width: soundSheet.availableWidth
                    height: 38
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - partDo.width - 12
                        spacing: 2
                        Text {
                            width: parent.width
                            elide: Text.ElideRight
                            text: part.modelData.name + " — " + part.modelData.about
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                        Text {
                            objectName: "soundNote_" + part.modelData.id
                            width: parent.width
                            elide: Text.ElideRight
                            text: part.modelData.note
                            color: part.modelData.failed ? Theme.p.error : Theme.p.faint
                            font.family: Theme.uiFont
                            font.pixelSize: 11
                        }
                    }
                    Row {
                        id: partDo
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        PlainButton {
                            objectName: "soundGet_" + part.modelData.id
                            visible: part.modelData.state === "missing"
                            iconName: "download"
                            text: part.modelData.getText
                            onClicked: Films.getSound(part.modelData.id)
                        }
                        PlainButton {
                            objectName: "soundStop_" + part.modelData.id
                            visible: part.modelData.state === "loading"
                            kind: "danger"
                            iconName: "stop"
                            text: qsTr("Стоп")
                            onClicked: Films.stopSound(part.modelData.id)
                        }
                        IconButton {
                            objectName: "soundRemove_" + part.modelData.id
                            visible: part.modelData.state === "ready"
                            iconName: "trash"
                            tip: qsTr("Удалить с диска")
                            enabled: !Films.busy
                            onClicked: Films.removeSound(part.modelData.id)
                        }
                    }
                }
            }
            Note {
                width: soundSheet.availableWidth
                visible: !Films.soundEngine
                text: qsTr("С первой частью скачается и сама программа звука audio.cpp — до 1 ГБ. Звук делается на этом компьютере, в облако ничего не уходит.")
                color: Theme.p.faint
            }
            Row {
                anchors.right: parent.right
                PlainButton {
                    objectName: "soundDone"
                    kind: "primary"
                    text: qsTr("Готово")
                    onClicked: soundSheet.close()
                }
            }
        }
    }

    ImproveSheet {  // 54.6: «Улучшить…» of the joined film
        id: improveSheet
        objectName: "filmImproveSheet"
        prefix: "filmImprove"
    }

    // a scene changed by hand: what happens (the person's language), the description the video model films by, how
    // the scene begins, its length
    Popup {
        id: sceneSheet
        objectName: "sceneSheet"
        property int n: 0
        property string mode: ""
        property string length: ""
        property var modes: []

        function openFor(card) {
            n = card.n
            mode = card.mode
            length = card.length
            modes = Films.sceneModes(card.n)
            sceneWords.text = card.words
            scenePrompt.text = card.prompt
            sceneSound.text = card.sound
            sceneSay.text = card.say
            open()
        }

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(560, parent ? parent.width - 40 : 560)
        padding: 20
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        Overlay.modal: Rectangle { color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.03, 0.55) : Qt.rgba(0.1, 0.1, 0.12, 0.22) }
        background: Rectangle {
            radius: 10
            color: Theme.p.popup
            border.color: Theme.p.hairline_strong
            SoftShadow { corner: 10 }
        }
        contentItem: Column {
            spacing: 10
            Text {
                text: qsTr("Сцена %1").arg(sceneSheet.n)
                color: Theme.p.text
                font.family: Theme.uiFont
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            Label { text: qsTr("Что происходит") }
            Area {
                id: sceneWords
                edit.objectName: "sceneWords"
                width: sceneSheet.availableWidth
                height: 64
            }
            Label { text: qsTr("Описание для модели видео (по-английски)") }
            Area {
                id: scenePrompt
                edit.objectName: "scenePrompt"
                width: sceneSheet.availableWidth
                height: 110
            }
            Label { text: qsTr("Звук: что слышно (по-английски)"); visible: sceneSound.visible }
            Area {  // 52.6: a film with sound
                id: sceneSound
                edit.objectName: "sceneSound"
                visible: Films.canSound && Films.sound
                width: sceneSheet.availableWidth
                height: 48
            }
            Label { text: qsTr("Диктор: что говорит за кадром"); visible: sceneSay.visible }
            Area {  // 53.6: a film with a narrator
                id: sceneSay
                edit.objectName: "sceneSay"
                visible: Films.voice !== ""
                width: sceneSheet.availableWidth
                height: 48
                placeholder: qsTr("Пусто — диктор в этой сцене молчит. Около 11 знаков на секунду сцены")
            }
            Label { text: qsTr("Как начинается") }
            Segmented {
                objectName: "sceneMode"
                width: sceneSheet.availableWidth
                options: sceneSheet.modes
                value: sceneSheet.mode
                onPicked: (id) => sceneSheet.mode = id
            }
            Label { text: qsTr("Длина") }
            Segmented {
                objectName: "sceneLength"
                width: sceneSheet.availableWidth
                options: Films.sceneLengths
                value: sceneSheet.length
                onPicked: (id) => sceneSheet.length = id
            }
            Note {
                width: sceneSheet.availableWidth
                text: qsTr("Изменилось описание, звук, начало или длина — сцену нужно переснять, а с ней и те, что её продолжают. Реплика диктора пересъёмки не требует — только «Собрать ролик» заново.")
                color: Theme.p.faint
            }
            Row {
                anchors.right: parent.right
                spacing: 8
                PlainButton { objectName: "sceneCancel"; text: qsTr("Отмена"); onClicked: sceneSheet.close() }
                PlainButton {
                    objectName: "sceneSave"
                    kind: "primary"
                    text: qsTr("Сохранить")
                    enabled: scenePrompt.text.trim() !== ""
                    onClicked: {
                        Films.saveScene(sceneSheet.n, sceneWords.text, scenePrompt.text, sceneSheet.mode,
                                        sceneSheet.length, sceneSound.text, sceneSay.text)
                        sceneSheet.close()
                    }
                }
            }
        }
    }

    // left: what the film is about
    Rectangle {
        id: settings
        width: director.narrow ? 280 : 320
        height: parent.height
        color: Theme.p.window
        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.p.hairline }
        Flickable {
            anchors.fill: parent
            anchors.margins: 16
            contentHeight: column.height
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            Column {
                id: column
                width: parent.width
                spacing: 12
                Chip {
                    id: filmChip
                    objectName: "filmChoice"
                    visible: Films.filmChoices.length > 1
                    iconName: "layers"
                    text: qsTr("Ролик: %1").arg(Films.filmName)
                    enabled: !Films.busy
                    onClicked: { filmMenu.anchor = filmChip; filmMenu.open() }
                }
                Label { text: qsTr("Фото товара") }
                PlainButton {
                    objectName: "filmPhoto"
                    visible: !Films.hasPhoto
                    iconName: "image"
                    text: qsTr("Выбрать фото…")
                    onClicked: photoDialog.open()
                }
                Row {
                    objectName: "filmPhotoShown"
                    visible: Films.hasPhoto
                    spacing: 10
                    Rectangle {
                        width: 64
                        height: 64
                        radius: 6
                        color: Theme.p.card
                        border.color: Theme.p.hairline
                        Image {
                            anchors.fill: parent
                            anchors.margins: 3
                            source: Films.photoSource
                            sourceSize.width: 128
                            sourceSize.height: 128
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            cache: false
                        }
                    }
                    Column {
                        spacing: 6
                        anchors.verticalCenter: parent.verticalCenter
                        PlainButton { iconName: "image"; text: qsTr("Другое…"); onClicked: photoDialog.open() }
                        PlainButton {
                            objectName: "filmNoPhoto"
                            iconName: "close"
                            text: qsTr("Без фото")
                            onClicked: Films.clearPhoto()
                        }
                    }
                }
                Label { text: qsTr("Идея ролика") }
                Area {
                    id: idea
                    edit.objectName: "filmIdea"
                    width: parent.width
                    height: 120
                    text: Films.idea
                    placeholder: qsTr("О чём ролик? Например: латунная лампа на рабочем столе, уютный вечер, тёплый свет")
                    edit.onTextChanged: if (idea.text !== Films.idea) Films.setIdea(idea.text)
                }
                Label { text: qsTr("Длина") }
                Segmented {
                    objectName: "filmLength"
                    width: parent.width
                    options: Films.lengths
                    value: Films.length
                    onPicked: (id) => Films.setLength(id)
                }
                Label { text: qsTr("Кадр") }
                Segmented {
                    objectName: "filmShape"
                    width: parent.width
                    options: Films.shapes
                    value: Films.shape
                    onPicked: (id) => Films.setShape(id)
                }
                PlainButton {
                    objectName: "filmWrite"
                    width: parent.width
                    kind: Films.state === "writing" ? "danger" : "primary"
                    iconName: Films.state === "writing" ? "stop" : "sparkle"
                    enabled: Films.state === "writing" || Films.canWrite
                    text: Films.state === "writing" ? qsTr("Стоп") : Films.writeText
                    onClicked: Films.state === "writing" ? Films.stop() : Films.write()
                }
                Note {
                    text: qsTr("Сценарий пишет модель роли «Режиссёр» («Модели» → «Роли»); ей уходят идея и фото товара. Снимает модель видео — сцена за сценой.")
                    color: Theme.p.faint
                }
                OnOff {
                    id: checks
                    objectName: "filmChecks"
                    text: qsTr("Проверять сцены")
                    enabled: !Films.busy
                    onToggled: Films.setChecks(checked)
                    Binding { target: checks; property: "checked"; value: Films.checks }  // a click does not cut it
                }
                Note {
                    text: qsTr("Режиссёр смотрит шесть кадров каждой снятой сцены и фото товара. Брак — товар не тот, нет движения, искажения, ушёл цвет — переснимается с исправленным описанием, не больше двух раз. Облачной модели кадры уходят в облако.")
                    color: Theme.p.faint
                }
                OnOff {  // 52.6: a video model that films with sound
                    id: filmSound
                    objectName: "filmSound"
                    visible: Films.canSound
                    text: qsTr("Со звуком")
                    enabled: !Films.busy
                    onToggled: Films.setSound(checked)
                    Binding { target: filmSound; property: "checked"; value: Films.sound }  // a click does not cut it
                }
                Note {
                    visible: Films.canSound
                    text: Films.soundNote
                    color: Theme.p.faint
                }
                PlainButton {  // 53.6: the narrator, the music, the parts of the engine of sound
                    objectName: "filmAdded"
                    text: qsTr("Звук фильма…")
                    onClicked: soundSheet.openUp()
                }
                Note {
                    objectName: "filmAddedText"
                    text: Films.addedText
                    color: Theme.p.faint
                }
                Label { text: qsTr("Стыки сцен"); visible: Films.hasScenes }
                Segmented {
                    objectName: "filmJoint"
                    visible: Films.hasScenes
                    width: parent.width
                    options: Films.joints
                    value: Films.joint
                    onPicked: (id) => Films.setJoint(id)
                }
                PlainButton {
                    objectName: "filmDelete"
                    visible: Films.hasFilm
                    enabled: !Films.busy
                    iconName: "trash"
                    text: qsTr("Удалить ролик")
                    onClicked: Films.remove()
                }
            }
        }
    }

    // the centre: the scenes, the player, what to do
    Item {
        id: stage
        x: settings.width
        width: parent.width - settings.width
        height: parent.height

        Text {
            objectName: "filmStatus"
            x: 20
            y: 14
            width: parent.width - 40
            visible: text !== ""
            text: Films.statusText
            elide: Text.ElideRight
            color: Films.failed ? Theme.p.error : Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Rectangle {
            visible: Films.state === "filming"
            x: 20
            y: 34
            width: parent.width - 40
            height: 3
            radius: 1.5
            color: Theme.p.hairline
            Rectangle {
                width: parent.width * Films.percent / 100
                height: parent.height
                radius: 1.5
                color: Theme.p.accent
            }
        }
        ListView {
            id: cards
            objectName: "filmScenes"
            x: 20
            y: 46
            width: parent.width - 40
            height: Films.hasScenes ? (Films.anyHeard ? 228 : 212) : 0
            visible: Films.hasScenes
            orientation: ListView.Horizontal
            spacing: 8
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: Films.scenes
            delegate: Rectangle {
                id: card
                required property var modelData
                objectName: "filmCard_" + card.modelData.n
                width: 220
                height: Films.anyHeard ? 220 : 204
                radius: 6
                color: Theme.p.card
                border.color: Films.shownScene === card.modelData.n ? Theme.p.accent : Theme.p.hairline
                Rectangle {  // the clip's first frame; a click plays the clip
                    id: frame
                    x: 8
                    y: 8
                    width: parent.width - 16
                    height: 84
                    radius: 4
                    color: Theme.p.window
                    clip: true
                    Image {
                        anchors.fill: parent
                        visible: card.modelData.thumb !== ""
                        source: card.modelData.thumb
                        sourceSize.width: 408
                        sourceSize.height: 168
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                    Icon {
                        anchors.centerIn: parent
                        visible: card.modelData.thumb === ""
                        name: "image"
                        size: 20
                        tint: Theme.p.faint
                    }
                    MouseArea {
                        objectName: "filmShow_" + card.modelData.n
                        anchors.fill: parent
                        enabled: card.modelData.filmed
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: Films.show(card.modelData.n)
                    }
                }
                Text {
                    x: 10
                    y: 98
                    width: parent.width - 20
                    text: card.modelData.title
                    elide: Text.ElideRight
                    color: Theme.p.muted
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                }
                Text {
                    x: 10
                    y: 116
                    width: parent.width - 20
                    height: 34
                    text: card.modelData.words
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Text {
                    x: 10
                    y: 152
                    width: parent.width - 20
                    text: card.modelData.statusText
                    elide: Text.ElideRight
                    color: card.modelData.failed ? Theme.p.error : Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                    ToolTip.visible: statusMouse.containsMouse && truncated
                    ToolTip.text: text
                    MouseArea { id: statusMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                }
                Text {  // 53.15: what the listener heard in the scene's sound - its own, or the made one
                    objectName: "filmHeard_" + card.modelData.n
                    x: 10
                    y: 168
                    width: parent.width - 20
                    visible: card.modelData.heard !== ""
                    text: card.modelData.heard
                    elide: Text.ElideRight
                    color: card.modelData.heardBad ? Theme.p.error : Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                    ToolTip.visible: heardMouse.containsMouse
                    ToolTip.text: card.modelData.heardHint
                    MouseArea { id: heardMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
                }
                Row {
                    x: 6
                    y: Films.anyHeard ? 184 : 168
                    spacing: 2
                    IconButton {
                        objectName: "filmEdit_" + card.modelData.n
                        iconName: "edit"
                        tip: qsTr("Изменить")
                        enabled: !Films.busy
                        onClicked: sceneSheet.openFor(card.modelData)
                    }
                    IconButton {
                        objectName: "filmShoot_" + card.modelData.n
                        iconName: "refresh"
                        tip: card.modelData.shootText
                        enabled: !Films.busy
                        onClicked: Films.shootScene(card.modelData.n)
                    }
                }
            }
        }
        Rectangle {
            id: canvas
            x: 20
            anchors.top: cards.bottom
            anchors.topMargin: cards.visible ? 4 : 0
            width: parent.width - 40
            height: parent.height - y - actions.height - (player.visible ? player.height + 8 : 0)
                    - (improvedList.visible ? improvedList.height + 10 : 0) - 36
            radius: 8
            color: Theme.p.card
            border.color: Theme.p.hairline
            VideoPlayer {
                id: video
                objectName: "filmVideo"
                anchors.fill: parent
                anchors.margins: 8
                visible: Films.playSource !== "" || Films.playFallback !== ""
                source: director.visible ? Films.playSource : ""  // «Режиссёр» hidden: the clip let go
                fallback: Films.playFallback
            }
            Text {
                anchors.centerIn: parent
                width: parent.width - 40
                visible: !video.visible
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap
                text: Films.hasScenes ? qsTr("Здесь появится снятая сцена, а потом и весь ролик")
                                      : qsTr("Опишите идею ролика и нажмите «Собрать сценарий» — режиссёр разложит его на сцены")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
        VideoControls {
            id: player
            objectName: "filmPlayer"
            prefix: "film"
            target: video
            note: Films.playNote
            x: 20
            width: parent.width - 40
            anchors.top: canvas.bottom
            anchors.topMargin: 8
            visible: video.visible
        }
        Flow {
            id: actions
            x: 20
            width: parent.width - 40
            anchors.top: player.visible ? player.bottom : canvas.bottom
            anchors.topMargin: 10
            spacing: 8
            visible: Films.hasScenes
            PlainButton {
                objectName: "filmShootAll"
                kind: Films.state === "filming" ? "danger" : "primary"
                iconName: Films.state === "filming" ? "stop" : "play"
                enabled: Films.state === "filming" || Films.canShoot
                text: Films.state === "filming" ? qsTr("Стоп") : Films.shootText
                onClicked: Films.state === "filming" ? Films.stop() : Films.shoot()
            }
            PlainButton {
                id: joinButton
                objectName: "filmJoin"
                // 53.6: a film with added sound is joined for minutes - its sound is made first; «Стоп» stops that
                readonly property bool stops: Films.state === "joining" && Films.addsSound
                kind: stops ? "danger" : "plain"
                iconName: stops ? "stop" : "layers"
                enabled: stops || Films.canJoin
                text: stops ? qsTr("Стоп") : Films.joined ? qsTr("Собрать заново") : qsTr("Собрать ролик")
                onClicked: stops ? Films.stop() : Films.join()
            }
            PlainButton {
                objectName: "filmWhole"
                visible: Films.joined && Films.shownScene !== 0
                iconName: "play"
                text: qsTr("Весь ролик")
                onClicked: Films.show(0)
            }
            PlainButton {
                objectName: "filmSave"
                visible: Films.joined
                iconName: "download"
                text: qsTr("Сохранить как…")
                onClicked: saveDialog.open()
            }
            PlainButton {
                objectName: "filmImprove"  // 54.6: twice as large, 60 a second, slowed - a video of its own
                visible: Films.joined
                enabled: !Films.busy
                iconName: "sparkle"
                text: qsTr("Улучшить…")
                onClicked: {
                    Improve.openFilm()
                    improveSheet.open()
                }
            }
        }
        // 54.6: the film's video improved - each as the Studio's queue has it: how far, ✕; done - to the Studio
        Column {
            id: improvedList
            x: 20
            width: parent.width - 40
            anchors.top: actions.bottom
            anchors.topMargin: visible ? 10 : 0
            visible: Films.improved.length > 0
            spacing: 6
            Repeater {
                model: Films.improved
                delegate: Item {
                    id: better
                    required property var modelData
                    objectName: "filmImproved_" + better.modelData.id
                    width: improvedList.width
                    height: 28
                    Text {
                        objectName: "filmImprovedText_" + better.modelData.id
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - betterDo.width - 12
                        elide: Text.ElideRight
                        text: better.modelData.what + " — " + better.modelData.state
                        color: better.modelData.failed ? Theme.p.error : Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                    }
                    Rectangle {
                        visible: better.modelData.going
                        anchors.bottom: parent.bottom
                        width: parent.width - betterDo.width - 12
                        height: 2
                        radius: 1
                        color: Theme.p.hairline
                        Rectangle {
                            width: parent.width * better.modelData.percent / 100
                            height: parent.height
                            radius: 1
                            color: Theme.p.accent
                        }
                    }
                    Row {
                        id: betterDo
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        PlainButton {
                            objectName: "filmImprovedOpen_" + better.modelData.id
                            visible: better.modelData.done
                            iconName: "play"
                            text: qsTr("Открыть в «Студии»")
                            onClicked: Films.openImproved(better.modelData.id)
                        }
                        IconButton {
                            objectName: "filmImprovedCancel_" + better.modelData.id
                            visible: better.modelData.going
                            iconName: "close"
                            tip: qsTr("Отменить")
                            onClicked: Visual.cancelJob(better.modelData.id)
                        }
                    }
                }
            }
        }
    }
}
