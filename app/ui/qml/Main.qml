import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs

// The window (D36): its own title bar, chats or projects on the left, the conversation and the composer (or the
// project: its overview or a file), a 26px status bar.
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
        onProjectMenuRequested: (anchor) => projectMenu.openBelow(anchor)
        onAddProjectRequested: folderDialog.open()
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
            onFeedRequested: feedSheet.open()
            onApprovalsRequested: waitingPopup.open()
            onAgentsRequested: agentsSheet.open()
        }
        ChatView {
            visible: App.mode === "chats"
            reserve: approvalBanner.visible ? approvalBanner.height + 10 : 0
            y: titleBar.height
            width: parent.width
            height: parent.height - titleBar.height - composer.height
        }
        ProjectView {
            visible: App.mode === "projects"
            y: titleBar.height
            width: parent.width
            height: parent.height - titleBar.height - (terminalPanel.visible ? terminalPanel.height : 0)
            onAddRequested: folderDialog.open()
            onSettingsRequested: projectSheet.open()
            onCardEditRequested: (text, draft) => cardSheet.openWith(text, draft)
            onExportRequested: (anchor) => { Projects.loadExport(); exportMenu.openAt(anchor) }
        }
        TerminalPanel {
            id: terminalPanel
            visible: App.mode === "projects" && Projects.currentId !== "" && Terminals.panelOpen
            anchors.bottom: parent.bottom
            width: parent.width
            height: Math.min(Terminals.panelHeight, parent.height - titleBar.height - 160)
        }
        Composer {
            id: composer
            visible: App.mode === "chats"
            anchors.bottom: parent.bottom
            width: parent.width
            onModelMenuRequested: (anchor) => modelMenu.openAt(anchor)
            onEffortMenuRequested: (anchor) => effortMenu.openAt(anchor)
            onProjectMenuRequested: (anchor) => chatProjectMenu.openAt(anchor)
            onContextRequested: contextSheet.open()
        }
        // the open chat's requests: a card over the top of its conversation (D38)
        ApprovalBanner {
            id: approvalBanner
            z: 2
            column: Math.min(780, parent.width - 48)
            x: (parent.width - width) / 2
            y: titleBar.height + 10
            room: parent.height - y - composer.height - 10
            onFinished: composer.focusInput()
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
    MenuPopup {
        id: chatProjectMenu
        menuWidth: 340
        items: [{ section: "Проекты этого чата", id: "" }].concat(App.chatProjectChoices)
        onChosen: (id) => App.toggleChatProject(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: projectMenu
        menuWidth: 320
        items: Projects.projectList.map(p => ({ id: p.id, name: p.name, note: p.path, icon: "folder", selected: p.selected }))
                   .concat([{ section: " ", id: "" }, { id: "+add", name: "Добавить проект…", icon: "folder-plus" }])
        onChosen: (id) => id === "+add" ? folderDialog.open() : Projects.openProject(id)
        function openBelow(item) { at = item.mapToItem(null, 0, item.height + 4); open() }
    }
    MenuPopup {
        id: exportMenu
        objectName: "exportMenu"
        menuWidth: 360
        items: [{ section: "Записать карточку в проект", id: "" }].concat(Projects.exportTargets)
        onChosen: (id) => Projects.exportCard(id)
        function openAt(item) { anchor = item; open() }
    }
    FolderDialog {
        id: folderDialog
        title: "Папка проекта"
        onAccepted: Projects.addProject(selectedFolder.toString())
    }
    CommandPalette { id: palette }
    BudgetsSheet { id: budgetsSheet }
    SettingsSheet { id: settingsSheet }
    FeedSheet { id: feedSheet }
    WaitingPopup { id: waitingPopup }
    AgentsSheet { id: agentsSheet }
    ConnectionsSheet { id: connectionsSheet }
    ProjectSheet { id: projectSheet }
    ContextSheet { id: contextSheet }
    CardSheet { id: cardSheet }
    ProgramsSheet { id: programsSheet }
    NewProgramPopup {}
    RenameDialog { id: renameDialog }
    ConfirmDialog {}

    Connections {
        target: App
        function onUiRequested(what) {
            if (what === "budgets") budgetsSheet.open()
            else if (what === "settings") settingsSheet.open()
            else if (what === "feed") feedSheet.open()
            else if (what === "approvals") waitingPopup.open()
            else if (what === "agents") agentsSheet.open()
            else if (what === "programs") programsSheet.open()
            else if (what === "connections") connectionsSheet.open()
            else if (what === "rename") renameDialog.openFor(App.currentId)
            else if (what === "models") modelMenu.openAt(composer.modelChip)
            else if (what === "add-project") folderDialog.open()
            else if (what === "search-project") Qt.callLater(sidebar.focusProjectSearch)
        }
    }

    // ---------- keyboard ----------

    Shortcut { sequence: "Ctrl+N"; onActivated: { App.setMode("chats"); App.newChat(); composer.focusInput() } }
    Shortcut { sequence: "Ctrl+L"; onActivated: { App.setMode("chats"); composer.focusInput() } }
    Shortcut { sequence: "Ctrl+1"; onActivated: App.setMode("chats") }
    Shortcut { sequence: "Ctrl+2"; onActivated: App.setMode("projects") }
    Shortcut { sequence: "F5"; enabled: App.mode === "projects"; onActivated: Projects.refresh() }
    Shortcut {
        sequences: ["Ctrl+J", "Ctrl+`"]
        onActivated: {
            App.setMode("projects")
            Terminals.setPanelOpen(!Terminals.panelOpen)
            if (Terminals.panelOpen) terminalPanel.focusScreen()
        }
    }
    Shortcut { sequence: "Ctrl+Shift+`"; onActivated: { App.setMode("projects"); Terminals.newTerminal() } }
    Shortcut {
        sequence: "Ctrl+Shift+F"
        onActivated: { App.setMode("projects"); Qt.callLater(sidebar.focusProjectSearch) }
    }
    Shortcut { sequence: "Ctrl+K"; onActivated: palette.open() }
    Shortcut { sequence: "Ctrl+B"; onActivated: budgetsSheet.open() }
    Shortcut { sequence: "Ctrl+E"; onActivated: feedSheet.open() }
    Shortcut { sequence: "Ctrl+Shift+A"; onActivated: waitingPopup.open() }
    Shortcut { sequence: "Ctrl+Shift+G"; onActivated: agentsSheet.open() }
    Shortcut { sequence: "Ctrl+Shift+K"; onActivated: connectionsSheet.open() }
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
