"""«Перенос настроек» in the window (D46, step 13.10): every setting into one JSON file, and a file back after a look
at what it would do - part by part, each line marked «добавится», «изменится», «без изменений» or «пропущено», with
a warning where a part gives more rights. The hub's parts are the hub's (hub/portable.py); the window adds its own:
the theme, what a new chat or task starts with and, since 25.4, the layout - the panels' sizes, the terminals'
panel open, the window maximized; not where the window stands (another computer has other screens) (app.ini). The
parts behind the connections' password ask for it here; the ticket lives while the sheet is open, as in Connections.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from common.i18n import N_, tr

from .. import presenter
from .autonomy import AUTONOMY, LEVEL_KEY

if TYPE_CHECKING:
    from .controller import Controller

KIND = "localchat-settings"
WINDOW = "window"
THEMES = {"system": N_("как в системе"), "light": N_("светлая"), "dark": N_("тёмная")}
PRIORITIES = {"balanced": N_("баланс"), "quality": N_("качество"), "speed": N_("скорость"), "price": N_("цена")}
WINDOW_KEYS = {"ui/theme": N_("тема"), "chat/priority": N_("подбор модели: что важнее"),
               "chat/model": N_("модель нового чата"), "chat/effort": N_("глубина размышлений"),
               LEVEL_KEY: N_("режим новой задачи агента")}  # fmt: skip
LAYOUT_KEYS = {"ui/sidebarWidth": N_("ширина боковой панели"), "projects/chatsHeight": N_("высота «Чаты проекта»"),
               "terminals/height": N_("высота панели терминалов"), "terminals/open": N_("панель терминалов открыта"),
               "quick/maximized": N_("окно во весь экран")}  # fmt: skip
FLAGS = ("terminals/open", "quick/maximized")
ACTIONS = {"add": (N_("добавится"), "ok"), "change": (N_("изменится"), "accent"),
           "same": (N_("без изменений"), "faint"), "skip": (N_("пропущено"), "warn")}  # fmt: skip


def layout_value(key: str, value: Any) -> tuple[str, str] | None:
    """A layout key's value as app.ini keeps it, and as a person reads it; None - not one it may be (a size is
    held in its bounds where it is set)."""
    text = str(value).strip().lower()
    if key in FLAGS:
        return (text, tr("да") if text == "true" else tr("нет")) if text in ("true", "false") else None
    if not text.isdigit():
        return None
    return text, tr("по размеру чатов") if key == "projects/chatsHeight" and text == "0" else text


def window_values(models: dict[str, Any]) -> dict[str, dict[str, str]]:
    """What each of the window's keys may be, as a person reads it."""
    return {"ui/theme": {k: tr(v) for k, v in THEMES.items()},
            "chat/priority": {k: tr(v) for k, v in PRIORITIES.items()}, "chat/effort": dict(presenter.efforts()),
            "chat/model": {mid: m.get("name") or mid for mid, m in models.items()},
            LEVEL_KEY: {a: tr(name) for a, name, _hint in AUTONOMY}}  # fmt: skip


def window_part(saved: dict[str, Any], now: dict[str, str], models: dict[str, Any]) -> dict[str, Any]:
    """The window's own part of a file, as the hub shows its parts."""
    known = window_values(models)
    items = []
    for key, what in WINDOW_KEYS.items():
        if key not in saved:
            continue
        value = str(saved[key])
        if value not in known[key]:
            if key == "chat/model":
                reason = tr("такой модели нет в этом хабе")
            else:
                reason = tr("неизвестное значение «{value}»", value=value)
            items.append({"key": key, "what": tr(what), "action": "skip", "detail": reason, "warn": ""})
            continue
        action = "same" if now.get(key) == value else "change"
        items.append({"key": key, "what": tr(what), "action": action, "detail": known[key][value], "warn": ""})
    for key, what in LAYOUT_KEYS.items():
        if key not in saved:
            continue
        found = layout_value(key, saved[key])
        if found is None:
            items.append({"key": key, "what": tr(what), "action": "skip", "warn": "",
                          "detail": tr("неизвестное значение «{value}»", value=saved[key])})  # fmt: skip
            continue
        action = "same" if now.get(key) == found[0] else "change"
        items.append({"key": key, "what": tr(what), "action": action, "detail": found[1], "warn": ""})
    return {"id": WINDOW, "title": tr("Окно: тема, раскладка и что выбрано для новых чатов"), "locked": False,
            "items": items}  # fmt: skip


def part_rows(parts: list[dict[str, Any]], chosen: set[str]) -> list[dict[str, Any]]:
    rows = []
    for part in parts:
        on = part["id"] in chosen
        items = [{**i, "actionText": tr(ACTIONS[i["action"]][0]) if i["action"] in ACTIONS else i["action"],
                  "tone": ACTIONS.get(i["action"], ("", "faint"))[1]} for i in part["items"]] if on else []  # fmt: skip
        counts = [f"{tr(word)}: {n}" for action, (word, _t) in ACTIONS.items()
                  if (n := sum(1 for i in part["items"] if i["action"] == action))]  # fmt: skip
        rows.append({"id": part["id"], "title": part["title"], "chosen": on, "locked": bool(part["locked"]) and on,
                     "items": items, "counts": ", ".join(counts) if on else tr("не загружается")})  # fmt: skip
    return rows


def summary(parts: list[dict[str, Any]]) -> str:
    counts = {a: sum(1 for p in parts for i in p["items"] if i["action"] == a) for a in ACTIONS}
    said = [tr("добавлено {count}", count=counts["add"]), tr("изменено {count}", count=counts["change"])]
    if counts["skip"]:
        said.append(tr("пропущено {count}", count=counts["skip"]))
    locked = [p["title"] for p in parts if p["locked"]]
    text = tr("Настройки загружены: {done}", done=", ".join(said))
    return text + (tr(". Не загружено без пароля подключений: {parts}", parts="; ".join(locked)) if locked else "")


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
        now = {key: str(value) for key, value in found.items() if value not in (None, "")}
        terminals = self.app.terminals
        layout = {
            "ui/sidebarWidth": self.app.sidebarWidth,
            "projects/chatsHeight": self.app.projectChatsHeight,
            "terminals/height": terminals.panelHeight,
            "terminals/open": terminals.panelOpen,
            "quick/maximized": self.app.settings.value("quick/maximized", False),
        }  # as the window has it now
        for key, value in layout.items():
            if (shown := layout_value(key, value)) is not None:
                now[key] = shown[0]
        return now

    @Slot(str)
    def exportTo(self, url: str) -> None:
        path = Path(QUrl(url).toLocalFile() or url)
        window = self._window_now()

        def write(data: dict[str, Any]) -> None:
            data = {**data, WINDOW: window}
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self.app.show_error(tr("Файл не записан: {error}", error=exc))
                return
            cut = data.get("redacted") or 0
            if cut:
                said = tr("Настройки записаны в {file}; похожее на ключи вырезано: {count}", file=path.name, count=cut)
            else:
                said = tr("Настройки записаны в {file}", file=path.name)
            self.app.flash(said, ms=10000)

        self.app.call_or_fail(lambda: self.app.client.export_settings(), write,
                              lambda e: self.app.show_error(tr("Не выгружено: {error}", error=e)))  # fmt: skip

    # ---------------------------------------------------------------- back

    @Slot(str, result=bool)
    def openFile(self, url: str) -> bool:
        """A file read and its preview asked for; False (and said why) when it is not one of ours."""
        path = Path(QUrl(url).toLocalFile() or url)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.app.show_error(tr("Файл не прочитан: {error}", error=exc))
            return False
        if not isinstance(data, dict) or data.get("kind") != KIND:
            self.app.show_error(tr("Это не файл настроек AFO (настройки моделей загружаются в «Настройке моделей»)"))
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
            self._busy, self._error = False, tr("Хаб не показал, что загрузится: {error}", error=error)
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
            self._busy, self._error = False, tr("Не загружено: {error}", error=error)
            self.changed.emit()

        if hub_parts:
            self.app.call_or_fail(lambda: self.app.client.import_settings(data, hub_parts, ticket), done, failed)
        else:
            done({})

    def _apply_window(self, saved: dict[str, Any], part: dict[str, Any]) -> None:
        loaded = {i["key"] for i in part["items"] if i["action"] == "change"}
        for key in (*WINDOW_KEYS, *LAYOUT_KEYS):
            if key not in loaded:
                continue
            value = str(saved[key]) if key in WINDOW_KEYS else layout_value(key, saved[key])[0]
            if key == "ui/sidebarWidth":
                self.app.setSidebarWidth(int(value))
            elif key == "projects/chatsHeight":
                self.app.setProjectChatsHeight(int(value))
            elif key == "terminals/height":
                self.app.terminals.setPanelHeight(int(value))
            elif key == "terminals/open":
                self.app.terminals.setPanelOpen(value == "true")
            elif key == "ui/theme":
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
