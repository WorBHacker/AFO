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
    title: "AFO"
    color: Theme.p.window
    flags: Qt.Window | Qt.FramelessWindowHint

    readonly property bool maximized: visibility === Window.Maximized

    // unsaved edits in the editor's tabs (D47, 14.4): asked before the window goes; it goes once they are decided
    onClosing: (close) => { if (!Editor.mayClose()) close.accepted = false }
    Connections {
        target: Editor
        function onCloseWindow() { root.close() }
    }

    Sidebar {
        id: sidebar
        objectName: "sidebar"
        width: Math.max(200, Math.min(App.sidebarWidth, root.width - 480))  // the main area keeps 480px
        height: parent.height - statusBar.height
        onThemeMenuRequested: (anchor) => themeMenu.openAt(anchor)
        onSettingsRequested: settingsSheet.open()
        onChatMenuRequested: (chatId, at) => chatMenu.openFor(chatId, at)
        onProjectMenuRequested: (anchor) => projectMenu.openBelow(anchor)
        onAddProjectRequested: folderDialog.open()
    }

    // the sidebar's right edge moves left and right (2026-10-28, the user's word); a double click: the usual width
    MouseArea {
        id: sidebarGrip
        objectName: "sidebarGrip"
        z: 3
        x: sidebar.width - 4
        width: 8
        height: sidebar.height
        hoverEnabled: true
        cursorShape: Qt.SizeHorCursor
        property real startX
        property int startWidth
        onPressed: (mouse) => { startX = mapToItem(null, mouse.x, 0).x; startWidth = sidebar.width }
        onPositionChanged: (mouse) => {
            if (pressed) App.setSidebarWidth(startWidth + (mapToItem(null, mouse.x, 0).x - startX))
        }
        onDoubleClicked: App.setSidebarWidth(0)
        Rectangle {
            x: 3
            width: 2
            height: parent.height
            visible: sidebarGrip.containsMouse || sidebarGrip.pressed
            color: Theme.p.accent
        }
        ToolTip.visible: containsMouse && !pressed
        ToolTip.delay: 800
        ToolTip.text: qsTr("Потяните влево или вправо. Двойной щелчок — обычная ширина")
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
            visible: App.mode === "chats" || App.showProjectChat
            reserve: approvalBanner.visible ? approvalBanner.height + 10 : 0
            y: titleBar.height
            width: parent.width
            height: parent.height - titleBar.height - composer.height - (sandboxBar.visible ? sandboxBar.height : 0)
                    - (folderBar.visible ? folderBar.height : 0)
                    - (goalBar.visible ? goalBar.height : 0) - (tasksBar.visible ? tasksBar.height : 0)
        }
        TasksBar {
            id: tasksBar
            visible: (App.mode === "chats" || App.showProjectChat) && Tasks.shown
            anchors.bottom: goalBar.visible ? goalBar.top : sandboxBar.visible ? sandboxBar.top : folderBar.visible
                            ? folderBar.top : composer.top
            width: parent.width
            onBoardRequested: tasksSheet.open()
        }
        GoalBar {
            id: goalBar
            visible: (App.mode === "chats" || App.showProjectChat) && Goal.shown
            anchors.bottom: sandboxBar.visible ? sandboxBar.top : folderBar.visible ? folderBar.top : composer.top
            width: parent.width
        }
        SandboxBar {
            id: sandboxBar
            visible: (App.mode === "chats" || App.showProjectChat) && Sandbox.active
            anchors.bottom: folderBar.visible ? folderBar.top : composer.top
            width: parent.width
            onReviewRequested: sandboxSheet.open()
        }
        ChatFolderBar {  // what Claude Code made in a chat with no project (31.7)
            id: folderBar
            visible: (App.mode === "chats" || App.showProjectChat) && ChatFolder.shown
            anchors.bottom: composer.top
            width: parent.width
        }
        AgentView {
            visible: App.mode === "agent"
            y: titleBar.height
            width: parent.width
            height: parent.height - titleBar.height
            reserveTop: approvalBanner.visible ? approvalBanner.height + 10 : 0
        }
        ProjectView {
            visible: App.mode === "projects" && !App.showProjectChat
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
            visible: App.mode === "projects" && !App.showProjectChat && Projects.currentId !== "" && Terminals.panelOpen
            anchors.bottom: parent.bottom
            width: parent.width
            height: Math.min(Terminals.panelHeight, parent.height - titleBar.height - 160)
        }
        Composer {
            id: composer
            visible: App.mode === "chats" || App.showProjectChat
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
        items: App.pickerItems.concat([{ section: " ", id: "" },
                                       { id: "+settings", name: qsTr("Настроить модели…"), icon: "sliders" },
                                       { id: "+install", name: qsTr("Установить локальную модель…"), icon: "download" }])
        onChosen: (id) => id === "+settings" ? modelsSheet.openFor(App.modelId)
                        : id === "+install" ? installSheet.open() : App.chooseModel(id)
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
            { id: "system", name: qsTr("Как в системе"), icon: "layers", selected: Theme.mode === "system" },
            { id: "light", name: qsTr("Светлая"), icon: "sun", selected: Theme.mode === "light" },
            { id: "dark", name: qsTr("Тёмная"), icon: "moon", selected: Theme.mode === "dark" }
        ]
        onChosen: (id) => App.setTheme(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: chatMenu
        property string chatId
        menuWidth: 200
        items: [{ id: "rename", name: qsTr("Переименовать"), icon: "edit" }, { id: "delete", name: qsTr("Удалить"), icon: "trash" }]
        onChosen: (id) => id === "rename" ? renameDialog.openFor(chatId) : App.askDeleteChat(chatId)
        function openFor(id, point) { chatId = id; at = point; open() }
    }
    MenuPopup {
        id: chatProjectMenu
        menuWidth: 340
        items: [{ section: qsTr("Проекты этого чата"), id: "" }].concat(App.chatProjectChoices)
        onChosen: (id) => App.toggleChatProject(id)
        function openAt(item) { anchor = item; open() }
    }
    MenuPopup {
        id: projectMenu
        menuWidth: 320
        items: Projects.projectList.map(p => ({ id: p.id, name: p.name, note: p.path, icon: "folder", selected: p.selected }))
                   .concat([{ section: " ", id: "" }, { id: "+add", name: qsTr("Добавить проект…"), icon: "folder-plus" }])
        onChosen: (id) => id === "+add" ? folderDialog.open() : Projects.openProject(id)
        function openBelow(item) { at = item.mapToItem(null, 0, item.height + 4); open() }
    }
    MenuPopup {
        id: exportMenu
        objectName: "exportMenu"
        menuWidth: 360
        items: [{ section: qsTr("Записать карточку в проект"), id: "" }].concat(Projects.exportTargets)
        onChosen: (id) => Projects.exportCard(id)
        function openAt(item) { anchor = item; open() }
    }
    FolderDialog {
        id: folderDialog
        title: qsTr("Папка проекта")
        onAccepted: Projects.addProject(selectedFolder.toString())
    }
    CommandPalette { id: palette }
    BudgetsSheet { id: budgetsSheet }
    SettingsSheet { id: settingsSheet }
    // the settings' categories: each a sheet of its own, its arrow back to the list (2026-11-05)
    SettingsLookSheet { id: settingsLook; backTo: settingsSheet }
    SettingsModelsSheet { id: settingsModels; backTo: settingsSheet }
    SettingsAgentSheet { id: settingsAgent; backTo: settingsSheet }
    SettingsStartupSheet { id: settingsStartup; backTo: settingsSheet }
    SettingsHubSheet { id: settingsHub; backTo: settingsSheet }
    SettingsTransferSheet { id: settingsTransfer; backTo: settingsSheet }
    SettingsAboutSheet { id: settingsAbout; backTo: settingsSheet }
    FeedSheet { id: feedSheet }
    WaitingPopup { id: waitingPopup }
    AgentsSheet { id: agentsSheet }
    ConnectionsSheet { id: connectionsSheet }
    ProjectSheet { id: projectSheet }
    ContextSheet { id: contextSheet }
    CardSheet { id: cardSheet }
    ProgramsSheet { id: programsSheet }
    ModelsSheet { id: modelsSheet }
    MemorySheet { id: memorySheet }
    AgentAccessSheet { id: accessSheet }
    SandboxSheet { id: sandboxSheet }
    TasksSheet { id: tasksSheet }
    GoalSheet { id: goalSheet }
    ProvingSheet { id: provingSheet }
    InstallSheet { id: installSheet }
    VisualSheet { id: visualSheet }
    WizardSheet { id: wizardSheet }
    CheckSheet { id: checkSheet }
    TransferSheet { id: transferSheet }
    NewProgramPopup { held: wizardSheet.opened }
    ComputerFrame {}  // the agent at the desktop: its frame and «Стоп» (D41)
    RenameDialog { id: renameDialog }
    ConfirmDialog {}

    // the wizard opening by itself (10.2) waits while the keys are in an open panel or list: it would take them from
    // a comment being typed in a card (2026-10-28); asked for from Settings, it opens at once
    property bool wizardWaits: false
    function openWizardWhenFree() {
        let item = root.activeFocusItem
        while (item && item !== Overlay.overlay)
            item = item.parent
        wizardWaits = !!item
        if (!wizardWaits)
            wizardSheet.open()
    }
    Timer { interval: 400; repeat: true; running: root.wizardWaits; onTriggered: root.openWizardWhenFree() }

    Connections {
        target: Goal
        function onOpenRequested() { goalSheet.open() }
    }
    Connections {
        target: App
        function onUiRequested(what) {
            if (what === "budgets") budgetsSheet.open()
            else if (what === "settings") settingsSheet.open()
            else if (what === "settings-look") settingsLook.open()
            else if (what === "settings-models") settingsModels.open()
            else if (what === "settings-agent") settingsAgent.open()
            else if (what === "settings-startup") settingsStartup.open()
            else if (what === "settings-hub") settingsHub.open()
            else if (what === "settings-transfer") settingsTransfer.open()
            else if (what === "settings-about") settingsAbout.open()
            else if (what === "feed") feedSheet.open()
            else if (what === "approvals") waitingPopup.open()
            else if (what === "agents") agentsSheet.open()
            else if (what === "programs") programsSheet.open()
            else if (what === "model-settings") modelsSheet.openFor("")
            else if (what === "install-models") installSheet.open()
            else if (what === "draw") visualSheet.open()
            else if (what === "setup") wizardSheet.open()
            else if (what === "setup-first-run") root.openWizardWhenFree()
            else if (what === "check-system") checkSheet.open()
            else if (what === "transfer") transferSheet.open()
            else if (what === "memory") memorySheet.open()
            else if (what === "agent-access") accessSheet.open()
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
    Shortcut { sequence: "Ctrl+3"; onActivated: App.setMode("agent") }
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
