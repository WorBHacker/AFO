import QtQuick

// A git mark beside a file (M changed, A added, D deleted, R renamed, U new, ! conflict; • a folder with changes),
// in its colour; `colour` is also there for the file's name.
Text {
    property string mark
    readonly property color colour: mark === "M" ? Theme.p.warn
                                    : mark === "A" || mark === "U" ? Theme.p.ok
                                    : mark === "D" || mark === "!" ? Theme.p.error
                                    : mark === "R" ? Theme.p.accent : Theme.p.muted
    text: mark
    color: colour
    font.family: Theme.monoFont
    font.pixelSize: 11
    font.weight: Font.DemiBold
}
