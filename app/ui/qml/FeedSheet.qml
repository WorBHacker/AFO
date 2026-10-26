import QtQuick
import QtQuick.Controls.Basic

// The feed (D29): what happened - projects, terminals, ports, git, answers, budgets, the hub - newest first, by day,
// with a filter by kind. New events join at the top while the window runs.
Sheet {
    id: sheet
    objectName: "feedSheet"
    title: "Лента событий"
    iconName: "activity"
    width: Math.min(560, (parent ? parent.width : 960) * 0.9)
    onAboutToShow: Feed.load()

    Segmented {
        width: parent.width
        options: Feed.groups
        value: Feed.group
        onPicked: (id) => Feed.setGroup(id)
    }

    Text {
        visible: Feed.items.length === 0
        topPadding: 24
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        text: "Здесь пока ничего нет"
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 13
    }

    Column {
        objectName: "feedItems"
        width: parent.width
        spacing: 2
        Repeater {
            model: Feed.items
            delegate: Loader {
                required property var modelData
                width: parent.width
                sourceComponent: modelData.section ? dayRow : eventRow
                onLoaded: item.info = modelData
            }
        }
    }

    PlainButton {
        visible: Feed.more
        anchors.horizontalCenter: parent.horizontalCenter
        text: "Показать ещё"
        onClicked: Feed.loadMore()
    }

    Component {
        id: dayRow
        Text {
            property var info: ({})
            height: 34
            verticalAlignment: Text.AlignBottom
            bottomPadding: 6
            text: info.section || ""
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
    }

    Component {
        id: eventRow
        Item {
            id: row
            property var info: ({})
            height: Math.max(36, texts.height + 12)
            Icon {
                id: mark
                y: 10
                name: row.info.icon || "dots"
                size: 15
                tint: row.info.level === "error" ? Theme.p.error : row.info.level === "warn" ? Theme.p.warn : Theme.p.muted
            }
            Column {
                id: texts
                x: 26
                y: 6
                width: parent.width - x - when.width - 12
                spacing: 2
                Text {
                    width: parent.width
                    text: row.info.title || ""
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                    color: row.info.level === "error" ? Theme.p.error : Theme.p.text
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    text: [row.info.project, row.info.detail].filter(s => !!s).join(" · ")
                    elide: Text.ElideRight
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 12
                }
            }
            Text {
                id: when
                anchors.right: parent.right
                y: 8
                text: row.info.time || ""
                color: Theme.p.faint
                font.family: Theme.uiFont
                font.pixelSize: 12
            }
        }
    }
}
