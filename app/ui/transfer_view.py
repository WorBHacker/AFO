"""«Перенос настроек» in the window (D46, step 13.10): every setting into one JSON file, and a file back after a look
at what it would do - part by part, each line marked «добавится», «изменится», «без изменений» or «пропущено», with
a warning where a part gives more rights. The hub's parts are the hub's (hub/portable.py); the window adds its own:
the theme and what a new chat or task starts with (app.ini). The parts behind the connections' password ask for it
here; the ticket lives while the sheet is open, as in Connections.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from .. import presenter
from .autonomy import AUTONOMY, LEVEL_KEY

if TYPE_CHECKING:
    from .controller import Controller

KIND = "localchat-settings"
WINDOW = "window"
THEMES = {"system": "как в системе", "light": "светлая", "dark": "тёмная"}
PRIORITIES = {"balanced": "баланс", "quality": "качество", "speed": "скорость", "price": "цена"}
WINDOW_KEYS = {"ui/theme": "тема", "chat/priority": "подбор модели: что важнее", "chat/model": "модель нового чата",
               "chat/effort": "глубина размышлений", LEVEL_KEY: "режим новой задачи агента"}  # fmt: skip
ACTIONS = {"add": ("добавится", "ok"), "change": ("изменится", "accent"), "same": ("без изменений", "faint"),
           "skip": ("пропущено", "warn")}  # fmt: skip


def window_values(models: dict[str, Any]) -> dict[str, dict[str, str]]:
    """What each of the window's keys may be, as a person reads it."""
    return {"ui/theme": THEMES, "chat/priority": PRIORITIES, "chat/effort": dict(presenter.efforts()),
            "chat/model": {mid: m.get("name") or mid for mid, m in models.items()},
            LEVEL_KEY: {a: name for a, name, _hint in AUTONOMY}}  # fmt: skip


def window_part(saved: dict[str, Any], now: dict[str, str], models: dict[str, Any]) -> dict[str, Any]:
    """The window's own part of a file, as the hub shows its parts."""
    known = window_values(models)
    items = []
    for key, what in WINDOW_KEYS.items():
        if key not in saved:
            continue
        value = str(saved[key])
        if value not in known[key]:
            reason = "такой модели нет в этом хабе" if key == "chat/model" else f"неизвестное значение «{value}»"
            items.append({"what": what, "action": "skip", "detail": reason, "warn": ""})
            continue
        action = "same" if now.get(key) == value else "change"
        items.append({"what": what, "action": action, "detail": known[key][value], "warn": ""})
    return {"id": WINDOW, "title": "Окно: тема и что выбрано для новых чатов", "locked": False, "items": items}


def part_rows(parts: list[dict[str, Any]], chosen: set[str]) -> list[dict[str, Any]]:
    rows = []
    for part in parts:
        on = part["id"] in chosen
        items = [{**i, "actionText": ACTIONS.get(i["action"], (i["action"], ""))[0],
                  "tone": ACTIONS.get(i["action"], ("", "faint"))[1]} for i in part["items"]] if on else []  # fmt: skip
        counts = [f"{word}: {n}" for action, (word, _t) in ACTIONS.items()
                  if (n := sum(1 for i in part["items"] if i["action"] == action))]  # fmt: skip
        rows.append({"id": part["id"], "title": part["title"], "chosen": on, "locked": bool(part["locked"]) and on,
                     "items": items, "counts": ", ".join(counts) if on else "не загружается"})  # fmt: skip
    return rows


def summary(parts: list[dict[str, Any]]) -> str:
    counts = {a: sum(1 for p in parts for i in p["items"] if i["action"] == a) for a in ACTIONS}
    said = [f"добавлено {counts['add']}", f"изменено {counts['change']}"]
    if counts["skip"]:
        said.append(f"пропущено {counts['skip']}")
    locked = [p["title"] for p in parts if p["locked"]]
    text = "Настройки загружены: " + ", ".join(said)
    return text + (f". Не загружено без пароля подключений: {'; '.join(locked)}" if locked else "")


class TransferView(QObject):
    changed = Signal()
    finished = Signal()  # loaded: the sheet closes

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._data: dict[str, Any] = {}
        self._file = ""
        self._parts: dict[str, dict[str, Any]] = {}  # the last preview of each part, in the file's order
        self._chosen: set[str] = set()
        self._ticket = ""
        self._busy = False
        self._error = ""

    # ---------------------------------------------------------------- out

    def _window_now(self) -> dict[str, str]:
        found = {key: self.app.settings.value(key) for key in WINDOW_KEYS}
        return {key: str(value) for key, value in found.items() if value not in (None, "")}

    @Slot(str)
    def exportTo(self, url: str) -> None:
        path = Path(QUrl(url).toLocalFile() or url)
        window = self._window_now()

        def write(data: dict[str, Any]) -> None:
            data = {**data, WINDOW: window}
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self.app.show_error(f"Файл не записан: {exc}")
                return
            cut = data.get("redacted") or 0
            self.app.flash(f"Настройки записаны в {path.name}" + (
                f"; похожее на ключи вырезано: {cut}" if cut else ""), ms=10000)  # fmt: skip

        self.app.call_or_fail(lambda: self.app.client.export_settings(), write,
                              lambda e: self.app.show_error(f"Не выгружено: {e}"))  # fmt: skip

    # ---------------------------------------------------------------- back

    @Slot(str, result=bool)
    def openFile(self, url: str) -> bool:
        """A file read and its preview asked for; False (and said why) when it is not one of ours."""
        path = Path(QUrl(url).toLocalFile() or url)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.app.show_error(f"Файл не прочитан: {exc}")
            return False
        if not isinstance(data, dict) or data.get("kind") != KIND:
            self.app.show_error("Это не файл настроек AFO (настройки моделей загружаются в «Настройке моделей»)")
            return False
        self.close()
        self._data, self._file, self._parts = data, path.name, {}
        self._chosen = {k for k, v in data.items() if isinstance(v, dict | list)}
        self._preview()
        return True

    def _hub_parts(self) -> list[str]:
        return sorted(p for p in self._chosen if p != WINDOW)

    def _preview(self) -> None:
        self._busy, self._error = True, ""
        self.changed.emit()
        data, chosen, ticket = self._data, None if not self._parts else self._hub_parts(), self._ticket

        def done(result: dict[str, Any]) -> None:
            self._busy = False
            for part in result.get("parts") or []:
                self._parts[part["id"]] = part
            if isinstance(data.get(WINDOW), dict):
                self._parts[WINDOW] = window_part(data[WINDOW], self._window_now(), self.app.models)
            self._chosen &= set(self._parts)
            self.changed.emit()

        def failed(error: Exception) -> None:
            self._busy, self._error = False, f"Хаб не показал, что загрузится: {error}"
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.preview_settings(data, chosen, ticket), done, failed)

    @Property(list, notify=changed)
    def parts(self) -> list:
        return part_rows(list(self._parts.values()), self._chosen)

    @Property(str, notify=changed)
    def fileName(self) -> str:
        return self._file

    @Property(str, notify=changed)
    def exportedAt(self) -> str:
        return str(self._data.get("exported_at") or "")[:16].replace("T", " ")

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(bool, notify=changed)
    def needsPassword(self) -> bool:
        return any(p["locked"] and p["id"] in self._chosen for p in self._parts.values())

    @Slot(str)
    def toggle(self, part_id: str) -> None:
        if self._busy or part_id not in self._parts:
            return
        self._chosen ^= {part_id}
        self._preview()  # what one part does may hang on another: an agent a schedule names

    @Slot(str)
    def unlock(self, password: str) -> None:
        if self._busy or not password:
            return
        self._busy, self._error = True, ""
        self.changed.emit()

        def done(ticket: str) -> None:
            self._ticket, self._busy = ticket, False
            self._preview()

        def failed(error: Exception) -> None:
            self._busy, self._error = False, str(error)
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.unlock_connections(password), done, failed)

    @Slot()
    def apply(self) -> None:
        if self._busy or not self._data or not self._chosen:
            return
        self._busy, self._error = True, ""
        self.changed.emit()
        data, hub_parts, ticket = self._data, self._hub_parts(), self._ticket
        window = self._parts.get(WINDOW) if WINDOW in self._chosen else None

        def done(result: dict[str, Any]) -> None:
            parts = list(result.get("parts") or [])
            if window is not None:
                self._apply_window(data[WINDOW], window)
                parts.append(window)
            self.app.flash(summary(parts), ms=15000)
            self.app.reload_all()
            self.close()
            self.finished.emit()

        def failed(error: Exception) -> None:
            self._busy, self._error = False, f"Не загружено: {error}"
            self.changed.emit()

        if hub_parts:
            self.app.call_or_fail(lambda: self.app.client.import_settings(data, hub_parts, ticket), done, failed)
        else:
            done({})

    def _apply_window(self, saved: dict[str, Any], part: dict[str, Any]) -> None:
        loaded = {i["what"] for i in part["items"] if i["action"] == "change"}
        for key, what in WINDOW_KEYS.items():
            if what not in loaded:
                continue
            value = str(saved[key])
            if key == "ui/theme":
                self.app.setTheme(value)
            elif key == "chat/priority":
                self.app.setPriority(value)
            elif key == "chat/effort":
                self.app.chooseEffort(value)
            elif key == "chat/model":
                self.app.chooseModel(value)
            else:
                self.app.settings.setValue(key, value)

    @Slot()
    def close(self) -> None:
        """The sheet closed: the password's ticket ends at once, the file is forgotten."""
        if self._ticket and self.app.client is not None:
            ticket = self._ticket
            self.app._call(lambda: self.app.client.lock_connections(ticket), None, lambda _e: None)
        self._data, self._file, self._parts, self._chosen = {}, "", {}, set()
        self._ticket, self._busy, self._error = "", False, ""
        self.changed.emit()
