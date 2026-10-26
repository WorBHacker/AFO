import QtQuick
import QtQuick.Controls.Basic

// The window (D36): its own title bar, chats on the left, the conversation and the composer, a 26px status bar.
// Drawn by the CPU through Qt Quick's software scene graph (D3).
Window {
    id: root
    width: 1280
    height: 820
    minimumWidth: 900
    minimumHeight: 560
    visible: true
    title: "LocalChat"
    color: Theme.p.window
    flags: Qt.Window | Qt.FramelessWindowHint

    readonly property bool maximized: visibility === Window.Maximized

    Sidebar {
        id: sidebar
        width: 264
        height: parent.height - statusBar.height
        onThemeMenuRequested: (anchor) => themeMenu.openAt(anchor)
        onSettingsRequested: settingsSheet.open()
        onChatMenuRequested: (chatId, at) => chatMenu.openFor(chatId, at)
    }

    Item {
        id: main
        x: sidebar.width
        width: parent.width - sidebar.width
        height: parent.height - statusBar.height

        TitleBar {
            id: titleBar
            width: parent.width
            onSearchRequested: palette.open()
        }
        ChatView {
            y: titleBar.height
            width: parent.width
            height: parent.height - titleBar.height - composer.height
        }
        Composer {
            id: composer
            anchors.bottom: parent.bottom
            width: parent.width
            onModelMenuRequested: (anchor) => modelMenu.openAt(anchor)
            onEffortMenuRequested: (anchor) => effortMenu.openAt(anchor)
        }
    }

    StatusBar {
        id: statusBar
        anchors.bottom: parent.bottom
        width: parent.width
        onBudgetsRequested: budgetsSheet.open()
    }

    // ---------- menus, panels, dialogs ----------

    MenuPopup {
        id: modelMenu
        menuWidth: 430
        items: App.pickerItems
        onChosen: (id) => App.chooseModel(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: effortMenu
        menuWidth: 260
        items: App.efforts.map(e => ({ id: e.value, name: e.label, icon: "sparkle", selected: e.value === App.effort }))
        onChosen: (id) => App.chooseEffort(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: themeMenu
        menuWidth: 220
        items: [
            { id: "system", name: "Как в системе", icon: "layers", selected: Theme.mode === "system" },
            { id: "light", name: "Светлая", icon: "sun", selected: Theme.mode === "light" },
            { id: "dark", name: "Тёмная", icon: "moon", selected: Theme.mode === "dark" }
        ]
        onChosen: (id) => App.setTheme(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: chatMenu
        property string chatId
        menuWidth: 200
        items: [{ id: "rename", name: "Переименовать", icon: "edit" }, { id: "delete", name: "Удалить", icon: "trash" }]
        onChosen: (id) => id === "rename" ? renameDialog.openFor(chatId) : App.askDeleteChat(chatId)
        function openFor(id, point) { chatId = id; at = point; open() }
    }
    CommandPalette { id: palette }
    BudgetsSheet { id: budgetsSheet }
    SettingsSheet { id: settingsSheet }
    RenameDialog { id: renameDialog }
    ConfirmDialog {}

    Connections {
        target: App
        function onUiRequested(what) {
            if (what === "budgets") budgetsSheet.open()
            else if (what === "settings") settingsSheet.open()
            else if (what === "rename") renameDialog.openFor(App.currentId)
            else if (what === "models") modelMenu.openAt(composer.modelChip)
        }
    }

    // ---------- keyboard ----------

    Shortcut { sequence: "Ctrl+N"; onActivated: { App.newChat(); composer.focusInput() } }
    Shortcut { sequence: "Ctrl+L"; onActivated: composer.focusInput() }
    Shortcut { sequence: "Ctrl+K"; onActivated: palette.open() }
    Shortcut { sequence: "Ctrl+B"; onActivated: budgetsSheet.open() }
    Shortcut { sequence: "Ctrl+,"; onActivated: settingsSheet.open() }
    Shortcut { sequence: "F2"; enabled: App.currentId !== ""; onActivated: renameDialog.openFor(App.currentId) }
    Shortcut { sequence: "Ctrl+Shift+U"; onActivated: App.unloadLocal() }

    // ---------- resizing a frameless window from its edges ----------

    component Edge: MouseArea {
        property int edges
        enabled: !root.maximized
        hoverEnabled: true
        onPressed: root.startSystemResize(edges)
    }
    Edge { edges: Qt.LeftEdge; x: 0; y: 8; width: 5; height: parent.height - 16; cursorShape: Qt.SizeHorCursor }
    Edge { edges: Qt.RightEdge; x: parent.width - 5; y: 8; width: 5; height: parent.height - 16; cursorShape: Qt.SizeHorCursor }
    Edge { edges: Qt.TopEdge; x: 8; y: 0; width: parent.width - 16; height: 4; cursorShape: Qt.SizeVerCursor }
    Edge { edges: Qt.BottomEdge; x: 8; y: parent.height - 4; width: parent.width - 16; height: 4; cursorShape: Qt.SizeVerCursor }
    Edge { edges: Qt.LeftEdge | Qt.TopEdge; width: 8; height: 8; cursorShape: Qt.SizeFDiagCursor }
    Edge { edges: Qt.RightEdge | Qt.TopEdge; x: parent.width - 8; width: 8; height: 8; cursorShape: Qt.SizeBDiagCursor }
    Edge { edges: Qt.LeftEdge | Qt.BottomEdge; y: parent.height - 8; width: 8; height: 8; cursorShape: Qt.SizeBDiagCursor }
    Edge {
        edges: Qt.RightEdge | Qt.BottomEdge
        x: parent.width - 8; y: parent.height - 8; width: 8; height: 8
        cursorShape: Qt.SizeFDiagCursor
    }
}
