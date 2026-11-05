import QtQuick
import QtQuick.Controls.Basic

// Settings → «О программе»: the version, and AFO's own updates (10.4d) - the newest release on GitHub, downloaded on
// a click, installed after a question.
Sheet {
    id: sheet
    objectName: "settingsAbout"
    title: qsTr("О программе")
    iconName: "sparkle"

    Text {
        width: parent.width
        text: qsTr("AFO %1 · окно рисует процессор, видеопамять остаётся моделям").arg(App.version)
        wrapMode: Text.Wrap
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Text {
        objectName: "releaseText"
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Release.text
        color: Release.failed ? Theme.p.warn : Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 13
    }
    Text {
        visible: text !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: Release.note
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
    Rectangle {
        visible: Release.downloading
        width: parent.width
        height: 4
        radius: 2
        color: Theme.p.hairline_strong
        Rectangle { width: parent.width * Release.progress; height: parent.height; radius: 2; color: Theme.p.accent }
    }
    Flow {
        width: parent.width
        spacing: 8
        PlainButton {
            objectName: "releaseCheck"
            visible: !Release.busy
            iconName: "refresh"
            text: qsTr("Проверить обновления")
            onClicked: Release.check()
        }
        PlainButton {
            objectName: "releaseDownload"
            visible: Release.canDownload
            iconName: "download"
            text: qsTr("Скачать AFO %1").arg(Release.latest)
            onClicked: Release.download()
        }
        PlainButton {
            visible: Release.downloading
            text: qsTr("Остановить загрузку")
            onClicked: Release.cancel()
        }
        PlainButton {
            objectName: "releaseInstall"
            visible: Release.canInstall
            iconName: "sparkle"
            text: qsTr("Установить и перезапустить…")
            onClicked: Release.install()
        }
        PlainButton {
            visible: Release.notesUrl !== ""
            iconName: "external"
            text: qsTr("Что нового")
            onClicked: App.openLink(Release.notesUrl)
        }
    }
    Row {
        spacing: 8
        Text {
            text: qsTr("Проверять обновления сами")
            color: Theme.p.text_soft
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
        Segmented {
            objectName: "releaseAuto"
            options: [{ id: "on", name: qsTr("вкл") }, { id: "off", name: qsTr("выкл") }]
            value: Release.auto ? "on" : "off"
            onPicked: (id) => Release.setAuto(id === "on")
        }
    }
}
