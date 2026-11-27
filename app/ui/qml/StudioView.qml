import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Студия» (48.7а, the user's word 2026-11-15): pictures and videos on a screen of their own - the fourth mode of the
// sidebar. Left: what to make - the description, picture or video, the shape, how long, «Быстрее ↔ Качественнее», a
// picture to start from, «Нарисовать»/«Снять видео»; the centre: the picture or the video as it comes, and what to do
// with it; below: the latest. The chip «Нарисовать» in the chat's input and a chat's picture open it (29.5, 29.6б).
// With a video model here (50.9): «Вручную | Режиссёр» at the top - by hand, as above, or a film of scenes
// (DirectorView).
Item {
    id: studio
    objectName: "studioView"
    readonly property bool narrow: width < 860
    readonly property bool byHand: Films.kind !== "director"
    // a picture to start from, in use: «По картинке», or a video's first frame unless the video is by words (48.7б)
    readonly property bool fromPicture: Visual.hasSource && (Visual.mode !== "video" || Visual.videoMode !== "text")
    onVisibleChanged: if (visible) Visual.load()

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

    FileDialog {
        id: sourceDialog
        title: qsTr("Картинка-образец")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")]
        onAccepted: Visual.chooseSource(selectedFile.toString())
    }

    FileDialog {
        id: lastDialog  // a video's last frame (48.7б)
        title: qsTr("Последний кадр")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("Картинки (*.png *.jpg *.jpeg *.webp *.bmp)")]
        onAccepted: Visual.chooseLast(selectedFile.toString())
    }

    MenuPopup {  // the model (48.7б): «Авто» or one installed
        id: modelMenu
        menuWidth: 340
        below: true
        wrapNotes: true
        items: Visual.modelChoices
        onChosen: (id) => Visual.setModel(id)
    }

    FileDialog {
        id: saveDialog
        title: Visual.videoFile !== "" ? qsTr("Сохранить видео") : qsTr("Сохранить картинку")
        fileMode: FileDialog.SaveFile
        // a video: MP4 (made by ffmpeg in the hub, 29.8в) or the engine's WebP as it is; with sound (52.5) - MP4
        nameFilters: Visual.videoFile === "" ? [qsTr("Картинка PNG (*.png)")]
                     : Visual.videoLoud ? [qsTr("Видео MP4 (*.mp4)")]
                                        : [qsTr("Видео MP4 (*.mp4)"), qsTr("Видео WebP (*.webp)")]
        defaultSuffix: Visual.videoFile === "" ? "png"
                                                 : (selectedNameFilter.extensions.length > 0
                                                    ? selectedNameFilter.extensions[0] : "mp4")
        onAccepted: Visual.saveAs(selectedFile.toString())
    }

    ImproveSheet {  // 54.6: «Улучшить…» of the video shown
        id: improveSheet
        objectName: "improveSheet"
        prefix: "improve"
    }

    Item {  // 50.9: by hand, or the director's film
        id: kindBar
        visible: Films.available
        width: parent.width
        height: visible ? 46 : 0
        Segmented {
            objectName: "studioKind"
            x: 16
            anchors.verticalCenter: parent.verticalCenter
            options: Films.kinds
            value: Films.kind
            onPicked: (id) => Films.setKind(id)
        }
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
    }
    DirectorView {
        visible: studio.visible && !studio.byHand
        y: kindBar.height
        width: parent.width
        height: parent.height - kindBar.height
    }

    // no model yet: where to get one - for pictures, for video (the fix after 0.1.15: the Studio films too); each
    // button takes the install sheet to its own place, whatever it was left on
    Column {
        visible: Visual.loaded && !Visual.hasModels
        anchors.centerIn: parent
        width: Math.min(460, parent.width - 48)
        spacing: 12
        Note { text: qsTr("Моделей для картинок и видео ещё нет. AFO подберёт модель под этот компьютер и поставит вместе с движком — одной кнопкой.") }
        Flow {
            width: parent.width
            spacing: 8
            PlainButton {
                objectName: "visualInstall"
                kind: "primary"
                iconName: "download"
                text: qsTr("Поставить модель картинок")
                onClicked: Installer.offer("image")  // «Подборка», «Рисовать»
            }
            PlainButton {
                objectName: "visualInstallVideo"
                iconName: "download"
                text: qsTr("Поставить модель для видео")
                onClicked: Installer.offer("video")  // «Подборка», the checked video model's card
            }
        }
        Text {
            objectName: "visualMoreVideo"
            text: "<a href=\"x\">" + qsTr("Другие модели для видео") + "</a>"
            textFormat: Text.StyledText
            linkColor: Theme.p.link
            font.family: Theme.uiFont
            font.pixelSize: 13
            onLinkActivated: Installer.offer("moreVideo")  // «Поиск», «Видео», «Поместится в видеопамять»
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
    }

    // left: what to make
    Rectangle {
        id: settings
        visible: Visual.hasModels && studio.byHand
        y: kindBar.height
        width: studio.narrow ? 280 : 320
        height: parent.height - kindBar.height
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
                Label { text: qsTr("Что сделать") }
                Rectangle {
                    width: parent.width
                    height: 120
                    radius: 6
                    color: Theme.p.card
                    border.color: promptArea.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 2
                        TextArea {
                            id: promptArea
                            objectName: "visualPrompt"
                            text: Visual.prompt
                            wrapMode: TextEdit.Wrap
                            placeholderText: qsTr("Что нарисовать? Например: рыжая лиса в снегу на рассвете, фотография")
                            placeholderTextColor: Theme.p.faint
                            color: Theme.p.text
                            font.family: Theme.uiFont
                            font.pixelSize: 13
                            background: null
                            onTextChanged: if (text !== Visual.prompt) Visual.setPrompt(text)
                            Keys.onReturnPressed: (event) => {
                                if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return }
                                Visual.draw()
                            }
                        }
                    }
                }
                Segmented {  // 29.8б; always (2026-11-27): a kind without a model offers one
                    objectName: "visualMode"
                    options: Visual.modes
                    value: Visual.mode
                    onPicked: (id) => Visual.pickMode(id)
                }
                Chip {  // 48.7б: «Авто» or one of those installed
                    id: modelChip
                    objectName: "visualModel"
                    iconName: "sparkle"
                    text: qsTr("Модель: %1").arg(Visual.modelName)
                    onClicked: { modelMenu.anchor = modelChip; modelMenu.open() }
                }
                Segmented {  // 48.7б: by words, by a picture, by two frames - as the model films
                    objectName: "visualVideoMode"
                    visible: Visual.mode === "video" && Visual.videoModes.length > 1
                    options: Visual.videoModes
                    value: Visual.videoMode
                    onPicked: (id) => Visual.setVideoMode(id)
                }
                Segmented {
                    objectName: "visualShape"
                    visible: !studio.fromPicture  // from a picture: its shape
                    options: Visual.shapes
                    value: Visual.shape
                    onPicked: (id) => Visual.setShape(id)
                }
                Segmented {
                    objectName: "visualLength"
                    visible: Visual.mode === "video"
                    options: Visual.lengths
                    value: Visual.length
                    onPicked: (id) => Visual.setLength(id)
                }
                OnOff {  // 52.5: a model that films with sound
                    id: soundSwitch
                    objectName: "visualSound"
                    visible: Visual.canSound
                    text: qsTr("Со звуком")
                    onToggled: Visual.setSound(checked)
                    Binding { target: soundSwitch; property: "checked"; value: Visual.sound }  // a click does not cut it
                }
                Rectangle {
                    width: parent.width
                    height: 34
                    radius: 6
                    visible: Visual.canSound && Visual.sound
                    color: Theme.p.card
                    border.color: soundWords.activeFocus ? Theme.p.accent : Theme.p.hairline_strong
                    TextField {
                        id: soundWords
                        objectName: "visualSoundWords"
                        anchors.fill: parent
                        anchors.margins: 2
                        text: Visual.soundWords
                        placeholderText: qsTr("Звук: что слышно? Например: шум дождя, проезжает машина")
                        placeholderTextColor: Theme.p.faint
                        color: Theme.p.text
                        font.family: Theme.uiFont
                        font.pixelSize: 13
                        background: null
                        onTextChanged: if (text !== Visual.soundWords) Visual.setSoundWords(text)
                    }
                }
                Segmented {
                    objectName: "visualQuality"
                    visible: Visual.choice
                    options: Visual.qualities
                    value: Visual.quality
                    onPicked: (id) => Visual.setQuality(id)
                }
                PlainButton {
                    objectName: "visualFromFile"
                    visible: !Visual.hasSource && (Visual.mode !== "video" || Visual.videoMode !== "text")
                    iconName: "image"
                    text: Visual.mode === "video" ? qsTr("Первый кадр…") : qsTr("По картинке…")
                    onClicked: sourceDialog.open()
                }
                // «По картинке» (29.6б): the source and how much to change it
                Row {
                    objectName: "visualSource"
                    visible: studio.fromPicture
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
                            source: Visual.sourceSource
                            sourceSize.width: 128
                            sourceSize.height: 128
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                        }
                    }
                    Column {
                        spacing: 6
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: Visual.mode === "video" ? qsTr("Первый кадр") : qsTr("По картинке — насколько менять:")
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                        Segmented {
                            objectName: "visualStrength"
                            visible: Visual.mode !== "video"  // a video's first frame is kept as it is
                            options: Visual.strengths
                            value: Visual.strength
                            onPicked: (id) => Visual.setStrength(id)
                        }
                        PlainButton {
                            iconName: "close"
                            text: Visual.mode === "video" ? qsTr("Без кадра") : qsTr("Без образца")
                            onClicked: Visual.clearSource()
                        }
                    }
                }
                // 48.7б: a video between two frames - its last one
                PlainButton {
                    objectName: "visualLastFromFile"
                    visible: Visual.mode === "video" && Visual.videoMode === "frames" && !Visual.hasLast
                    iconName: "image"
                    text: qsTr("Последний кадр…")
                    onClicked: lastDialog.open()
                }
                Row {
                    objectName: "visualLast"
                    visible: Visual.mode === "video" && Visual.videoMode === "frames" && Visual.hasLast
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
                            source: Visual.lastSource
                            sourceSize.width: 128
                            sourceSize.height: 128
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                        }
                    }
                    Column {
                        spacing: 6
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: qsTr("Последний кадр")
                            color: Theme.p.text_soft
                            font.family: Theme.uiFont
                            font.pixelSize: 12
                        }
                        PlainButton {
                            iconName: "close"
                            text: qsTr("Без последнего")
                            onClicked: Visual.clearLast()
                        }
                    }
                }
                PlainButton {
                    objectName: "visualDraw"
                    width: parent.width
                    kind: Visual.busy ? "danger" : "primary"
                    iconName: Visual.busy ? "stop" : "image"
                    enabled: Visual.busy || promptArea.text.trim() !== ""
                    text: Visual.busy ? qsTr("Стоп") : (Visual.mode === "video" ? qsTr("Снять видео") : qsTr("Нарисовать"))
                    onClicked: Visual.busy ? Visual.stop() : Visual.draw()
                }
                PlainButton {  // 48.7в: one drawn - the next into the hub's queue, the canvas kept
                    objectName: "visualEnqueue"
                    visible: Visual.busy
                    width: parent.width
                    iconName: "plus"
                    enabled: promptArea.text.trim() !== ""
                    text: qsTr("В очередь")
                    onClicked: Visual.draw()
                }
                Note { text: Visual.modelText; color: Theme.p.faint; visible: text !== "" }
            }
        }
    }

    // the centre: the picture or the video, and what to do with it; below - the latest
    Item {
        id: stage
        visible: Visual.hasModels && studio.byHand
        x: settings.width
        y: kindBar.height
        width: parent.width - settings.width
        height: parent.height - kindBar.height

        Text {
            objectName: "visualStatus"
            x: 20
            y: 14
            width: parent.width - 40
            visible: text !== ""
            text: Visual.statusText
            elide: Text.ElideRight
            color: Visual.statusText.indexOf(qsTr("не вышло")) === 0 ? Theme.p.error : Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Rectangle {
            visible: Visual.busy
            x: 20
            y: 34
            width: parent.width - 40
            height: 3
            radius: 1.5
            color: Theme.p.hairline
            Rectangle {
                width: parent.width * Visual.percent / 100
                height: parent.height
                radius: 1.5
                color: Theme.p.accent
            }
        }
        Rectangle {
            id: canvas
            x: 20
            y: 46
            width: parent.width - 40
            height: parent.height - y - actions.height - latest.height - queueStrip.height
                    - (player.visible ? player.height + 8 : 0) - 36
            radius: 8
            color: Theme.p.card
            border.color: Theme.p.hairline
            VideoPlayer {  // a video (29.8б, 48.12): its MP4, the hub's WebP until that is made
                id: video
                objectName: "visualVideo"
                anchors.fill: parent
                anchors.margins: 8
                visible: Visual.videoFile !== ""
                source: visible ? Visual.videoMp4 : ""  // the Studio hidden, or «Режиссёр» shown: the clip let go
                fallback: Visual.videoFile
            }
            Image {
                objectName: "visualPicture"
                anchors.fill: parent
                anchors.margins: 8
                source: Visual.pictureSource
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
                smooth: true
                mipmap: true
            }
            Text {
                anchors.centerIn: parent
                visible: Visual.pictureSource === "" && Visual.videoFile === ""
                text: Visual.busy ? (Visual.shownVideo ? qsTr("Снимаю видео — это минута или больше…")
                                                               : qsTr("Загружаю модель…"))
                                  : (Visual.mode === "video" ? qsTr("Здесь появится видео") : qsTr("Здесь появится картинка"))
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
        // 48.7г, 48.12: the video's own controls - pause, the time to go to, its time; (52.5) its sound
        VideoControls {
            id: player
            objectName: "visualPlayer"
            prefix: "visual"
            target: video
            note: Visual.videoNote
            x: 20
            width: parent.width - 40
            anchors.top: canvas.bottom
            anchors.topMargin: 8
            visible: Visual.videoFile !== ""
        }
        Flow {  // a narrow centre: the buttons go on a second row
            id: actions
            x: 20
            width: parent.width - 40
            anchors.top: player.visible ? player.bottom : canvas.bottom
            anchors.topMargin: 10
            spacing: 8
            visible: Visual.done || Visual.failed
            PlainButton {  // (2026-11-27) the one that did not come out - «хаб перезапускался», the engine refused
                objectName: "visualRetry"
                visible: Visual.failed
                iconName: "refresh"
                text: qsTr("Повторить")
                onClicked: Visual.retry()
            }
            PlainButton {
                objectName: "visualAgain"
                visible: Visual.done
                iconName: "refresh"
                text: qsTr("Ещё вариант")
                onClicked: Visual.again()
            }
            PlainButton {
                objectName: "visualFromThis"
                visible: Visual.done && Visual.videoFile === ""
                iconName: "image"
                text: qsTr("По этой картинке")
                onClicked: Visual.fromThis()
            }
            PlainButton {
                objectName: "visualImprove"  // 54.6: twice as large, 60 a second, slowed - a new video beside
                visible: Visual.done && Visual.videoFile !== ""
                iconName: "sparkle"
                text: qsTr("Улучшить…")
                onClicked: {
                    Improve.openStudio()
                    improveSheet.open()
                }
            }
            PlainButton {
                objectName: "visualSave"
                visible: Visual.done
                iconName: "download"
                text: qsTr("Сохранить как…")
                onClicked: saveDialog.open()
            }
            PlainButton {
                visible: Visual.done
                iconName: "folder"
                text: qsTr("Папка с картинками")
                onClicked: Visual.openFolder()
            }
            PlainButton {  // (2026-11-25) for good, asked first
                objectName: "visualDelete"
                visible: Visual.done
                iconName: "trash"
                text: qsTr("Удалить")
                onClicked: Visual.remove("")
            }
        }
        // 48.7в: the jobs going on - each how far it is, ✕ to cancel
        ListView {
            id: queueStrip
            objectName: "visualQueue"
            x: 20
            anchors.bottom: latest.top
            anchors.bottomMargin: latest.visible ? 10 : 0
            width: parent.width - 40
            height: Visual.queue.length > 0 ? 56 : 0
            visible: Visual.queue.length > 0
            orientation: ListView.Horizontal
            spacing: 8
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: Visual.queue
            delegate: Rectangle {
                id: queued
                required property var modelData
                width: 240
                height: 56
                radius: 6
                color: Theme.p.card
                border.color: queued.modelData.shown ? Theme.p.accent : Theme.p.hairline
                Icon {
                    x: 10
                    y: 10
                    name: queued.modelData.kind === "video" ? "play" : "image"
                    size: 14
                }
                Text {
                    x: 32
                    y: 8
                    width: parent.width - 32 - 34
                    text: queued.modelData.prompt
                    elide: Text.ElideRight
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
                Text {
                    x: 32
                    y: 26
                    width: parent.width - 32 - 34
                    text: queued.modelData.words
                    elide: Text.ElideRight
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 11
                }
                Rectangle {
                    x: 10
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 6
                    width: parent.width - 20
                    height: 2
                    radius: 1
                    color: Theme.p.hairline
                    Rectangle {
                        width: parent.width * queued.modelData.percent / 100
                        height: parent.height
                        radius: 1
                        color: Theme.p.accent
                    }
                }
                IconButton {
                    objectName: "visualCancel_" + queued.modelData.id
                    anchors.right: parent.right
                    anchors.rightMargin: 6
                    y: 6
                    iconName: "close"
                    tip: qsTr("Отменить")
                    onClicked: Visual.cancelJob(queued.modelData.id)
                }
            }
        }
        Column {
            id: latest
            x: 20
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 12
            width: parent.width - 40
            height: Visual.recent.length > 0 || Visual.gallery !== "all" ? 124 : 0
            spacing: 6
            visible: height > 0
            Row {
                spacing: 12
                Label { text: qsTr("Последние"); anchors.verticalCenter: parent.verticalCenter }
                Segmented {  // 48.7в: all, pictures or videos
                    objectName: "visualGallery"
                    options: Visual.galleryFilters
                    value: Visual.gallery
                    onPicked: (id) => Visual.setGallery(id)
                }
            }
            ListView {
                objectName: "visualRecent"
                width: parent.width
                height: 84
                orientation: ListView.Horizontal
                spacing: 8
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: Visual.recent
                delegate: Rectangle {
                    id: thumb
                    required property var modelData
                    width: 84
                    height: 84
                    radius: 6
                    color: Theme.p.card
                    border.color: thumb.modelData.chosen ? Theme.p.accent : Theme.p.hairline
                    Image {
                        anchors.fill: parent
                        anchors.margins: 3
                        source: thumb.modelData.source
                        sourceSize.width: 160
                        sourceSize.height: 160
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                    Rectangle {  // a video: its first frame, marked
                        visible: thumb.modelData.video
                        x: 6
                        y: 6
                        width: 20
                        height: 20
                        radius: 10
                        color: Theme.p.window
                        opacity: 0.85
                        Icon { anchors.centerIn: parent; name: "play"; size: 12; tint: Theme.p.text }
                    }
                    MouseArea {
                        id: thumbMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Visual.pick(thumb.modelData.id)
                    }
                    HoverHandler { id: thumbHover }  // the mouse over the thumbnail or its ✕
                    IconButton {  // (2026-11-25) deleted for good, asked first
                        objectName: "visualDelete_" + thumb.modelData.id
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 3
                        visible: thumbHover.hovered
                        iconName: "close"
                        tip: qsTr("Удалить")
                        onClicked: Visual.remove(thumb.modelData.id)
                    }
                    ToolTip.visible: thumbMouse.containsMouse
                    ToolTip.delay: 600
                    ToolTip.text: thumb.modelData.prompt
                }
            }
        }
    }
}
