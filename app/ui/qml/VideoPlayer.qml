import QtQuick
import QtMultimedia
import AFO.Video

// A video (48.12): Qt Multimedia's player over the hub's MP4 - it goes to any frame and keeps none of them in memory
// (AnimatedImage kept every frame to go back: 960×960×73 - 257 MiB). The hub makes the MP4 as the video is filmed
// (48.12.8), an older one's the first time it is shown: until it is there the WebP plays, as Qt reads it, a frame at a
// time, without seeking - and the MP4 takes on from the frame the WebP was at («Стабилизация» 2026-11-27, «в чате не
// работает воспроизведение видео»: the first time ffmpeg is fetched, ~86 MB, the first frame stood still for minutes;
// before that, 48.12.8, the MP4 began from its start and the clip began twice); with none to be made the WebP goes on
// so. Hidden - its own view or any above it - it lets the clip go;
// the window in the tray or minimized - it pauses (48.12: it went on playing and decoding), and goes on from there.
// VideoControls drives it. The frames are drawn by VideoSurface (app/ui/video_surface.py, 48.12.7): VideoOutput draws
// with the card's shaders, and the window's software scene graph drew nothing of it - the live check saw the
// background. A clip with sound (52.5) is heard: the volume and «без звука» are the window's own, one for every
// player (Visual); `loud` - whether the MP4 has sound at all.
Item {
    id: root
    property string source: ""  // the MP4, a file's URL; "" - not made yet; the WebP's own - none can be made
    property string fallback: ""  // the WebP, the hub's file
    property bool held: false  // paused by the person
    // the window hidden in the tray or minimized: nobody sees it - paused, not let go (its place in the clip kept)
    readonly property bool away: root.Window.visibility === Window.Hidden || root.Window.visibility === Window.Minimized
    readonly property bool stopped: root.held || root.away
    readonly property bool mp4: root.source !== "" && root.source !== root.fallback
    readonly property bool seekable: root.mp4 && player.seekable && player.duration > 0
    readonly property int position: root.wanted >= 0 ? root.wanted : player.position
    readonly property int duration: root.mp4 ? player.duration : 0
    readonly property bool loud: root.mp4 && player.hasAudio
    readonly property bool playing: player.playbackState === MediaPlayer.PlayingState || (still.playing && !root.stopped)
    // the frame's size, for a view that takes its shape (a chat's card): the MP4's, or the WebP's while it stands
    readonly property size frame: root.mp4 ? Qt.size(output.frameSize.width, output.frameSize.height)
                                           : Qt.size(still.implicitWidth, still.implicitHeight)
    // (48.12.8) the slider asks for a time at each move of the mouse - each 4 ms at a 240 Hz screen - and each asked of
    // the player dropped the one it was going to: a drag across a 960×960 clip showed 2 frames in 1.9 s. One at a time
    // now: the next once the frame of the one before is shown, the latest asked meanwhile, the slider at it
    property int wanted: -1  // the latest time asked while the player was going to another; -1 - none
    property bool going: false  // a time asked of the player, its frame not shown yet
    property real at: 0  // how far into the clip the WebP is while it plays, 0 to 1 - where the MP4 takes on
    property real handover: -1  // that share, kept for the MP4 until it knows its length; -1 - none

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
    onStoppedChanged: if (player.source.toString() !== "") stopped ? player.pause() : player.play()
    onFallbackChanged: { held = false; at = 0 }  // another video: it plays, from its start

    MediaPlayer {
        id: player
        objectName: "videoMedia"
        source: root.visible && root.mp4 ? root.source : ""
        videoOutput: output.videoSink
        audioOutput: AudioOutput {
            objectName: "videoSpeaker"
            volume: Visual.volume
            muted: Visual.muted
        }
        loops: MediaPlayer.Infinite
        onSourceChanged: {
            root.going = false
            root.wanted = -1
            root.handover = source.toString() !== "" && root.at > 0 ? root.at : -1
            if (source.toString() !== "")
                root.stopped ? pause() : play()
        }
        // the frame the WebP was at: on from there, once the player may go to it (before that it is not heard)
        function takeOn() {
            const loaded = mediaStatus === MediaPlayer.LoadedMedia || mediaStatus === MediaPlayer.BufferingMedia
                           || mediaStatus === MediaPlayer.BufferedMedia
            if (root.handover < 0 || duration <= 0 || !seekable || !loaded)
                return
            setPosition(Math.floor(root.handover * duration))
            root.handover = -1
        }
        onDurationChanged: takeOn()
        onSeekableChanged: takeOn()
        onMediaStatusChanged: takeOn()
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
    AnimatedImage {  // the WebP until the MP4 is there, or with none to be made: read forward, not kept
        id: still
        objectName: "videoStill"
        anchors.fill: parent
        visible: !root.mp4 && root.fallback !== ""
        source: root.visible && !root.mp4 ? root.fallback : ""
        cache: false
        fillMode: Image.PreserveAspectFit
        playing: visible
        paused: root.stopped
        // not when it is let go for the MP4: it stops, and its frame goes back to 0, before its source is gone
        onCurrentFrameChanged: if (playing && visible && source.toString() !== "" && status === Image.Ready
                                   && frameCount > 1)
            root.at = currentFrame / frameCount
    }
}
