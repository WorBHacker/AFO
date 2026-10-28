import QtQuick
import QtQuick.Controls.Basic

// The first run (10.2, D37): this computer, the engine for local models (llama.cpp + llama-swap from GitHub, one
// button), and the first model for each purpose. Opens by itself when the hub has no engine yet; «Позже» keeps it
// closed until Settings opens it.
Sheet {
    id: sheet
    objectName: "wizardSheet"
    title: "Первый запуск"
    iconName: "sparkle"
    width: Math.min(700, (parent ? parent.width : 960) * 0.94)
    onAboutToShow: Setup.load()

    function tone(t) { return t === "ok" ? Theme.p.ok : t === "warn" ? Theme.p.warn : t === "error" ? Theme.p.error : Theme.p.muted }

    component Note: Text {
        width: parent ? parent.width : 600
        wrapMode: Text.Wrap
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
        lineHeight: 1.3
    }

    Note {
        text: "Локальные модели работают прямо на этом компьютере: без интернета, без оплаты, и ваши тексты никуда не "
              + "уходят. LocalChat сам поставит всё нужное — нажимайте кнопки сверху вниз."
    }

    SectionTitle { text: "Этот компьютер" }
    Repeater {
        model: Setup.cards
        delegate: Note {
            required property var modelData
            text: "• " + modelData
            color: Theme.p.text
        }
    }
    Note { visible: Setup.why !== ""; text: Setup.why; color: Theme.p.muted }
    Note { visible: Setup.memoryText !== ""; text: Setup.memoryText; color: Theme.p.muted }
    Note { visible: Setup.pageAdvice !== ""; text: Setup.pageAdvice; color: Theme.p.warn; font.pixelSize: 12 }

    SectionTitle { text: "Движок" }
    Note { objectName: "wizardEngine"; text: Setup.engineText }
    Segmented {
        visible: Setup.needed && Setup.jobStatus !== "downloading" && Setup.jobStatus !== "starting"
        width: parent.width
        options: Setup.builds
        value: Setup.build
        onPicked: (id) => Setup.setBuild(id)
    }
    Rectangle {
        visible: Setup.jobStatus === "downloading"
        width: parent.width
        height: 6
        radius: 3
        color: Theme.p.hairline
        Rectangle {
            width: parent.width * Math.min(Setup.jobPercent, 100) / 100
            height: parent.height
            radius: 3
            color: Theme.p.accent
        }
    }
    Note {
        visible: Setup.jobText !== ""
        text: Setup.jobText
        color: Setup.jobStatus === "failed" ? Theme.p.error : Setup.jobStatus === "done" ? Theme.p.ok : Theme.p.muted
        font.pixelSize: 12
    }
    Note {
        objectName: "wizardUpdate"
        visible: !Setup.needed && Setup.updateText !== ""
        text: Setup.updateText
        color: Theme.p.muted
        font.pixelSize: 12
    }
    PlainButton {
        objectName: "wizardUpdateEngine"
        visible: !Setup.needed && Setup.updateAvailable && Setup.jobStatus !== "downloading"
        iconName: "refresh"
        text: "Обновить движок"
        onClicked: Setup.updateEngine()
    }
    Row {
        spacing: 8
        PlainButton {
            objectName: "wizardInstallEngine"
            visible: Setup.needed && Setup.jobStatus !== "downloading" && Setup.jobStatus !== "starting"
            kind: "primary"
            iconName: "download"
            text: Setup.jobStatus === "failed" ? "Попробовать снова" : "Установить движок"
            onClicked: Setup.installEngine()
        }
        PlainButton {
            visible: Setup.jobStatus === "downloading"
            kind: "danger"
            text: "Отменить"
            onClicked: Setup.cancelEngine()
        }
    }

    SectionTitle { text: "Первая модель"; visible: Setup.loaded && !Setup.needed }
    Note {
        visible: Setup.loaded && !Setup.needed && Setup.modelsLoading
        text: "Подбираю модели под этот компьютер…"
        color: Theme.p.faint
    }
    Repeater {
        model: Setup.loaded && !Setup.needed ? Setup.models : []
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
                Text {
                    text: card.modelData.title + " — " + card.modelData.what
                    color: Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                }
                Row {
                    spacing: 6
                    Rectangle { width: 8; height: 8; radius: 4; color: sheet.tone(card.modelData.tone); anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: [card.modelData.name, card.modelData.offer, card.modelData.words].filter(s => s).join(" · ")
                        color: Theme.p.text_soft
                        font.family: Theme.uiFont
                        font.pixelSize: 12
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                PlainButton {
                    objectName: "wizardModel_" + card.modelData.id
                    visible: card.modelData.canInstall
                    kind: card.modelData.installed ? "plain" : "primary"
                    iconName: "download"
                    text: card.modelData.installed ? "Уже установлена" : "Установить"
                    enabled: !card.modelData.installed
                    onClicked: Setup.installModel(card.modelData.id)
                }
            }
        }
    }
    Row {
        spacing: 8
        visible: Setup.loaded && !Setup.needed
        PlainButton {
            iconName: "layers"
            text: "Все модели и ход установки…"
            onClicked: { sheet.close(); App.openUi("install-models") }
        }
    }

    Item { width: 1; height: 6 }
    Row {
        spacing: 8
        PlainButton {
            objectName: "wizardLater"
            text: "Позже"
            onClicked: { Setup.dismiss(); sheet.close() }
        }
        PlainButton {
            kind: "primary"
            text: "Готово"
            visible: Setup.loaded && !Setup.needed
            onClicked: { Setup.dismiss(); sheet.close() }
        }
    }
}
