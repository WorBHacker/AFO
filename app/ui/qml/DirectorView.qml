import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Режиссёр» (stage 50.9): a film of scenes, in the Studio beside «Вручную». Left: the product's photo, the idea, the
// length, the shape, «Собрать сценарий»; the centre: the scenes' cards - the clip's first frame, what happens,
// «Изменить», «Переснять» -, the player (a scene's clip or the joined film), «Снять всё», «Собрать ролик». The hub
// makes the film (hub/director.py); Films (app/ui/film_view.py) asks and shows.
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
                text: qsTr("Изменилось описание, начало или длина — сцену нужно переснять, а с ней и те, что её продолжают.")
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
                        Films.saveScene(sceneSheet.n, sceneWords.text, scenePrompt.text, sceneSheet.mode, sceneSheet.length)
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
            height: Films.hasScenes ? 212 : 0
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
                height: 204
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
                Row {
                    x: 6
                    y: 168
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
            height: parent.height - y - actions.height - (player.visible ? player.height + 8 : 0) - 36
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
                objectName: "filmJoin"
                iconName: "layers"
                enabled: Films.canJoin
                text: Films.joined ? qsTr("Собрать заново") : qsTr("Собрать ролик")
                onClicked: Films.join()
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
        }
    }
}
