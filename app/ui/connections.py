"""Connections in the window (D13): the hub's API keys, tokens and addresses, behind the user's password.

The sheet shows what is connected - never a key; while unlocked, a key's last four characters to recognise it.
Changes need the password: the hub gives a ticket that lives here, in memory only, and lapses after ten minutes
without a change; closing the sheet locks at once. A wrong password, or a lapsed ticket, brings back the password
field with why. The first time, the sheet asks the user to make up the password.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from hub.connections import MIN_PASSWORD

if TYPE_CHECKING:
    from .controller import Controller

TELEGRAM = "telegram_bot_token"
SHOWN_CHOICES = 80  # models listed at once in the chooser; the search narrows the rest


def connection_rows(data: dict[str, Any], notes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What the sheet lists: each connection with its state in words and the last check's answer."""
    out = []
    for c in data.get("connections", []):
        if c["kind"] == "address":
            status = c.get("value") or "не задан"
        elif c["set"]:
            status = "задан" + (f" · …{c['tail']}" if c.get("tail") else "")
            if c.get("choose"):
                status += f" · моделей в списке: {c.get('chosen', 0)}"
        else:
            status = "не задан"
        note = notes.get(c["name"], {})
        out.append({
            "name": c["name"],
            "title": c["title"],
            "purpose": c["purpose"],
            "site": c["site"],
            "hint": c["hint"],
            "secret": c["kind"] != "address",
            "set": bool(c["set"]),
            "status": status,
            "note": note.get("text", ""),
            "noteOk": bool(note.get("ok")),
            "telegram": c["name"] == TELEGRAM,
            "choose": bool(c.get("choose")),
        })  # fmt: skip
    return out


def choice_rows(models: list[dict[str, Any]], picked: set[str], query: str) -> tuple[list[dict[str, Any]], int]:
    """The chooser's list: models matching every word typed (in the id or the name), the picked ones first, at most
    SHOWN_CHOICES; and how many matched in all."""
    words = query.casefold().split()
    found = [m for m in models if all(w in f"{m['id']} {m.get('name', '')}".casefold() for w in words)]
    found.sort(key=lambda m: (m["id"] not in picked, m.get("name", m["id"]).casefold()))
    rows = []
    for m in found[:SHOWN_CHOICES]:
        note = f"${m.get('input', 0):g} / ${m.get('output', 0):g} за 1M"
        if m.get("context"):
            note += f" · контекст {int(m['context']) // 1000}K"
        rows.append({"id": m["id"], "name": m.get("name") or m["id"], "note": f"{m['id']} · {note}",
                     "picked": m["id"] in picked})  # fmt: skip
    return rows, len(found)


class Connections(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._data: dict[str, Any] = {}
        self._ticket = ""
        self._error = ""
        self._notes: dict[str, dict[str, Any]] = {}
        self._link = ""  # the one-time code for "/link" to the bot
        self._busy = False
        self._choosing = ""  # the connection whose models are being chosen (OpenRouter)
        self._choices: list[dict[str, Any]] = []
        self._picked: set[str] = set()
        self._query = ""

    # --- what QML reads ---

    @Property(str, notify=changed)
    def state(self) -> str:
        """loading, new (no password yet), locked, open."""
        if not self._data:
            return "loading"
        if not self._data.get("password"):
            return "new"
        return "open" if self._ticket and self._data.get("unlocked") else "locked"

    @Property(list, notify=changed)
    def items(self) -> list:
        return connection_rows(self._data, self._notes)

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def linkCode(self) -> str:
        return self._link

    @Property(bool, notify=changed)
    def telegramLinked(self) -> bool:
        return bool((self._data.get("telegram") or {}).get("linked"))

    @Property(str, notify=changed)
    def choosing(self) -> str:
        return self._choosing

    @Property(list, notify=changed)
    def choices(self) -> list:
        return choice_rows(self._choices, self._picked, self._query)[0]

    @Property(str, notify=changed)
    def choicesNote(self) -> str:
        if not self._choices:
            return "Загружаю список моделей…" if self._busy else ""
        shown, total = choice_rows(self._choices, self._picked, self._query)
        head = f"Выбрано: {len(self._picked)} · найдено: {total}"
        return head + (f" (показаны первые {len(shown)} — уточните поиск)" if total > len(shown) else "")

    @Property(int, notify=changed)
    def pickedCount(self) -> int:
        return len(self._picked)

    # --- choosing a provider's models (OpenRouter) ---

    @Slot(str)
    def openModels(self, name: str) -> None:
        self._choosing, self._choices, self._query = name, [], ""

        def shown(found: dict[str, Any]) -> None:
            self._choices = found.get("models", [])
            self._picked = set(found.get("chosen", []))

        self._run(lambda: self.app.client.provider_models(name), shown)

    @Slot()
    def closeModels(self) -> None:
        self._choosing, self._choices, self._picked, self._query = "", [], set(), ""
        self.changed.emit()

    @Slot(str)
    def filterModels(self, text: str) -> None:
        self._query = text
        self.changed.emit()

    @Slot(str)
    def toggleModel(self, model_id: str) -> None:
        self._picked ^= {model_id}
        self.changed.emit()

    @Slot()
    def saveModels(self) -> None:
        name, ticket, ids = self._choosing, self._ticket, sorted(self._picked)

        def saved(result: dict[str, Any]) -> None:
            self._notes[name] = {"ok": True, "text": f"В списке моделей: {len(result.get('chosen', []))}"}
            self.closeModels()
            self.load()
            self.app.load_models()

        self._run(lambda: self.app.client.choose_models(name, ids, ticket), saved)

    # --- talking to the hub ---

    def _run(self, fn, done=None) -> None:
        self._busy = True
        self._error = ""
        self.changed.emit()

        def ok(result: Any) -> None:
            self._busy = False
            if done is not None:
                done(result)
            self.changed.emit()

        self.app.call_or_fail(fn, ok, self._failed)

    def _failed(self, error: Exception) -> None:
        self._busy = False
        if getattr(error, "code", None) == "locked":  # ten minutes without a change, or the hub restarted
            self._ticket, self._link = "", ""
            self._error = "Подключения закрылись: введите пароль снова"
        else:
            self._error = str(error)
        self.changed.emit()
        self.load()

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return
        ticket = self._ticket

        def show(data: dict[str, Any]) -> None:
            if ticket and ticket == self._ticket and not data.get("unlocked"):
                self._ticket, self._link = "", ""
                self._error = "Подключения закрылись (10 минут без изменений): введите пароль снова"
            self._data = data
            self.app.show_connections(data.get("connections", []))
            self.changed.emit()

        self.app._call(lambda: self.app.client.connections(ticket), show, lambda _e: None)

    @Slot(str, str)
    def createPassword(self, password: str, repeat: str) -> None:
        if len(password) < MIN_PASSWORD:
            self._say(f"Пароль — не короче {MIN_PASSWORD} символов")
        elif password != repeat:
            self._say("Пароли не совпадают")
        else:
            self._run(lambda: self.app.client.set_connections_password(password), lambda _r: self.unlock(password))

    @Slot(str)
    def unlock(self, password: str) -> None:
        if not password:
            return

        def opened(ticket: str) -> None:
            self._ticket = ticket
            self.load()

        self._run(lambda: self.app.client.unlock_connections(password), opened)

    @Slot()
    def lock(self) -> None:
        ticket, self._ticket, self._link, self._notes = self._ticket, "", "", {}
        self._error = ""
        if ticket and self.app.client is not None:
            self.app._call(lambda: self.app.client.lock_connections(ticket), None, lambda _e: None)
        self.changed.emit()

    @Slot(str, str)
    def save(self, name: str, value: str) -> None:
        if not value.strip():
            return
        ticket = self._ticket

        def saved(result: dict[str, Any]) -> None:
            errors = (result.get("catalog") or {}).get("errors")
            text = f"Сохранено, но список моделей не получен: {errors}" if errors else "Сохранено"
            self._notes[name] = {"ok": not errors, "text": text}
            self.app.flash(f"«{self._title(name)}» сохранено в хабе")
            self.load()
            self.app.load_models()

        self._run(lambda: self.app.client.set_connection(name, value.strip(), ticket), saved)

    @Slot(str)
    def remove(self, name: str) -> None:
        ticket = self._ticket

        def decide(yes: bool) -> None:
            if yes:
                self._notes.pop(name, None)
                self._run(lambda: self.app.client.delete_connection(name, ticket), lambda _r: self.load())

        title = self._title(name)
        self.app._ask(f"Удалить подключение «{title}»?", "Ключ удалится из диспетчера учётных данных Windows; "
                      "то, что от него зависит, перестанет работать.", "Удалить", decide)  # fmt: skip

    @Slot(str)
    def check(self, name: str) -> None:
        ticket = self._ticket
        self._notes[name] = {"ok": True, "text": "Проверяю…"}

        def checked(result: dict[str, Any]) -> None:
            self._notes[name] = {"ok": bool(result.get("ok")), "text": str(result.get("detail", ""))}

        self._run(lambda: self.app.client.check_connection(name, ticket), checked)

    @Slot(str, str, str)
    def changePassword(self, old: str, new: str, repeat: str) -> None:
        if len(new) < MIN_PASSWORD:
            self._say(f"Новый пароль — не короче {MIN_PASSWORD} символов")
            return
        if new != repeat:
            self._say("Новые пароли не совпадают")
            return

        def changed(_result: Any) -> None:
            self._ticket = ""
            self.app.flash("Пароль подключений изменён")
            self.unlock(new)

        self._run(lambda: self.app.client.set_connections_password(new, old), changed)

    @Slot()
    def linkTelegram(self) -> None:
        ticket = self._ticket

        def shown(result: dict[str, Any]) -> None:
            self._link = str(result.get("code", ""))

        self._run(lambda: self.app.client.telegram_link(ticket), shown)

    @Slot()
    def unlinkTelegram(self) -> None:
        ticket = self._ticket
        self._link = ""
        self._run(lambda: self.app.client.telegram_unlink(ticket), lambda _r: self.load())

    @Slot(str)
    def openSite(self, name: str) -> None:
        site = next((c["site"] for c in self._data.get("connections", []) if c["name"] == name), "")
        if site.startswith("https://"):
            QDesktopServices.openUrl(QUrl(site))

    def on_event(self, kind: str, _payload: dict[str, Any]) -> None:
        if kind == "telegram.linked":
            self._link = ""
            self.app.flash("Telegram-чат привязан: подтверждения придут и на телефон")
        self.load()

    def _say(self, text: str) -> None:
        self._error = text
        self.changed.emit()

    def _title(self, name: str) -> str:
        return next((c["title"] for c in self._data.get("connections", []) if c["name"] == name), name)
