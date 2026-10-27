"""Outside MCP servers in the window (9.5, D42): a section of Connections. The list - each server's state, its tools
and their classes of action - is always seen; adding a server, its keys, turning it on or off, a tool's class and
removing it need the connections' password (a server is a program that runs as the user), with the ticket the
Connections sheet holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

if TYPE_CHECKING:
    from .controller import Controller

STATES = {"ready": "работает", "starting": "запускается…", "error": "ошибка", "off": "выключен",
          "stopped": "остановлен"}  # fmt: skip
CLASSES = [  # as the class menu lists them: id, name, what the hub does
    {"id": "read", "name": "только читает", "note": "без вопроса"},
    {"id": "write", "name": "меняет данные", "note": "спросить; ответ «для этого чата» запоминается"},
    {"id": "command", "name": "запускает команды", "note": "спросить каждый новый вызов"},
    {"id": "network", "name": "ходит в сеть", "note": "спрашивать каждый раз"},
    {"id": "send", "name": "отправляет от вашего имени", "note": "спрашивать каждый раз"},
    {"id": "destructive", "name": "удаляет или необратимо меняет", "note": "спрашивать каждый раз"},
]
CLASS_TEXT = {c["id"]: c["name"] for c in CLASSES}


def env_lines(text: str) -> str:
    """The window's one line "KEY=value; KEY2=value" as the hub's KEY=value lines."""
    return "\n".join(part.strip() for part in text.replace("\n", ";").split(";") if part.strip())


def server_rows(data: dict[str, Any], opened: set[str]) -> list[dict[str, Any]]:
    """What the section lists: each server with its state in words and, when opened, its tools."""
    rows = []
    for s in data.get("servers", []):
        tools = s.get("tools") or []
        state = s.get("state", "")
        status = STATES.get(state, state)
        if state == "ready":
            status += f" · инструментов: {len(tools)}"
        rows.append({
            "name": s["name"],
            "target": s.get("target", ""),
            "enabled": bool(s.get("enabled")),
            "state": state,
            "status": status,
            "detail": s.get("detail", ""),
            "env": ", ".join(s.get("env") or []),
            "opened": s["name"] in opened,
            "tools": [{"tool": t["tool"], "description": (t.get("description") or "").split("\n")[0][:160],
                       "cls": t["class"], "clsText": CLASS_TEXT.get(t["class"], t["class"]),
                       "changed": t["class"] != t.get("default")}
                      for t in tools],
        })  # fmt: skip
    return rows


class McpView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._data: dict[str, Any] = {}
        self._opened: set[str] = set()
        self._busy = False
        self._error = ""

    @Property(list, notify=changed)
    def servers(self) -> list:
        return server_rows(self._data, self._opened)

    @Property(list, constant=True)
    def classes(self) -> list:
        return CLASSES

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def show(data: dict[str, Any]) -> None:
            self._data = data
            self.changed.emit()

        self.app._call(lambda: self.app.client.mcp_servers(), show, lambda _e: None)

    def _run(self, fn, done=None) -> None:
        self._busy, self._error = True, ""
        self.changed.emit()

        def ok(result: Any) -> None:
            self._busy = False
            if done is not None:
                done(result)
            self.load()

        def failed(error: Exception) -> None:
            self._busy = False
            self._error = str(error)
            self.changed.emit()
            if getattr(error, "code", None) == "locked":  # the ticket lapsed: the sheet asks for the password
                self.app.connections.load()

        self.app.call_or_fail(fn, ok, failed)

    @Slot(str)
    def toggle(self, name: str) -> None:
        self._opened ^= {name}
        self.changed.emit()

    @Slot(str, str, str)
    def add(self, name: str, target: str, env: str) -> None:
        name, target, ticket, env = name.strip(), target.strip(), self.app.connections.ticket, env_lines(env)

        def added(server: dict[str, Any]) -> None:
            self._opened.add(server["name"])
            if server.get("state") == "error":
                self.app.flash(f"Сервер «{server['name']}» добавлен, но не запустился", error=True)
            else:
                self.app.flash(f"Сервер «{server['name']}» добавлен: инструментов — {len(server.get('tools', []))}")

        if name and target:
            self._run(lambda: self.app.client.add_mcp_server(name, target, env, ticket), added)

    @Slot(str, bool)
    def setEnabled(self, name: str, enabled: bool) -> None:
        ticket = self.app.connections.ticket
        self._run(lambda: self.app.client.set_mcp_server(name, ticket, enabled=enabled))

    @Slot(str, str)
    def setEnv(self, name: str, env: str) -> None:
        ticket, env = self.app.connections.ticket, env_lines(env)
        self._run(lambda: self.app.client.set_mcp_server(name, ticket, env=env),
                  lambda _r: self.app.flash(f"Ключи сервера «{name}» сохранены, сервер перезапущен"))  # fmt: skip

    @Slot(str)
    def restart(self, name: str) -> None:
        self._run(lambda: self.app.client.restart_mcp_server(name))

    @Slot(str, str, str)
    def setClass(self, name: str, tool: str, cls: str) -> None:
        if cls in CLASS_TEXT:
            ticket = self.app.connections.ticket
            self._run(lambda: self.app.client.set_mcp_tool_class(name, tool, cls, ticket))

    @Slot(str)
    def remove(self, name: str) -> None:
        ticket = self.app.connections.ticket

        def decide(yes: bool) -> None:
            if yes:
                self._opened.discard(name)
                self._run(lambda: self.app.client.delete_mcp_server(name, ticket))

        self.app._ask(f"Удалить сервер MCP «{name}»?", "Его инструменты пропадут из новых чатов и задач агента; "
                      "его ключи удалятся.", "Удалить", decide)  # fmt: skip

    def on_event(self, _kind: str, _payload: dict[str, Any]) -> None:
        self.load()
