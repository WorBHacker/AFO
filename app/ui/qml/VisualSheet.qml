import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// «Нарисовать» (stage 29.5, D55): a description, the shape, «Быстрее ↔ Качественнее», the picture as it is drawn -
// step and preview - then the result with «Ещё вариант», «Сохранить как…» and the folder; the latest pictures below.
// The chip «Нарисовать» in the chat's input opens it with what was typed.
Sheet {
    id: sheet
    objectName: "visualSheet"
    title: qsTr("Нарисовать")
    iconName: "image"
    width: Math.min(760, (parent ? parent.width : 960) * 0.94)
    onOpened: Visual.load()

    component Note: Text {
        width: parent ? parent.width : 600
        wrapMode: Text.Wrap
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 12
        lineHeight: 1.25
    }

    FileDialog {
        id: saveDialog
        title: qsTr("Сохранить картинку")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Картинка PNG (*.png)")]
        defaultSuffix: "png"
        onAccepted: Visual.saveAs(selectedFile.toString())
    }

    // no picture model yet: where to get one
    Column {
        visible: Visual.loaded && !Visual.hasModels
        width: parent.width
        spacing: 10
        Note { text: qsTr("Модели для картинок ещё нет. AFO подберёт её под этот компьютер и поставит вместе с движком — одной кнопкой.") }
        PlainButton {
            objectName: "visualInstall"
            kind: "primary"
            iconName: "download"
            text: qsTr("Поставить модель картинок")
            onClicked: { Installer.setPurpose("image"); sheet.close(); App.openUi("install-models") }
        }
    }

    Column {
        visible: Visual.hasModels
        width: parent.width
        spacing: 10
        Rectangle {
            width: parent.width
            height: 84
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
        Flow {
            width: parent.width
            spacing: 10
            Segmented {
                objectName: "visualShape"
                options: Visual.shapes
                value: Visual.shape
                onPicked: (id) => Visual.setShape(id)
            }
            Segmented {
                objectName: "visualQuality"
                visible: Visual.choice
                options: Visual.qualities
                value: Visual.quality
                onPicked: (id) => Visual.setQuality(id)
            }
            PlainButton {
                objectName: "visualDraw"
                kind: Visual.busy ? "danger" : "primary"
                iconName: Visual.busy ? "stop" : "image"
                enabled: Visual.busy || promptArea.text.trim() !== ""
                text: Visual.busy ? qsTr("Стоп") : qsTr("Нарисовать")
                onClicked: Visual.busy ? Visual.stop() : Visual.draw()
            }
        }
        Note { text: Visual.modelText; color: Theme.p.faint; visible: text !== "" }
        Text {
            objectName: "visualStatus"
            width: parent.width
            visible: text !== ""
            text: Visual.statusText
            wrapMode: Text.Wrap
            color: Visual.statusText.indexOf(qsTr("не вышло")) === 0 ? Theme.p.error : Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
        Rectangle {
            visible: Visual.busy
            width: parent.width
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
            width: parent.width
            height: Math.min(parent.width, 560)
            radius: 8
            color: Theme.p.card
            border.color: Theme.p.hairline
            Image {
                objectName: "visualPicture"
                anchors.fill: parent
                anchors.margins: 6
                source: Visual.pictureSource
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                cache: false
                smooth: true
                mipmap: true
            }
            Text {
                anchors.centerIn: parent
                visible: Visual.pictureSource === ""
                text: Visual.busy ? qsTr("Загружаю модель…") : qsTr("Здесь появится картинка")
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 13
            }
        }
        Row {
            visible: Visual.done
            spacing: 8
            PlainButton {
                objectName: "visualAgain"
                iconName: "refresh"
                text: qsTr("Ещё вариант")
                onClicked: Visual.again()
            }
            PlainButton {
                objectName: "visualSave"
                iconName: "download"
                text: qsTr("Сохранить как…")
                onClicked: saveDialog.open()
            }
            PlainButton {
                iconName: "folder"
                text: qsTr("Папка с картинками")
                onClicked: Visual.openFolder()
            }
        }
        Text {
            visible: Visual.recent.length > 0
            text: qsTr("Последние")
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Flow {
            width: parent.width
            spacing: 8
            Repeater {
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
                    MouseArea {
                        id: thumbMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Visual.pick(thumb.modelData.id)
                    }
                    ToolTip.visible: thumbMouse.containsMouse
                    ToolTip.delay: 600
                    ToolTip.text: thumb.modelData.prompt
                }
            }
        }
    }
}
