"""The agent's mailbox in the window (stage 9.4b, Q21): a section of Connections - the address and the app password
(the servers of well-known providers fill in by themselves), «Проверить», «Отключить». Changes need the connections'
password, with the ticket the Connections sheet holds; the password never comes back to the window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from hub.mail import preset

if TYPE_CHECKING:
    from .controller import Controller


def servers_text(account: dict[str, Any] | None) -> str:
    if not account:
        return ""
    return (f"IMAP {account['imap_host']}:{account['imap_port']} · SMTP {account['smtp_host']}:"
            f"{account['smtp_port']}")  # fmt: skip


class MailView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._data: dict[str, Any] = {}
        self._busy = False
        self._note = {"ok": True, "text": ""}

    @Property(bool, notify=changed)
    def connected(self) -> bool:
        return bool(self._data.get("account")) and bool(self._data.get("password"))

    @Property(str, notify=changed)
    def address(self) -> str:
        return (self._data.get("account") or {}).get("address", "")

    @Property(str, notify=changed)
    def servers(self) -> str:
        return servers_text(self._data.get("account"))

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def note(self) -> str:
        return self._note["text"]

    @Property(bool, notify=changed)
    def noteOk(self) -> bool:
        return bool(self._note["ok"])

    @Slot(str, result=bool)
    def knownProvider(self, address: str) -> bool:
        """The servers fill in by themselves for this address: no server fields needed."""
        return bool(preset(address.strip()))

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def show(data: dict[str, Any]) -> None:
            self._data = data
            self.changed.emit()

        self.app._call(lambda: self.app.client.mail(), show, lambda _e: None)

    def _run(self, fn, done) -> None:
        self._busy = True
        self.changed.emit()

        def ok(result: Any) -> None:
            self._busy = False
            done(result)
            self.load()

        def failed(error: Exception) -> None:
            self._busy = False
            self._note = {"ok": False, "text": str(error)}
            self.changed.emit()
            if getattr(error, "code", None) == "locked":
                self.app.connections.load()

        self.app.call_or_fail(fn, ok, failed)

    @Slot(str, str, str, str, str, str)
    def save(self, address: str, password: str, imap_host: str, imap_port: str, smtp_host: str,
             smtp_port: str) -> None:  # fmt: skip
        body: dict[str, Any] = {"address": address.strip(), "password": password}
        for key, value in (("imap_host", imap_host), ("imap_port", imap_port), ("smtp_host", smtp_host),
                           ("smtp_port", smtp_port)):  # fmt: skip
            if value.strip():
                body[key] = int(value) if key.endswith("_port") and value.strip().isdigit() else value.strip()
        ticket = self.app.connections.ticket

        def saved(_result: Any) -> None:
            self._note = {"ok": True, "text": "Почта подключена — проверьте её кнопкой «Проверить»"}

        self._run(lambda: self.app.client.set_mail(body, ticket), saved)

    @Slot()
    def check(self) -> None:
        ticket = self.app.connections.ticket
        self._note = {"ok": True, "text": "Проверяю…"}

        def checked(result: dict[str, Any]) -> None:
            self._note = {"ok": bool(result.get("ok")), "text": str(result.get("detail", ""))}

        self._run(lambda: self.app.client.check_mail(ticket), checked)

    @Slot()
    def remove(self) -> None:
        ticket = self.app.connections.ticket

        def decide(yes: bool) -> None:
            if yes:
                self._note = {"ok": True, "text": ""}
                self._run(lambda: self.app.client.delete_mail(ticket), lambda _r: None)

        self.app._ask("Отключить почту?", "Агент перестанет читать и отправлять письма; пароль приложения удалится.",
                      "Отключить", decide)  # fmt: skip

    def on_event(self, _kind: str, _payload: dict[str, Any]) -> None:
        self.load()
