import QtQuick
import QtQuick.Controls.Basic

// The window's own title bar: the chat's title (or the project's, with its git state), the command search, the
// feed, the window buttons. Drag it to move the window, double-click to maximize.
Item {
    id: bar
    signal searchRequested()
    signal feedRequested()
    signal approvalsRequested()
    signal agentsRequested()
    height: 52

    // the window through the bar: a handler is not an Item, its own Window.window is null
    DragHandler { target: null; onActiveChanged: if (active) bar.Window.window.startSystemMove() }
    TapHandler { onDoubleTapped: bar.toggleMaximized() }

    function toggleMaximized() {
        const w = Window.window
        if (w.visibility === Window.Maximized) w.showNormal()
        else w.showMaximized()
    }

    Column {
        x: 24
        width: Math.max(0, search.x - 48)
        anchors.verticalCenter: parent.verticalCenter
        spacing: 1
        Text {
            width: parent.width
            text: App.mode === "projects" ? (Projects.currentName || "Проекты") : App.currentTitle
            elide: Text.ElideRight
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 14
            font.weight: Font.DemiBold
        }
        Text {
            width: parent.width
            visible: text !== ""
            text: App.mode !== "projects" ? App.currentMeta
                  : Projects.currentId ? [Projects.git.summary, Projects.processesSummary].filter(s => !!s).join(" · ") : ""
            elide: Text.ElideRight
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
        }
    }

    AbstractButton {
        id: search
        anchors.centerIn: parent
        width: Math.min(360, parent.width - 520)
        height: 32
        hoverEnabled: true
        onClicked: bar.searchRequested()
        background: Rectangle {
            radius: 6
            color: Theme.p.card
            border.color: search.hovered ? Theme.p.hairline_strong : Theme.p.hairline
        }
        contentItem: Item {
            Row {
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Icon { name: "search"; size: 15; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: "Поиск и команды"
                    color: Theme.p.faint
                    font.family: Theme.uiFont
                    font.pixelSize: 13
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
            Kbd { text: "Ctrl K"; anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter }
        }
    }

    Row {
        anchors.right: parent.right
        anchors.rightMargin: 4
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        IconButton {
            id: approvals
            objectName: "approvalsButton"
            visible: Approvals.count > 0
            iconName: "shield"
            implicitWidth: 38
            tint: Theme.p.warn
            tip: "Подтверждения: ждут ответа " + Approvals.count + " (Ctrl+Shift+A)"
            onClicked: bar.approvalsRequested()
            Rectangle {
                x: parent.width - width - 4
                y: 5
                width: Math.max(16, count.implicitWidth + 8)
                height: 16
                radius: 8
                color: Theme.p.warn
                Text {
                    id: count
                    anchors.centerIn: parent
                    text: Approvals.count
                    color: Theme.p.on_accent
                    font.family: Theme.uiFont
                    font.pixelSize: 10
                    font.weight: Font.DemiBold
                }
            }
        }
        IconButton { objectName: "agentsButton"; iconName: "agent"; implicitWidth: 38; tip: "Агенты (Ctrl+Shift+G)"; onClicked: bar.agentsRequested() }
        IconButton { iconName: "activity"; implicitWidth: 38; tip: "Лента событий (Ctrl+E)"; onClicked: bar.feedRequested() }
        Rectangle { width: 1; height: 18; color: Theme.p.hairline; anchors.verticalCenter: parent.verticalCenter }
        IconButton { iconName: "min"; implicitWidth: 42; tip: "Свернуть"; onClicked: Window.window.showMinimized() }
        IconButton {
            iconName: "max"
            implicitWidth: 42
            tip: Window.window && Window.window.visibility === Window.Maximized ? "Восстановить" : "Развернуть"
            onClicked: bar.toggleMaximized()
        }
        IconButton {
            iconName: "close"
            implicitWidth: 42
            tip: "Закрыть (хаб продолжит работать)"
            hoverColour: "#c42b1c"
            hoverTint: "#ffffff"
            onClicked: Window.window.close()
        }
    }

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.p.hairline }
}
