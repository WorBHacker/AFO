import QtQuick

// Answer text: selectable rich text (HTML from app/render.py); links open in the browser.
TextEdit {
    id: prose
    property var info: ({})
    // a string property notifies only when its value changes: the same HTML again is not laid out again
    property string html: info.html || ""
    readOnly: true
    selectByMouse: true
    textFormat: TextEdit.RichText
    wrapMode: TextEdit.Wrap
    text: html
    color: Theme.p.text_soft
    selectionColor: Theme.p.selection
    selectedTextColor: Theme.p.text
    font.family: Theme.uiFont
    font.pixelSize: 14
    onLinkActivated: (link) => App.openLink(link)
    HoverHandler { cursorShape: prose.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
}
