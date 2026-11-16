import QtQuick
import QtMultimedia

// A video (48.12): Qt Multimedia's player over the hub's MP4 - it goes to any frame and keeps none of them in memory
// (AnimatedImage kept every frame to go back: 960×960×73 - 257 MiB). Until the MP4 is there (the hub makes it the
// first time a video is shown) the WebP plays as Qt reads it, a frame at a time, and does not seek. Hidden - its own
// view or any above it - it lets the clip go. VideoControls drives it.
Item {
    id: root
    property string source: ""  // the MP4, a file's URL; "" - not made yet
    property string fallback: ""  // the WebP, the hub's file
    property bool held: false  // paused by the person
    readonly property bool seekable: root.source !== "" && player.seekable && player.duration > 0
    readonly property int position: player.position
    readonly property int duration: root.source !== "" ? player.duration : 0
    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState || (still.visible && !root.held)
    // the frame's size, for a view that takes its shape (a chat's card): the MP4's, or the WebP's while it plays
    readonly property size frame: root.source !== "" ? Qt.size(output.sourceRect.width, output.sourceRect.height)
                                                     : Qt.size(still.implicitWidth, still.implicitHeight)

    function toggle() { held = !held }
    function seek(ms) {
        held = true
        player.setPosition(Math.max(0, Math.min(ms, player.duration - 1)))
    }
    onHeldChanged: if (player.source.toString() !== "") held ? player.pause() : player.play()
    onFallbackChanged: held = false  // another video: it plays

    MediaPlayer {
        id: player
        objectName: "videoMedia"
        source: root.visible ? root.source : ""
        videoOutput: output
        loops: MediaPlayer.Infinite
        onSourceChanged: if (source.toString() !== "") root.held ? pause() : play()
    }
    VideoOutput {
        id: output
        objectName: "videoOutput"
        anchors.fill: parent
        visible: root.source !== ""
        fillMode: VideoOutput.PreserveAspectFit
    }
    AnimatedImage {  // the WebP until the MP4 is made: read forward, not kept (cache: false)
        id: still
        objectName: "videoStill"
        anchors.fill: parent
        visible: root.source === "" && root.fallback !== ""
        source: root.visible && root.source === "" ? root.fallback : ""
        cache: false
        fillMode: Image.PreserveAspectFit
        playing: visible
        paused: root.held
    }
}
