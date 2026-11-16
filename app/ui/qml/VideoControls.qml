import QtQuick
import QtQuick.Controls.Basic

// A video's own controls (48.7г, 48.12): pause, the time to go to, the time it is at - or, until the hub's MP4 is
// there, why it does not seek. `target` is a VideoPlayer; `prefix` begins the controls' objectNames.
Row {
    id: controls
    property Item target
    property string prefix: "video"
    property string note: ""
    height: 28
    spacing: 10

    function clock(ms) {
        const seconds = Math.floor(Math.max(0, ms) / 1000)
        return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0")
    }

    IconButton {
        id: playPause
        objectName: controls.prefix + "PlayPause"
        anchors.verticalCenter: parent.verticalCenter
        iconName: controls.target && controls.target.held ? "play" : "stop"
        tip: controls.target && controls.target.held ? qsTr("Дальше") : qsTr("Пауза")
        onClicked: controls.target.toggle()
    }
    Slider {
        objectName: controls.prefix + "Seek"
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(60, controls.width - playPause.width - time.width - 2 * controls.spacing)
        from: 0
        to: Math.max(1, controls.target ? controls.target.duration : 1)
        value: controls.target ? controls.target.position : 0
        enabled: !!controls.target && controls.target.seekable
        onMoved: controls.target.seek(value)
    }
    Text {
        id: time
        objectName: controls.prefix + "Time"
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, Math.max(80, controls.width / 2))
        elide: Text.ElideRight
        text: controls.note !== "" ? controls.note
                                   : controls.clock(controls.target ? controls.target.position : 0) + " / "
                                     + controls.clock(controls.target ? controls.target.duration : 0)
        color: Theme.p.text_soft
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
