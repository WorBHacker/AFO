import QtQuick
import QtQuick.Controls.Basic
import QtMultimedia

// A piece of sound of the Studio's «Аудио» («Стабилизация», portion 5): Qt Multimedia's player over its WAV, with
// play and pause, the time to go to and the time it is at. The volume and «без звука» are the window's own, one for
// every player (Visual). `source` "" - nothing: the file let go (Windows keeps a file being played).
Row {
    id: root
    property string source: ""
    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState
    height: 32
    spacing: 10

    function clock(ms) {
        const seconds = Math.floor(Math.max(0, ms) / 1000)
        return Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0")
    }

    MediaPlayer {
        id: player
        objectName: "audioMedia"
        source: root.visible ? root.source : ""
        audioOutput: AudioOutput {
            objectName: "audioSpeaker"
            volume: Visual.volume
            muted: Visual.muted
        }
        onSourceChanged: if (source.toString() !== "") play()
    }
    IconButton {
        id: playPause
        objectName: "audioPlayPause"
        anchors.verticalCenter: parent.verticalCenter
        enabled: root.source !== ""
        iconName: root.playing ? "stop" : "play"
        tip: root.playing ? qsTr("Пауза") : qsTr("Играть")
        onClicked: root.playing ? player.pause() : player.play()
    }
    Slider {
        objectName: "audioSeek"
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(80, root.width - playPause.width - time.width - 2 * root.spacing)
        from: 0
        to: Math.max(1, player.duration)
        value: player.position
        enabled: player.seekable && player.duration > 0
        onMoved: player.setPosition(value)
    }
    Text {
        id: time
        anchors.verticalCenter: parent.verticalCenter
        text: root.clock(player.position) + " / " + root.clock(player.duration)
        color: Theme.p.muted
        font.family: Theme.uiFont
        font.pixelSize: 12
    }
}
