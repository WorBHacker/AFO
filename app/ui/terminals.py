"""The terminal panel (stage 2): the open project's terminals as tabs, the chosen one on screen, typing into it.

Terminals live in the hub (hub/terminals.py); the window only shows them. Each one the window knows gets its own
emulator (term_screen.TermScreen). The one on screen plays every `terminal.output` event as it comes; the others keep
the raw output and play it when they show (D46: parsing costs the window's thread on every output, up to 33 a second
per terminal) - up to WAITING_MAX, past that the hub's memory has it. A terminal opened for the first time reads its
last output first and then takes the events after it (`offset` tells which it already has); output the window missed
(events lost in a break of its link to the hub) shows as a gap in the offsets and is read from the hub by offset.
Keys become the sequences a console expects and go to the hub through one worker thread, in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyte
from PySide6.QtCore import Property, QModelIndex, QObject, QPersistentModelIndex, Qt, Signal, Slot

from common.i18n import tr

from ..tasks import Background
from .models import RowModel
from .term_screen import Scrolled, TermScreen

if TYPE_CHECKING:
    from .controller import Controller

LINE_ROLES = ["key", "html"]
RESIZE_DELAY_MS = 150
RECENT_ENDED = 5  # ended terminals shown as tabs besides the running ones
WAITING_MAX = 256 * 1024  # raw output kept for a terminal off screen; past that it is read from the hub when it shows

K = Qt.Key
CTRL = Qt.KeyboardModifier.ControlModifier.value
ALT = Qt.KeyboardModifier.AltModifier.value
SHIFT = Qt.KeyboardModifier.ShiftModifier.value
ARROWS = {K.Key_Up.value: "A", K.Key_Down.value: "B", K.Key_Right.value: "C", K.Key_Left.value: "D"}
HOME_END = {K.Key_Home.value: "H", K.Key_End.value: "F"}
TILDE = {
    K.Key_Insert.value: 2, K.Key_Delete.value: 3, K.Key_PageUp.value: 5, K.Key_PageDown.value: 6,
    K.Key_F5.value: 15, K.Key_F6.value: 17, K.Key_F7.value: 18, K.Key_F8.value: 19, K.Key_F9.value: 20,
    K.Key_F10.value: 21, K.Key_F11.value: 23, K.Key_F12.value: 24,
}  # fmt: skip
SS3 = {K.Key_F1.value: "P", K.Key_F2.value: "Q", K.Key_F3.value: "R", K.Key_F4.value: "S"}


def key_sequence(key: int, modifiers: int, text: str, *, app_cursor: bool = False) -> str:
    """What a console expects for a key press ('' for keys it does not take)."""
    ctrl, alt, shift = bool(modifiers & CTRL), bool(modifiers & ALT), bool(modifiers & SHIFT)
    mod = 1 + shift + 2 * alt + 4 * ctrl
    if key in ARROWS or key in HOME_END:
        letter = ARROWS.get(key) or HOME_END[key]
        if mod > 1:
            return f"\x1b[1;{mod}{letter}"
        return ("\x1bO" if app_cursor else "\x1b[") + letter
    if key in TILDE:
        return f"\x1b[{TILDE[key]};{mod}~" if mod > 1 else f"\x1b[{TILDE[key]}~"
    if key in SS3:
        return f"\x1b[1;{mod}{SS3[key]}" if mod > 1 else f"\x1bO{SS3[key]}"
    if key in (K.Key_Return.value, K.Key_Enter.value):
        return "\r"
    if key == K.Key_Backspace.value:
        return "\x08" if ctrl else "\x7f"
    if key == K.Key_Backtab.value or (key == K.Key_Tab.value and shift):
        return "\x1b[Z"
    if key == K.Key_Tab.value:
        return "\t"
    if key == K.Key_Escape.value:
        return "\x1b"
    if ctrl and not alt:
        if K.Key_A.value <= key <= K.Key_Z.value:
            return chr(key - K.Key_A.value + 1)
        special = {K.Key_Space.value: "\x00", K.Key_BracketLeft.value: "\x1b", K.Key_Backslash.value: "\x1c",
                   K.Key_BracketRight.value: "\x1d"}  # fmt: skip
        if key in special:
            return special[key]
    if text and text.isprintable():
        return ("\x1b" + text) if alt and not ctrl else text
    return ""


@dataclass(eq=False)
class _Emu:
    """One terminal as the window plays it."""

    screen: TermScreen
    stream: pyte.Stream
    end: int | None = None  # the offset the next output starts at; None until the tail is read
    loaded: bool = False
    loading: bool = False  # its output is being read from the hub: events wait meanwhile
    waiting: list[tuple[int, str]] = field(default_factory=list)  # events not played yet: off screen, or loading
    waiting_chars: int = 0
    missed: bool = False  # output after `end` never reached the window: read from the hub when it shows


class Lines(RowModel):
    """The terminal's lines for the view: a scrollback line (term_screen.Scrolled) becomes rich text when the view
    first asks for it - a flood of output that scrolls by unseen costs no rendering (D46, step 13.7)."""

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        value = super().data(index, role)
        return value.html if isinstance(value, Scrolled) else value


class Terminals(QObject):
    tabsChanged = Signal()
    currentChanged = Signal()
    cursorChanged = Signal()
    panelChanged = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self.lines = Lines(self, roles=LINE_ROLES)
        self._infos: dict[str, dict[str, Any]] = {}  # the open project's terminals, as the hub describes them
        self._emus: dict[str, _Emu] = {}
        self._current = ""
        self._project = ""
        self._cols, self._rows = 120, 30
        self._shown = (0, 0)  # scrollback lines and screen lines in the line model now
        self._shown_base = 0  # the scrollback lines let go before the first one in the line model
        app.modeChanged.connect(self._catch_up)
        self._open = str(app.settings.value("terminals/open", "false")) == "true"
        self._height = int(app.settings.value("terminals/height", 300))
        self._sender = Background(self, threads=1)  # keys go out one after another, in order
        self._sender.error.connect(app.show_error)
        self._resize_timer = app._timer(RESIZE_DELAY_MS, self._send_size, single=True)
        app.theme.changed.connect(self._restyle)

    # ================= properties for QML =================

    @Property(list, notify=tabsChanged)
    def tabs(self) -> list:
        running = [t for t in self._infos.values() if t["status"] == "running"]
        ended = sorted((t for t in self._infos.values() if t["status"] != "running"), key=lambda t: t["created_at"])
        shown = sorted(running, key=lambda t: t["created_at"]) + ended[-RECENT_ENDED:]
        return [
            {
                "id": t["id"],
                "title": _title(t),
                "running": t["status"] == "running",
                "note": _note(t),
                "selected": t["id"] == self._current,
            }
            for t in shown
        ]

    @Property(str, notify=currentChanged)
    def currentId(self) -> str:
        return self._current

    @Property(str, notify=currentChanged)
    def currentNote(self) -> str:
        info = self._infos.get(self._current)
        return f"{info['command']} · {info['cwd']}" if info else ""

    @Property(bool, notify=currentChanged)
    def currentRunning(self) -> bool:
        info = self._infos.get(self._current)
        return bool(info) and info["status"] == "running"

    @Property(QObject, constant=True)
    def lineModel(self) -> RowModel:
        return self.lines

    @Property(int, notify=cursorChanged)
    def cursorX(self) -> int:
        emu = self._emus.get(self._current)
        return emu.screen.cursor.x if emu else 0

    @Property(int, notify=cursorChanged)
    def cursorY(self) -> int:
        emu = self._emus.get(self._current)
        return len(emu.screen.scrolled) + emu.screen.cursor.y if emu else 0

    @Property(bool, notify=cursorChanged)
    def cursorVisible(self) -> bool:
        emu = self._emus.get(self._current)
        return bool(emu) and emu.loaded and emu.screen.cursor_visible and self.currentRunning

    @Property(bool, notify=panelChanged)
    def panelOpen(self) -> bool:
        return self._open

    @Property(int, notify=panelChanged)
    def panelHeight(self) -> int:
        return self._height

    # ================= the panel =================

    @Slot(bool)
    def setPanelOpen(self, open_: bool) -> None:
        if open_ != self._open:
            self._open = open_
            self.app.settings.setValue("terminals/open", "true" if open_ else "false")
            self.panelChanged.emit()
            if open_ and not self._current and self._project:
                running = [t for t in self.tabs if t["running"]]
                if running:
                    self.openTerminal(running[0]["id"])
            self._catch_up()

    def _visible(self) -> bool:
        """The panel shows (QML: projects mode, a project open, the panel open)."""
        return self._open and self.app.mode == "projects"

    def _on_screen(self, terminal_id: str) -> bool:
        return terminal_id == self._current and self._visible()

    def _catch_up(self) -> None:
        """The panel shows again: what its terminal printed meanwhile is played and laid out now."""
        emu = self._emus.get(self._current)
        if emu is not None and emu.loaded and (emu.waiting or emu.missed) and self._visible():
            self._bring_up(self._current, emu)

    def resync(self) -> None:
        """Back after a break in the link to the hub: the break's output events are gone. Every terminal the window
        played reads what it missed when it shows - the one on screen at once."""
        for emu in self._emus.values():
            if emu.loaded and not emu.loading and emu.end is not None:
                emu.missed = True
        self._catch_up()

    @Slot(int)
    def setPanelHeight(self, height: int) -> None:
        self._height = max(120, min(height, 2000))
        self.app.settings.setValue("terminals/height", self._height)
        self.panelChanged.emit()

    @Slot(int, int)
    def setViewSize(self, cols: int, rows: int) -> None:
        """The panel's size in characters; the open terminal follows it (the hub is told after a moment)."""
        cols, rows = max(20, min(cols, 500)), max(5, min(rows, 200))
        if (cols, rows) == (self._cols, self._rows):
            return
        self._cols, self._rows = cols, rows
        emu = self._emus.get(self._current)
        if emu is not None:
            self._fit(emu)
            self._show(emu)
        self._resize_timer.start()

    def _fit(self, emu: _Emu) -> None:
        if (emu.screen.columns, emu.screen.lines) != (self._cols, self._rows):
            emu.screen.resize(self._rows, self._cols)

    def _send_size(self) -> None:
        if self.currentRunning:
            tid, cols, rows = self._current, self._cols, self._rows
            self._sender.run(lambda: self.app.client.resize_terminal(tid, cols, rows), on_error=lambda _e: None)

    # ================= the list =================

    def set_project(self, project_id: str) -> None:
        """The open project changed: its terminals become the tabs."""
        if project_id == self._project:
            return
        self._project = project_id
        self._infos, self._emus, self._current = {}, {}, ""
        self.lines.reset([])
        self.tabsChanged.emit()
        self.currentChanged.emit()
        self.cursorChanged.emit()
        self.load()

    def load(self) -> None:
        if not self._project or self.app.client is None:
            return
        pid = self._project

        def show(found: list[dict[str, Any]]) -> None:
            if pid != self._project:
                return
            self._infos = {t["id"]: t for t in found}
            self.tabsChanged.emit()
            if self._current not in self._infos:
                self._current = ""
                running = [t for t in self.tabs if t["running"]]
                if running and self._open:
                    self.openTerminal(running[-1]["id"])
                else:
                    self.lines.reset([])
                    self.currentChanged.emit()

        self.app._call(lambda: self.app.client.terminals(pid), show, lambda _e: None)

    @Slot()
    def newTerminal(self) -> None:
        if not self._project:
            self.app.show_error(tr("Терминал открывается в проекте: сначала выберите проект"))
            return
        pid, cols, rows = self._project, self._cols, self._rows

        def done(info: dict[str, Any]) -> None:
            if pid != self._project:
                return
            self._infos[info["id"]] = info
            self._emus.setdefault(info["id"], self._new_emu())
            self.tabsChanged.emit()
            self.setPanelOpen(True)
            self.openTerminal(info["id"])

        self.app._call(
            lambda: self.app.client.start_terminal(pid, cols=cols, rows=rows),
            done,
            lambda error: self.app.show_error(tr("Терминал не открылся: {error}", error=error)),
        )

    @Slot()
    def newClaudeCode(self) -> None:
        """Claude Code in a terminal of the open project, with the hub's tools (stage 4, D8)."""
        if not self._project:
            return
        pid, cols, rows = self._project, self._cols, self._rows

        def done(info: dict[str, Any]) -> None:
            if pid != self._project:
                return
            self._infos[info["id"]] = info
            self._emus.setdefault(info["id"], self._new_emu())
            self.tabsChanged.emit()
            self.setPanelOpen(True)
            self.openTerminal(info["id"])

        self.app._call(
            lambda: self.app.client.claude_code_terminal(pid, cols=cols, rows=rows),
            done,
            lambda error: self.app.show_error(tr("Claude Code не запустился: {error}", error=error)),
        )

    def _new_emu(self) -> _Emu:
        screen = TermScreen(self._cols, self._rows)
        return _Emu(screen=screen, stream=pyte.Stream(screen))

    @Slot(str)
    def openTerminal(self, terminal_id: str) -> None:
        if terminal_id not in self._infos:
            return
        self._current = terminal_id
        emu = self._emus.setdefault(terminal_id, self._new_emu())
        self.tabsChanged.emit()
        self.currentChanged.emit()
        if not emu.loaded and not emu.loading:
            self._read(terminal_id, emu)
        self._fit(emu)
        if emu.loaded and self._visible():
            self._bring_up(terminal_id, emu)
        else:
            self._show(emu)
        self._resize_timer.start()

    def _read(self, terminal_id: str, emu: _Emu) -> None:
        """The terminal's output from the hub: its last output the first time, what came after `end` when the window
        missed some. No hub: not loading, the next showing asks again."""
        since = emu.end if emu.loaded else None

        def failed(_error: Exception) -> None:
            if self._emus.get(terminal_id) is emu:
                emu.loading = False
                if not emu.loaded:  # shown empty; its events play as they come
                    self._tail_arrived(terminal_id, emu, {"data": "", "end": None})

        emu.loading = self.app._call(
            lambda: self.app.client.terminal_output(terminal_id, since),
            lambda tail: self._tail_arrived(terminal_id, emu, tail),
            failed,
        )

    def _tail_arrived(self, terminal_id: str, emu: _Emu, tail: dict[str, Any]) -> None:
        if self._emus.get(terminal_id) is not emu:
            return
        if emu.loaded and tail.get("start") != emu.end:
            # the hub no longer has all the window missed: the terminal is played again from what it has
            emu.screen = TermScreen(self._cols, self._rows)
            emu.stream = pyte.Stream(emu.screen)
        emu.stream.feed(tail.get("data") or "")
        emu.end = tail.get("end")
        emu.loaded, emu.loading, emu.missed = True, False, False
        waiting, emu.waiting, emu.waiting_chars = emu.waiting, [], 0
        for offset, data in waiting:
            self._play(emu, offset, data)
        if emu.missed:  # events lost while it was read: the rest, now
            self._read(terminal_id, emu)
        if terminal_id == self._current:
            self._show(emu)

    @Slot(str)
    def closeTab(self, terminal_id: str) -> None:
        """A running terminal is stopped (after a question); an ended one is removed with its log."""
        info = self._infos.get(terminal_id)
        if info is None:
            return
        if info["status"] == "running":

            def decide(yes: bool) -> None:
                if yes:
                    self.app._call(lambda: self.app.client.stop_terminal(terminal_id))

            self.app._ask(
                tr("Остановить терминал «{title}»?", title=_title(info)),
                tr("Всё, что в нём запущено, завершится. Вывод останется в журнале."),
                tr("Остановить"),
                decide,
            )
        else:
            self.app._call(lambda: self.app.client.remove_terminal(terminal_id), lambda _r: self._forget(terminal_id))

    def _forget(self, terminal_id: str) -> None:
        self._infos.pop(terminal_id, None)
        self._emus.pop(terminal_id, None)
        if self._current == terminal_id:
            self._current = ""
            tabs = self.tabs
            if tabs:
                self.openTerminal(tabs[-1]["id"])
            else:
                self.lines.reset([])
                self.currentChanged.emit()
                self.cursorChanged.emit()
        self.tabsChanged.emit()

    # ================= output =================

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "terminal.output":
            terminal_id = payload.get("terminal_id", "")
            emu = self._emus.get(terminal_id)
            if emu is None or not (emu.loaded or emu.loading):
                return  # never opened: its tail, read when it opens, has this
            offset, data = payload["offset"], payload["data"]
            if emu.loading or not self._on_screen(terminal_id):
                self._wait(emu, offset, data)
            elif self._play(emu, offset, data):
                self._update(emu)
            elif emu.missed:
                self._read(terminal_id, emu)
                self._wait(emu, offset, data)
        elif kind == "terminal.started":
            info = payload.get("terminal") or {}
            if info.get("project_id") == self._project and info.get("id") not in self._infos:
                self._infos[info["id"]] = info
                self._emus.setdefault(info["id"], self._new_emu())
                self.tabsChanged.emit()
        elif kind == "terminal.exited":
            info = self._infos.get(payload.get("terminal_id", ""))
            if info is not None:
                info.update(status="exited", exit_code=payload.get("exit_code"))
                self.tabsChanged.emit()
                if info["id"] == self._current:
                    self.currentChanged.emit()
                    self.cursorChanged.emit()
        elif kind == "terminal.removed":
            if payload.get("terminal_id") in self._infos:
                self._forget(payload["terminal_id"])

    def _wait(self, emu: _Emu, offset: int, data: str) -> None:
        """Output kept raw until the terminal shows (or its read ends). Too much of it is let go: the hub has it, and
        what comes after a gap too - the read when it shows brings all of it."""
        if emu.missed and not emu.loading:
            return
        emu.waiting.append((offset, data))
        emu.waiting_chars += len(data)
        if emu.waiting_chars > WAITING_MAX and not emu.loading:
            emu.waiting, emu.waiting_chars = [], 0
            emu.missed = emu.end is not None

    def _play(self, emu: _Emu, offset: int, data: str) -> bool:
        """Plays new output into the emulator, skipping what it already has. False when nothing was new - or when
        output before it never reached the window (then `missed`: it is read from the hub)."""
        if emu.end is not None:
            if offset > emu.end:
                emu.missed = True
                return False
            if offset + len(data) <= emu.end:
                return False
            data = data[emu.end - offset :]
            emu.end += len(data)
        emu.stream.feed(data)
        return True

    def _bring_up(self, terminal_id: str, emu: _Emu) -> None:
        """The terminal shows: what came while it did not is played now - after what the window missed, read first."""
        if not emu.missed and emu.waiting:
            waiting, emu.waiting, emu.waiting_chars = emu.waiting, [], 0
            for n, (offset, data) in enumerate(waiting):
                self._play(emu, offset, data)
                if emu.missed:
                    emu.waiting = waiting[n:]
                    emu.waiting_chars = sum(len(d) for _o, d in emu.waiting)
                    break
        if emu.missed and not emu.loading:
            self._read(terminal_id, emu)
        self._show(emu)

    def _show(self, emu: _Emu) -> None:
        """The whole terminal into the line model (on opening it, or after a resize)."""
        screen = emu.screen
        screen.take_news()
        base = screen.dropped
        rows = [{"key": f"h{base + i}", "html": line} for i, line in enumerate(screen.scrolled)]
        rows += [{"key": f"s{y}", "html": screen.html(y)} for y in range(screen.lines)]
        self.lines.sync(rows)
        self._shown, self._shown_base = (len(screen.scrolled), screen.lines), base
        self.cursorChanged.emit()

    def _update(self, emu: _Emu) -> None:
        """Only what changed: scrollback lines let go at the top are removed, new ones go in before the screen,
        changed screen lines are redrawn. A full scrollback (3000 lines) lets lines go on every output: laying it
        all out again then rebuilt every row each time."""
        screen = emu.screen
        news, dirty = screen.take_news()
        history, base = len(screen.scrolled), screen.dropped
        shown_history, shown_lines = self._shown
        gone = base - self._shown_base
        kept = shown_history - gone
        if (
            shown_lines != screen.lines
            or not 0 <= gone <= shown_history
            or history < kept
            or len(self.lines.rows) != (shown_history + shown_lines)
        ):
            self._show(emu)  # resized, or not what was shown: lay it out again (rows that stay are kept)
            return
        if gone:
            self.lines.remove(0, gone)
        if history > kept:
            added = [{"key": f"h{base + i}", "html": screen.scrolled[i]} for i in range(kept, history)]
            self.lines.insert(kept, added)
        self._shown, self._shown_base = (history, screen.lines), base
        for y in sorted(set(range(screen.lines)) if news else dirty):  # scrolled: every screen line moved up
            if y < screen.lines:
                html = screen.html(y)
                if self.lines.rows[history + y]["html"] != html:
                    self.lines.update(history + y, html=html)
        self.cursorChanged.emit()

    def _restyle(self) -> None:
        for emu in self._emus.values():
            emu.screen.restyle()
            emu.screen.dirty.update(range(emu.screen.lines))
        emu = self._emus.get(self._current)
        if emu is not None:
            self._show(emu)

    # ================= input =================

    @Slot(int, int, str, result=bool)
    def key(self, key: int, modifiers: int, text: str) -> bool:
        """A key pressed in the terminal; True when it was sent (the view then keeps it)."""
        emu = self._emus.get(self._current)
        if emu is None or not self.currentRunning:
            return False
        if (modifiers & CTRL and key == K.Key_V.value) or (modifiers & SHIFT and key == K.Key_Insert.value):
            self.paste()
            return True
        data = key_sequence(key, modifiers, text, app_cursor=emu.screen.app_cursor_keys)
        if data:
            self._send(data)
        return bool(data)

    @Slot()
    def paste(self) -> None:
        emu = self._emus.get(self._current)
        text = self.app._clipboard().text()
        if emu is None or not text or not self.currentRunning:
            return
        text = text.replace("\r\n", "\r").replace("\n", "\r")
        self._send(f"\x1b[200~{text}\x1b[201~" if emu.screen.bracketed_paste else text)

    @Slot(str)
    def sendText(self, text: str) -> None:
        if self.currentRunning:
            self._send(text)

    def _send(self, data: str) -> None:
        tid = self._current
        self._sender.run(
            lambda: self.app.client.terminal_input(tid, data),
            on_error=lambda error: self.app.show_error(tr("Ввод не дошёл до терминала: {error}", error=error)),
        )

    @Slot()
    def copyAll(self) -> None:
        emu = self._emus.get(self._current)
        if emu is not None:
            self.app.copy(emu.screen.plain_text())

    def shutdown(self) -> None:
        self._sender.wait(2000)


def _title(info: dict[str, Any]) -> str:
    title = info.get("title") or tr("терминал")
    return {"pwsh": "PowerShell 7", "powershell": "PowerShell", "cmd": "cmd"}.get(title, title)


def _note(info: dict[str, Any]) -> str:
    if info["status"] == "running":
        return tr("работает")
    if info["status"] == "lost":
        return tr("прерван (хаб останавливался)")
    code = info.get("exit_code")
    return tr("завершён") if code is None else tr("завершён, код {code}", code=code)
