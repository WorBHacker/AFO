"""A coordinator's board in the window (16.4, D50): when the open chat is a coordinator's run, a bar over the
composer says how its tasks stand, and the sheet shows each - the agent and its model, the status (one that waits
for the user's answer says so), how long it took, what it cost, its check (rounds, the judge's verdict), and the run
to open. The board comes from /v1/runs/{id}/tasks and is read again on each «run.tasks».
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, tr

from .. import presenter

if TYPE_CHECKING:
    from .controller import Controller

STATUS = {"pending": N_("ждёт очереди"), "running": N_("идёт"), "waiting": N_("ждёт вас"),
          "checking": N_("проверяется"), "done": N_("готово"), "escalated": N_("нужен человек"),
          "failed": N_("не удалась"), "cancelled": N_("отменена"), "skipped": N_("пропущена")}  # fmt: skip
TONE = {"pending": "faint", "running": "accent", "waiting": "warn", "checking": "accent", "done": "ok",
        "escalated": "warn", "failed": "error", "cancelled": "faint", "skipped": "faint"}  # fmt: skip
VERDICTS = {"ACHIEVED": N_("принято"), "INCOMPLETE": N_("не дотянула"), "REGRESSED": N_("сломала то, что работало")}


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def shown_status(task: dict[str, Any]) -> str:
    """A running task that waits for the user's answer is shown as such."""
    return "waiting" if task.get("status") == "running" and task.get("waiting") else task.get("status", "")


def check_text(task: dict[str, Any]) -> str:
    """How its check went: the rounds and the judge's last verdict; '' for a task without criteria."""
    if not task.get("criteria"):
        return ""
    rounds = task.get("rounds") or 0
    parts = [tr("круг {n} из 3", n=rounds)] if rounds else [tr("проверка после работы")]
    if task.get("verdict"):
        parts.append(tr(VERDICTS.get(task["verdict"], task["verdict"])))
    return " · ".join(parts)


def board_rows(tasks: list[dict[str, Any]], models: dict[str, str], now: datetime | None = None) -> list[dict]:
    """One row per task, as the sheet shows it."""
    now = now or datetime.now(UTC)
    rows = []
    for t in tasks:
        status = shown_status(t)
        started, ended = _when(t.get("started_at")), _when(t.get("ended_at"))
        took = ((ended or now) - started).total_seconds() if started else None
        rows.append({
            "id": t["task_id"],
            "agent": t.get("agent", ""),
            "model": models.get(t.get("model_id") or "", t.get("model_id") or ""),
            "status": status,
            "statusText": tr(STATUS.get(status, status)),
            "tone": TONE.get(status, "faint"),
            "time": presenter.seconds(max(took, 0.0)) if took is not None else "",
            "cost": presenter.money(t["cost_usd"]) if t.get("cost_usd") else "",
            "check": check_text(t),
            "after": tr("после {tasks}", tasks=", ".join(t["dependencies"])) if t.get("dependencies") else "",
            "detail": t.get("detail") or "",
            "runId": t.get("run_id") or "",
        })  # fmt: skip
    return rows


def board_summary(tasks: list[dict[str, Any]]) -> str:
    """«Подзадачи: готово 2 · идёт 1 · ждёт вас 1» - in the order a status needs the user first."""
    counts: dict[str, int] = {}
    for t in tasks:
        counts[shown_status(t)] = counts.get(shown_status(t), 0) + 1
    order = ("waiting", "escalated", "failed", "running", "checking", "pending", "done", "skipped", "cancelled")
    told = [f"{tr(STATUS[s])} {counts[s]}" for s in order if counts.get(s)]
    return tr("Подзадачи: {states}", states=" · ".join(told))


class TasksView(QObject):
    changed = Signal()

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._conversation = ""
        self._tasks: list[dict[str, Any]] = []

    @Property(bool, notify=changed)
    def shown(self) -> bool:
        return bool(self._tasks)

    @Property(str, notify=changed)
    def summary(self) -> str:
        return board_summary(self._tasks) if self._tasks else ""

    @Property(str, notify=changed)
    def tone(self) -> str:
        states = {shown_status(t) for t in self._tasks}
        return ("warn" if states & {"waiting", "escalated"} else "error" if "failed" in states
                else "accent" if states & {"running", "checking", "pending"} else "ok")  # fmt: skip

    @Property(list, notify=changed)
    def rows(self) -> list:
        models = {mid: m.get("name") or mid for mid, m in self.app.models.items()}
        return board_rows(self._tasks, models)

    def run_ids(self) -> dict[str, str]:
        """The open coordinator's tasks' runs: run id -> «[a] «Писатель»» (their approvals show under it)."""
        return {t["run_id"]: f"[{t['task_id']}] «{t['agent']}»" for t in self._tasks if t.get("run_id")}

    @Slot()
    def load(self) -> None:
        cid = self.app.current
        if self.app.client is None or not cid:
            self._set(cid or "", [])
            return
        self.app._call(lambda: self.app.client.run_tasks(cid), lambda data: self._set(cid, data.get("tasks", [])),
                       lambda _e: self._set(cid, []))  # fmt: skip

    def _set(self, cid: str, tasks: list[dict[str, Any]]) -> None:
        if cid != (self.app.current or ""):
            return
        if (cid, tasks) != (self._conversation, self._tasks):
            self._conversation, self._tasks = cid, tasks
            self.changed.emit()
            self.app.approvals.tasks_changed()  # the card may now show a task's request here

    @Slot(str)
    def openRun(self, run_id: str) -> None:
        if run_id:
            self.app.openChat(run_id)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        current = self.app.current or ""
        if not current:
            return
        mine = (kind == "run.tasks" and payload.get("run_id") == current) or (
            kind == "run.finished" and (payload.get("run") or {}).get("parent_run") == current
        )
        waits = (
            kind.startswith("approval.") and (payload.get("approval") or {}).get("conversation_id") in self.run_ids()
        )
        if mine or waits:  # its board changed; a task waits for the user's answer, or not any more
            self.load()
