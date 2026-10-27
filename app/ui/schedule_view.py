"""Schedules in the window (stage 9.7, Q23): the new-task form's «Когда» - now, by time, by an event - and the list of
schedules in the Agent mode's sidebar; the quiet hours in Settings. A schedule is the form's goal with a trigger;
the hub starts it (`hub/schedule.py`). Notices that come in the quiet hours wait for the morning here too.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from hub.schedule import QUIET_DEFAULT, quiet_until

from .autonomy import goal_body

if TYPE_CHECKING:
    from .controller import Controller

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WHEN = [{"id": "now", "name": "Сейчас"}, {"id": "time", "name": "По времени"}, {"id": "event", "name": "По событию"}]
EVERY = [{"id": "day", "name": "Каждый день"}, {"id": "weekdays", "name": "По дням недели"},
         {"id": "hours", "name": "Каждые N часов"}]  # fmt: skip
EVENTS = [{"id": "file", "name": "Новый файл в папке"}, {"id": "telegram", "name": "Сообщение боту"},
          {"id": "mail", "name": "Новое письмо"}]  # fmt: skip
BLANK = {"when": "now", "every": "day", "at": "09:00", "days": [1, 2, 3, 4, 5], "hours": "6", "event": "file",
         "folder": "", "pattern": "*", "contains": "", "words": "", "computer": False}  # fmt: skip


def trigger_text(trigger: dict[str, Any]) -> str:
    match trigger.get("kind"):
        case "time" if trigger.get("every") == "hours":
            return f"каждые {trigger['hours']} ч"
        case "time" if trigger.get("every") == "weekdays":
            days = ", ".join(DAYS[d - 1] for d in trigger.get("days") or [])
            return f"по {days} в {trigger['at']}"
        case "time":
            return f"каждый день в {trigger['at']}"
        case "file":
            return f"новый файл {trigger.get('pattern') or '*'} в {trigger.get('folder', '')}"
        case "telegram":
            words = trigger.get("contains") or ""
            return f"сообщение боту со словами «{words}»" if words else "любое сообщение боту"
        case "mail":
            words = trigger.get("words") or ""
            return f"новое письмо со словами «{words}»" if words else "любое новое письмо"
    return str(trigger.get("kind") or "")


def when_text(item: dict[str, Any], now: datetime) -> str:
    """The next start in words: «сегодня в 09:00», «завтра в 07:30», «ждёт конца тихих часов (08:00)»."""
    if not item.get("enabled"):
        return "выключено"
    if item.get("waiting_until"):
        return f"ждёт конца тихих часов ({item['waiting_until'][11:16]})"
    if item.get("pending"):
        return "событие ждёт запуска"
    if not item.get("next_at"):
        return "ждёт события"
    at = datetime.fromisoformat(item["next_at"])
    if at.date() == now.date():
        return f"сегодня в {at:%H:%M}"
    if at.date() == (now + timedelta(days=1)).date():
        return f"завтра в {at:%H:%M}"
    return f"{DAYS[at.isoweekday() - 1]} {at:%d.%m} в {at:%H:%M}"


def schedule_rows(items: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return [{"id": s["id"], "title": s["title"], "trigger": trigger_text(s["trigger"]), "when": when_text(s, now),
             "enabled": bool(s["enabled"]), "computer": bool(s.get("computer")), "error": s.get("last_error") or "",
             "lastRun": s.get("last_run_id") or ""} for s in items]  # fmt: skip


def trigger_body(draft: dict[str, Any]) -> dict[str, Any]:
    """The «Когда» part as the hub's trigger; ValueError with what is wrong."""
    if draft["when"] == "time":
        if draft["every"] == "hours":
            try:
                hours = int(str(draft["hours"]).strip())
            except ValueError:
                raise ValueError("Каждые N часов: нужно целое число") from None
            return {"kind": "time", "every": "hours", "hours": hours}
        body = {"kind": "time", "every": draft["every"], "at": str(draft["at"]).strip()}
        if draft["every"] == "weekdays":
            if not draft["days"]:
                raise ValueError("Отметьте хотя бы один день недели")
            body["days"] = sorted(draft["days"])
        return body
    if draft["event"] == "file":
        if not str(draft["folder"]).strip():
            raise ValueError("Выберите папку, в которой ждать новые файлы")
        return {"kind": "file", "folder": str(draft["folder"]).strip(), "pattern": str(draft["pattern"]).strip()}
    if draft["event"] == "mail":
        return {"kind": "mail", "words": str(draft["words"]).strip()}
    return {"kind": "telegram", "contains": str(draft["contains"]).strip()}


class SchedulesView(QObject):
    changed = Signal()  # the list, the quiet hours
    draftChanged = Signal()  # the form's «Когда»

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._items: list[dict[str, Any]] = []
        self._quiet: dict[str, Any] = dict(QUIET_DEFAULT)
        self._draft: dict[str, Any] = {**BLANK, "days": list(BLANK["days"])}
        self._error = ""

    # ---------- for QML ----------

    @Property(list, notify=changed)
    def items(self) -> list:
        return schedule_rows(self._items, datetime.now())

    @Property("QVariantMap", notify=draftChanged)
    def draft(self) -> dict:
        return {**self._draft, "dayFlags": [d in self._draft["days"] for d in range(1, 8)]}

    @Property(str, notify=draftChanged)
    def error(self) -> str:
        return self._error

    @Property(list, constant=True)
    def whenOptions(self) -> list:
        return WHEN

    @Property(list, constant=True)
    def everyOptions(self) -> list:
        return EVERY

    @Property(list, constant=True)
    def eventOptions(self) -> list:
        return EVENTS

    @Property(list, constant=True)
    def dayNames(self) -> list:
        return DAYS

    @Property("QVariantMap", notify=changed)
    def quiet(self) -> dict:
        return dict(self._quiet)

    @Property(str, notify=changed)
    def quietText(self) -> str:
        if not self._quiet.get("enabled"):
            return "Тихих часов нет: задачи по расписанию начинаются в любое время"
        return (f"С {self._quiet['from']} до {self._quiet['to']} задачи не начинаются, а сообщения агента ждут "
                "утра")  # fmt: skip

    def quiet_now(self) -> bool:
        return quiet_until(self._quiet, datetime.now()) is not None

    # ---------- the form's «Когда» ----------

    @Slot()
    def reset(self) -> None:
        self._draft, self._error = {**BLANK, "days": list(BLANK["days"])}, ""
        self.draftChanged.emit()

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if name in BLANK and name not in ("days", "computer"):
            self._draft = {**self._draft, name: value}
            self._error = ""
            self.draftChanged.emit()

    @Slot(int)
    def toggleDay(self, day: int) -> None:
        days = set(self._draft["days"]) ^ {day}
        self._draft = {**self._draft, "days": sorted(d for d in days if 1 <= d <= 7)}
        self.draftChanged.emit()

    @Slot(bool)
    def setComputer(self, value: bool) -> None:
        self._draft = {**self._draft, "computer": value}
        self.draftChanged.emit()

    @Slot(str)
    def chooseFolder(self, url: str) -> None:
        from PySide6.QtCore import QUrl

        self.setField("folder", QUrl(url).toLocalFile() if url.startswith("file:") else url)

    @Slot()
    def create(self) -> None:
        """The form's goal with this trigger, as a schedule."""
        try:
            goal = goal_body(self.app.autonomy_view._draft)
            trigger = trigger_body(self._draft)
        except ValueError as exc:
            self._error = str(exc)
            self.draftChanged.emit()
            return
        body = {"title": goal["goal"][:80], "goal": goal, "trigger": trigger, "computer": self._draft["computer"]}

        def done(found: dict[str, Any]) -> None:
            self.app.flash(f"Запланировано: {trigger_text(found['trigger'])}")
            self.reset()
            self.load()

        def failed(error: Exception) -> None:
            self._error = f"Не запланировано: {error}"
            self.draftChanged.emit()

        self.app.call_or_fail(lambda: self.app.client.add_schedule(body), done, failed)

    # ---------- the list ----------

    @Slot()
    def load(self) -> None:
        if self.app.client is None:
            return

        def show(data: dict[str, Any]) -> None:
            self._items = data.get("schedules", [])
            self._quiet = {**QUIET_DEFAULT, **(data.get("quiet") or {})}
            self.changed.emit()

        self.app._call(lambda: self.app.client.schedules(), show, lambda _e: None)

    def _change(self, call, said: str = "") -> None:
        def ok(_result: Any) -> None:
            if said:
                self.app.flash(said)
            self.load()

        self.app.call_or_fail(call, ok, lambda e: self.app.show_error(f"Не вышло: {e}"))

    @Slot(str, bool)
    def setEnabled(self, sid: str, enabled: bool) -> None:
        self._change(lambda: self.app.client.set_schedule(sid, enabled=enabled),
                     "Расписание включено" if enabled else "Расписание выключено")  # fmt: skip

    @Slot(str)
    def runNow(self, sid: str) -> None:
        def started(result: dict[str, Any]) -> None:
            self.app.flash("Задача по расписанию запущена сейчас")
            self.load()
            self.app.autonomy_view.load()
            self.app.autonomy_view.select(result["run_id"])

        self.app.call_or_fail(lambda: self.app.client.run_schedule(sid), started,
                              lambda e: self.app.show_error(f"Не запущено: {e}"))  # fmt: skip

    @Slot(str)
    def remove(self, sid: str) -> None:
        title = next((s["title"] for s in self._items if s["id"] == sid), "")

        def decide(yes: bool) -> None:
            if yes:
                self._change(lambda: self.app.client.delete_schedule(sid), "Расписание удалено")

        self.app._ask(f"Удалить расписание «{title}»?", "Начатые им задачи останутся в списке.", "Удалить", decide)

    @Slot(bool, str, str)
    def setQuiet(self, enabled: bool, start: str, end: str) -> None:
        change = {"enabled": enabled, "from": start.strip(), "to": end.strip()}
        self._change(lambda: self.app.client.set_quiet(**change), "Тихие часы сохранены")

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "schedule.started":
            title = (payload.get("schedule") or {}).get("title", "")
            self.app.flash(f"По расписанию начата задача «{title}»")
            self.app.autonomy_view.load()
        self.load()
