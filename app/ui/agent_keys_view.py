"""The user's own API keys for the agent's http_request (stage 9.4b): a section of Connections. A key is named, tied
to its host and header; the hub adds it to the requests to that host only, and the model never sees it. Changes need
the connections' password (the ticket the Connections sheet holds); a key never comes back to the window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import tr

if TYPE_CHECKING:
    from .controller import Controller


class AgentKeys(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._keys: list[dict[str, Any]] = []
        self._error = ""

    @Property(list, notify=changed)
    def items(self) -> list:
        return [{"name": k["name"], "host": k["host"], "header": k["header"], "set": bool(k.get("set")),
                 "note": f"{k['header']}: {k['prefix']}…" if k.get("set") else tr("ключ не задан")}
                for k in self._keys]  # fmt: skip

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def show(data: dict[str, Any]) -> None:
            self._keys = data.get("keys", [])
            self.changed.emit()

        self.app._call(lambda: self.app.client.agent_keys(), show, lambda _e: None)

    def _run(self, fn, said: str) -> None:
        self._error = ""

        def ok(data: dict[str, Any]) -> None:
            self._keys = data.get("keys", [])
            self.app.flash(said)
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._error = str(error)
            self.changed.emit()
            if getattr(error, "code", None) == "locked":
                self.app.connections.load()

        self.app.call_or_fail(fn, ok, failed)

    @Slot(str, str, str, str, str)
    def save(self, name: str, host: str, header: str, prefix: str, value: str) -> None:
        name, ticket = name.strip().lower(), self.app.connections.ticket
        body = {"host": host.strip(), "header": header.strip() or "Authorization", "prefix": prefix,
                "value": value.strip()}  # fmt: skip
        if not name or not body["host"]:
            self._error = tr("Нужны имя ключа и сайт, например weather и api.weather.example")
            self.changed.emit()
            return
        self._run(
            lambda: self.app.client.set_agent_key(name, body, ticket), tr("Ключ «{name}» сохранён в хабе", name=name)
        )

    @Slot(str)
    def remove(self, name: str) -> None:
        ticket = self.app.connections.ticket

        def decide(yes: bool) -> None:
            if yes:
                self._run(lambda: self.app.client.delete_agent_key(name, ticket), tr("Ключ «{name}» удалён", name=name))

        self.app._ask(
            tr("Удалить ключ «{name}»?", name=name),
            tr("Запросы агента к его сайту пойдут без ключа."),
            tr("Удалить"),
            decide,
        )

    def on_event(self, _kind: str, _payload: dict[str, Any]) -> None:
        self.load()
