import QtQuick
import QtMultimedia
import AFO.Video

// A video (48.12): Qt Multimedia's player over the hub's MP4 - it goes to any frame and keeps none of them in memory
// (AnimatedImage kept every frame to go back: 960×960×73 - 257 MiB). The hub makes the MP4 as the video is filmed
// (48.12.8), an older one's the first time it is shown: until it is there the WebP's first frame stands still - the
// WebP played meanwhile, then the MP4 from its start, and the clip began twice; with none to be made the WebP plays,
// as Qt reads it, a frame at a time, and does not seek. Hidden - its own view or any above it - it lets the clip go.
// VideoControls drives it. The frames are drawn by VideoSurface (app/ui/video_surface.py, 48.12.7): VideoOutput draws
// with the card's shaders, and the window's software scene graph drew nothing of it - the live check saw the
// background.
Item {
    id: root
    property string source: ""  // the MP4, a file's URL; "" - not made yet; the WebP's own - none can be made
    property string fallback: ""  // the WebP, the hub's file
    property bool held: false  // paused by the person
    readonly property bool mp4: root.source !== "" && root.source !== root.fallback
    readonly property bool seekable: root.mp4 && player.seekable && player.duration > 0
    readonly property int position: root.wanted >= 0 ? root.wanted : player.position
    readonly property int duration: root.mp4 ? player.duration : 0
    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState || (still.playing && !root.held)
    // the frame's size, for a view that takes its shape (a chat's card): the MP4's, or the WebP's while it stands
    readonly property size frame: root.mp4 ? Qt.size(output.frameSize.width, output.frameSize.height)
                                           : Qt.size(still.implicitWidth, still.implicitHeight)
    // (48.12.8) the slider asks for a time at each move of the mouse - each 4 ms at a 240 Hz screen - and each asked of
    // the player dropped the one it was going to: a drag across a 960×960 clip showed 2 frames in 1.9 s. One at a time
    // now: the next once the frame of the one before is shown, the latest asked meanwhile, the slider at it
    property int wanted: -1  // the latest time asked while the player was going to another; -1 - none
    property bool going: false  // a time asked of the player, its frame not shown yet

    function toggle() { held = !held }
    function seek(ms) {
        held = true
        const to = Math.max(0, Math.min(ms, player.duration - 1))
        if (going) {
            wanted = to
            return
        }
        going = true
        settle.restart()
        player.setPosition(to)
    }
    function next() {  // the frame asked for is shown, or it has not come in a while
        if (wanted < 0) {
            going = false
            settle.stop()
            return
        }
        const to = wanted
        settle.restart()
        player.setPosition(to)
        wanted = -1
    }
    onHeldChanged: if (player.source.toString() !== "") held ? player.pause() : player.play()
    onFallbackChanged: held = false  // another video: it plays

    MediaPlayer {
        id: player
        objectName: "videoMedia"
        source: root.visible && root.mp4 ? root.source : ""
        videoOutput: output.videoSink
        loops: MediaPlayer.Infinite
        onSourceChanged: {
            root.going = false
            root.wanted = -1
            if (source.toString() !== "")
                root.held ? pause() : play()
        }
    }
    Connections {
        target: output.videoSink
        function onVideoFrameChanged() { if (root.going) root.next() }
    }
    Timer {  // a time that brings no frame does not hold the rest
        id: settle
        interval: 250
        onTriggered: root.next()
    }
    VideoSurface {  // the frame, its shape kept
        id: output
        objectName: "videoOutput"
        anchors.fill: parent
        visible: root.mp4
    }
    AnimatedImage {  // the WebP: its first frame until the MP4 is there; with none to be made, read forward, not kept
        id: still
        objectName: "videoStill"
        anchors.fill: parent
        visible: !root.mp4 && root.fallback !== ""
        source: root.visible && !root.mp4 ? root.fallback : ""
        cache: false
        fillMode: Image.PreserveAspectFit
        playing: visible && root.source === root.fallback
        paused: root.held
    }
}
