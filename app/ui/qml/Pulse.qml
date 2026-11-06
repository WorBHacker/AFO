import QtQuick

// Something is going on (an answer, thinking, a draft, the agent at work): its `target` dims and brightens a step
// every `interval` while `running`. An endless opacity animation drew the whole window ~60 times a second for as long
// as it ran - all through an answer (24.5); this, twice a second.
Timer {
    property Item target
    property real low: 0.35
    interval: 700
    repeat: true
    onTriggered: if (target) target.opacity = target.opacity < 1 ? 1 : low
    onRunningChanged: if (!running && target) target.opacity = 1
}
