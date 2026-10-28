"""The Agent mode of the window (stage 9.2, D40): the autonomous agent's tasks beside the chats and the projects.

The sidebar lists the tasks - running, waiting for the user, finished. The main area works as a chat (2026-10-28,
the user's word): the input at the bottom takes a new task's goal - its mode, model, projects, limits and «when» are
chips under it, each choice explained in its menu, as in Claude's own apps - or, while a task runs, a word to the
agent: the answer to its question, or another limit it reads before its next step; the mode chip changes the running
task's mode too. Above: the open task - its goal and state, the plan as it goes (each step's state, its result and
how it was checked), the agent's question, the user's own plan, and the report at the end. Its approvals come as the
card over the view, as in a chat (D38); the whole course of work opens as a chat.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from .. import presenter
from .project_rows import local_time
from .rows import model_name

if TYPE_CHECKING:
    from .controller import Controller

# the modes, as the person reads them: who decides what (named so «Сам» can no longer read as "I do it myself")
AUTONOMY = [("free", "Авто", "Работает сам: план не утверждается, в разрешённых программах действует без вопросов. "
                             "Спрашивает только о рискованном — отправка сообщений, запись в проекты, команды, сеть."),
            ("plan", "Сначала план", "Покажет план — вы его утверждаете. Дальше работает сам; первое действие в "
                                     "каждой программе спросит один раз."),
            ("each_step", "Каждый шаг", "Утверждаете план и каждый шаг перед его началом. Медленнее всего, но всё "
                                        "под вашим контролем.")]  # fmt: skip
LIMITS = [("done_when", "Готово, когда", "По этому агент проверит себя в конце, например «все тесты проходят». "
                                        "Можно оставить пустым — тогда он сверится с самой целью."),
          ("max_steps", "Шагов, не больше", "Сколько действий агент может сделать, прежде чем остановится."),
          ("max_minutes", "Минут, не больше", "Сколько времени задача может идти."),
          ("budget_usd", "Бюджет, $", "Сколько можно потратить на платные модели; локальные — бесплатно."),
          ("max_replans", "Новых планов", "Сколько раз агент может переделать план, если пошло не так.")]  # fmt: skip
STATUSES = {"queued": "в очереди", "running": "идёт", "waiting": "ждёт вас", "done": "готово", "error": "ошибка",
            "cancelled": "остановлено", "paused": "пауза"}  # fmt: skip
STEP_STATES = {"pending": ("ждёт", "dots"), "running": ("идёт", "refresh"), "done": ("готово", "check"),
               "failed": ("не вышло", "close"), "skipped": ("пропущен", "back")}  # fmt: skip
TEMPLATES = [
    ("research", "Исследование в сети", "Найди в сети, что известно о …: сравни несколько источников и напиши отчёт",
     "отчёт со ссылками на источники, разногласия источников отмечены"),
    ("tests", "Починить тесты", "Запусти тесты проекта, найди причины падений и исправь их",
     "все тесты проходят; что исправлено — перечислено"),
    ("data", "Данные в таблицу", "Собери … из файлов папки … в CSV-таблицу",
     "таблица создана, несколько строк сверены с исходными файлами"),
    ("review", "Проверка изменений", "Прочитай изменения в проекте (git diff) и напиши, что стоит поправить",
     "список замечаний с файлами и строками"),
    ("docs", "Порядок в документации", "Сверь README проекта с кодом и поправь устаревшее",
     "README соответствует коду; изменения перечислены"),
]  # fmt: skip
LEVEL_KEY = "agent/autonomy"  # app.ini: the level the person chose last
BLANK: dict[str, Any] = {"goal": "", "done_when": "", "model_id": "", "planner_model_id": "", "project_ids": [],
                         "autonomy": "plan", "max_steps": "100", "max_minutes": "60", "budget_usd": "1",
                         "max_replans": "3", "network": False}  # fmt: skip


def utc_now() -> str:
    """As the hub writes its times (questions, answers): the thread sorts the user's words among them."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def task_rows(runs: list[dict[str, Any]], selected: str, paused: set[str]) -> list[dict[str, Any]]:
    """The sidebar's tasks: running and waiting first, then the finished ones."""
    out = []
    for run in runs:
        if run.get("kind") != "autonomous":
            continue
        status = "paused" if run["id"] in paused and run["status"] == "running" else run.get("shown_status") or \
            run["status"]  # fmt: skip
        live = run["status"] in ("queued", "running")
        out.append({"id": run["id"], "title": run.get("goal") or run.get("task", ""), "status": status,
                    "statusLabel": STATUSES.get(status, status), "live": live, "waiting": status == "waiting",
                    "section": "Идут" if live else "Готовы", "selected": run["id"] == selected,
                    "goalMet": bool((run.get("report") or {}).get("goal_met"))})  # fmt: skip
    out.sort(key=lambda r: (not r["live"],))  # stable: newest first within each, as the hub lists them
    return out


def step_rows(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for step in (plan or {}).get("steps") or []:
        label, icon = STEP_STATES.get(step["status"], (step["status"], "dots"))
        tries = f" · попыток {step['attempts']}" if step.get("attempts") else ""
        rows.append({"n": step["n"], "title": step["title"], "check": step.get("check") or "", "status": step["status"],
                     "label": label + tries, "icon": icon, "note": step.get("note") or ""})  # fmt: skip
    return rows


def plan_text(plan: dict[str, Any] | None) -> str:
    """The current plan as the editor shows it: a step per line, «title — check»."""
    return "\n".join(f"{s['title']} — {s['check']}" if s.get("check") else s["title"]
                     for s in (plan or {}).get("steps") or [])  # fmt: skip


def steps_from_text(text: str) -> list[dict[str, str]]:
    steps = []
    for line in text.splitlines():
        line = line.strip().lstrip("0123456789.) ").strip()
        if not line:
            continue
        title, _, check = line.partition(" — ")
        steps.append({"title": title.strip(), "check": check.strip()})
    return steps


def report_rows(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {}
    return {"summary": report.get("summary", ""), "goalMet": bool(report.get("goal_met")),
            "verdict": "Цель достигнута" if report.get("goal_met") else "Цель не достигнута",
            "done": report.get("done") or [], "notDone": report.get("not_done") or [],
            "files": report.get("files") or []}  # fmt: skip


def goal_body(draft: dict[str, Any]) -> dict[str, Any]:
    """The form as the hub's request; a field that is not a number says so (ValueError)."""
    goal = draft["goal"].strip()
    if not goal:
        raise ValueError("Напишите цель: что агент должен сделать")
    if not draft["model_id"]:
        raise ValueError("Выберите модель, которая будет работать")

    def whole(key: str, label: str) -> int:
        try:
            return int(str(draft[key]).strip())
        except ValueError:
            raise ValueError(f"{label}: нужно целое число") from None

    budget_text = str(draft["budget_usd"]).strip().replace(",", ".")
    try:
        budget = float(budget_text) if budget_text else None
    except ValueError:
        raise ValueError("Бюджет: нужно число долларов") from None
    return {"goal": goal, "done_when": draft["done_when"].strip(), "model_id": draft["model_id"],
            "planner_model_id": draft["planner_model_id"] or None, "project_ids": list(draft["project_ids"]),
            "autonomy": draft["autonomy"], "max_steps": whole("max_steps", "Шагов"),
            "max_minutes": whole("max_minutes", "Минут"), "max_replans": whole("max_replans", "Новых планов"),
            "budget_usd": budget, "network": bool(draft.get("network"))}  # fmt: skip


class AutonomyView(QObject):
    tasksChanged = Signal()
    changed = Signal()  # the open task (or the form)
    selectionChanged = Signal()
    asked = Signal(str, str)  # an agent's question: a notice outside the window when it is not in front
    goalBack = Signal(str)  # the task did not start: its goal back into the input

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._runs: list[dict[str, Any]] = []
        self._selected = ""
        self._detail: dict[str, Any] = {}
        self._paused: set[str] = set()
        self._said: dict[str, list[tuple[str, str]]] = {}  # run id -> (when, text) the user wrote to it on the way
        self._draft: dict[str, Any] = {**BLANK, "autonomy": self._level()}
        self._error = ""
        self._access: dict[str, Any] = {"folders": [], "domains": [], "default_domains": []}

    # ================= for QML =================

    @Property(list, notify=tasksChanged)
    def tasks(self) -> list:
        return task_rows(self._runs, self._selected, self._paused)

    @Property(str, notify=selectionChanged)
    def selectedId(self) -> str:
        return self._selected

    def selected(self) -> str:
        return self._selected

    def is_task(self, run_id: str) -> bool:
        return any(r["id"] == run_id and r.get("kind") == "autonomous" for r in self._runs)

    @Property("QVariantMap", notify=changed)
    def task(self) -> dict:
        """The open task: its goal, state, plan, question and report."""
        if not self._selected:
            return {}
        run = next((r for r in self._runs if r["id"] == self._selected), {})
        detail = self._detail
        plans = detail.get("plans") or []
        plan = plans[-1] if plans else None
        question = next((q for q in detail.get("questions") or [] if q["status"] == "pending"), None)
        status = "paused" if detail.get("paused") and run.get("status") == "running" else run.get("shown_status") \
            or run.get("status", "")  # fmt: skip
        model = model_name(self.app.models, run.get("model_id")) if run else ""
        steps = step_rows(plan)
        done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
        return {
            "id": self._selected, "goal": detail.get("goal") or run.get("goal") or "",
            "doneWhen": detail.get("done_when") or "", "status": status, "statusLabel": STATUSES.get(status, status),
            "live": run.get("status") in ("queued", "running"), "paused": bool(detail.get("paused")),
            "autonomy": dict((a, n) for a, n, _h in AUTONOMY).get(detail.get("autonomy") or "", ""),  # the mode
            "level": detail.get("autonomy") or "",
            "model": model, "detail": run.get("detail") or "",
            "meta": " · ".join(p for p in (model, f"шагов {run.get('steps', 0)}",
                                           presenter.money(run["cost_usd"]) if run.get("cost_usd") else "",
                                           local_time(run.get("created_at", ""))) if p),
            "steps": steps, "progress": f"{done} из {len(steps)}" if steps else "",
            "planVersion": plan["version"] if plan else 0, "planReason": (plan or {}).get("reason") or "",
            "planApproved": (plan or {}).get("approved") or "", "planText": plan_text(plan),
            "question": question or {}, "report": report_rows(detail.get("report")),
        }  # fmt: skip

    @Property("QVariantMap", notify=changed)
    def draft(self) -> dict:
        models = self.app.models
        d = self._draft
        names = {p["id"]: p["name"] for p in self.app.projects.items}
        return {**d, "modelName": model_name(models, d["model_id"]) if d["model_id"] else "выберите модель",
                "plannerName": model_name(models, d["planner_model_id"]) if d["planner_model_id"]
                else "та же модель", "projectNames": ", ".join(names.get(p, p) for p in d["project_ids"])}  # fmt: skip

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property(list, constant=True)
    def autonomyOptions(self) -> list:
        return [{"id": a, "name": n, "hint": h} for a, n, h in AUTONOMY]

    # ---------- the input at the bottom, as in a chat ----------

    def _running(self) -> bool:
        """A task is open and still going: the input writes to it."""
        run = next((r for r in self._runs if r["id"] == self._selected), {}) if self._selected else {}
        return run.get("status") in ("queued", "running")

    def _mode(self) -> str:
        return (self._detail.get("autonomy") or "") if self._running() else self._draft["autonomy"]

    @Property(bool, notify=changed)
    def running(self) -> bool:
        return self._running()

    @Property(list, notify=changed)
    def modes(self) -> list:
        """The mode chip's menu: each mode with what it means."""
        current = self._mode()
        return [{"section": "Режим — кто что решает", "id": ""}] + [
            {"id": a, "name": n, "note": h, "icon": "shield", "selected": a == current} for a, n, h in AUTONOMY
        ]

    @Property(str, notify=changed)
    def modeName(self) -> str:
        return dict((a, n) for a, n, _h in AUTONOMY).get(self._mode(), "")

    @Property(str, notify=changed)
    def modeHint(self) -> str:
        return dict((a, h) for a, _n, h in AUTONOMY).get(self._mode(), "")

    @Property(list, notify=changed)
    def projectItems(self) -> list:
        """The projects chip's menu: a click adds or removes one."""
        chosen = set(self._draft["project_ids"])
        rows = [{"id": p["id"], "name": p["name"], "icon": "folder", "selected": p["id"] in chosen}
                for p in self.app.projects.items]  # fmt: skip
        return [{"section": "Проекты — агент видит и меняет только их", "id": ""}] + (
            rows
            or [
                {"id": "", "name": "Проектов нет", "note": "агент будет работать без файлов проектов", "enabled": False}
            ]
        )

    @Property(list, notify=changed)
    def thread(self) -> list:
        """The open task's talk, as in a chat: the agent's questions, the user's answers and words on the way."""
        if not self._selected:
            return []
        rows = []
        for q in self._detail.get("questions") or []:
            if q["status"] == "pending":  # the one it waits on: the card under the thread
                continue
            rows.append({"who": "agent", "text": q["question"], "at": q.get("created_at") or "",
                         "expired": q["status"] == "expired"})  # fmt: skip
            if q.get("answer"):
                rows.append({"who": "you", "text": q["answer"], "at": q.get("answered_at") or "", "expired": False})
        rows += [
            {"who": "you", "text": text, "at": at, "expired": False} for at, text in self._said.get(self._selected, [])
        ]
        return sorted(rows, key=lambda r: r["at"])

    @Property(list, constant=True)
    def limitFields(self) -> list:
        return [{"id": k, "name": n, "hint": h} for k, n, h in LIMITS]

    @Property(str, notify=changed)
    def limitsText(self) -> str:
        d = self._draft
        parts = [f"{d['max_steps']} шагов", f"{d['max_minutes']} мин"]
        if str(d.get("budget_usd") or "").strip():
            parts.append(f"${d['budget_usd']}")
        return " · ".join(parts)

    @Property(str, notify=changed)
    def projectsText(self) -> str:
        names = {p["id"]: p["name"] for p in self.app.projects.items}
        chosen = [names.get(p, p) for p in self._draft["project_ids"]]
        return ", ".join(chosen) if chosen else "Без проектов"

    @Property(str, notify=changed)
    def placeholder(self) -> str:
        if not self._running():
            return "Что сделать агенту? Опишите, как человеку.  Enter — начать, Shift+Enter — новая строка"
        if any(q["status"] == "pending" for q in self._detail.get("questions") or []):
            return "Ответ на вопрос агента…  Enter — отправить"
        return "Написать агенту по ходу работы: уточнение, ограничение — он учтёт со следующего шага"

    @Slot(str)
    def chooseMode(self, autonomy: str) -> None:
        """The mode chip: a running task's mode changes on the way; otherwise the next task's."""
        if self._running():
            self.setLevel(autonomy)
        else:
            self.setField("autonomy", autonomy)

    @Slot(str, result=bool)
    def submit(self, text: str) -> bool:
        """The input sent: a word to the running task, or a new task with this goal (now or as scheduled)."""
        text = text.strip()
        if not text:
            return False
        if self._running():
            self.message(text)
            return True
        if self._selected:  # a finished task is open: the input starts a new one
            self.newTask()
        self._draft = {**self._draft, "goal": text}
        self._error = ""
        schedules = self.app.schedules_view
        if schedules.draft.get("when", "now") == "now":
            self.start()
            return not self._error  # a goal the form refused stays in the input
        schedules.create()
        return not schedules.error

    @Slot(str)
    def message(self, text: str) -> None:
        run_id = self._selected

        def done(result: dict[str, Any]) -> None:
            if result.get("answered"):
                self.app.flash("Ответ передан агенту")
            else:  # the hub keeps it for the agent only: the thread here remembers it
                self._said.setdefault(run_id, []).append((utc_now(), text))
                self.app.flash("Агент прочтёт это перед следующим шагом")
            self._load_detail()
            self.changed.emit()

        self.app.call_or_fail(lambda: self.app.client.message_run(run_id, text), done,
                              lambda e: self.app.show_error(f"Не передано: {e}"))  # fmt: skip

    @Property(list, constant=True)
    def templates(self) -> list:
        return [{"id": t[0], "name": t[1]} for t in TEMPLATES]

    @Property(list, notify=changed)
    def modelChoices(self) -> list:
        """The models an autonomous task may have (Claude Code is an agent of its own)."""
        return [m for m in self.app.pickerItems if not str(m.get("id", "")).startswith("claude-code/")]

    @Property(list, notify=changed)
    def projectChoices(self) -> list:
        chosen = set(self._draft["project_ids"])
        return [{"id": p["id"], "name": p["name"], "selected": p["id"] in chosen} for p in self.app.projects.items]

    # ================= loading =================

    @Slot()
    def load(self) -> None:
        def show(runs: list[dict[str, Any]]) -> None:
            self._runs = runs
            self.tasksChanged.emit()
            self.changed.emit()

        self.app._call(lambda: self.app.client.runs(), show, lambda _e: None)
        self._load_detail()

    def _load_detail(self) -> None:
        run_id = self._selected
        if not run_id:
            return

        def show(detail: dict[str, Any]) -> None:
            if run_id == self._selected:
                self._detail = detail
                (self._paused.add if detail.get("paused") else self._paused.discard)(run_id)
                self.changed.emit()
                self.tasksChanged.emit()

        self.app._call(lambda: self.app.client.run_plan(run_id), show, lambda _e: None)

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        run_id = payload.get("run_id") or (payload.get("run") or {}).get("id") or ""
        if kind == "run.question":
            self.app.flash("Агент спрашивает — ответ в режиме «Агент»", ms=10000)
            self.asked.emit("Агент спрашивает", str(payload.get("question", ""))[:200])
        if kind == "run.notice":  # the agent tells the user something (9.4)
            self.app.flash(f"Агент: {payload.get('text', '')}", ms=12000)
            self.asked.emit("Агент сообщает", str(payload.get("text", ""))[:200])
        if kind in ("run.paused", "run.resumed"):
            (self._paused.add if kind == "run.paused" else self._paused.discard)(run_id)
        if kind.startswith("run.") and run_id and run_id == self._selected and kind != "run.status":
            self._load_detail()
        if kind in ("run.started", "run.finished", "run.question", "run.answered", "run.report", "run.paused",
                    "run.resumed") or kind.startswith("approval."):  # fmt: skip
            self.load()
        elif kind == "run.plan" and run_id == self._selected:
            self._detail = {**self._detail, "plans": [*(self._detail.get("plans") or [])[:-1], payload["plan"]]
                            if payload.get("plan") else self._detail.get("plans") or []}  # fmt: skip
            self.changed.emit()

    # ================= the form =================

    @Slot()
    def newTask(self) -> None:
        self._selected, self._detail, self._error = "", {}, ""
        model = self.app.role_model("executor") or self.app.modelId
        self._draft = {**BLANK, "model_id": model, "planner_model_id": self.app.role_model("planner"),
                       "autonomy": self._level()}  # fmt: skip
        self.selectionChanged.emit()
        self.changed.emit()
        self.tasksChanged.emit()

    @Slot(str)
    def select(self, run_id: str) -> None:
        self._selected, self._detail, self._error = run_id, {}, ""
        self.selectionChanged.emit()
        self.changed.emit()
        self.tasksChanged.emit()
        self._load_detail()

    def _level(self) -> str:
        """The mode a new task starts with: the one chosen last time, «Авто» at first."""
        chosen = str(self.app.settings.value(LEVEL_KEY, "free"))
        return chosen if chosen in dict((a, n) for a, n, _h in AUTONOMY) else "free"

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if name in BLANK and name != "project_ids":
            self._draft = {**self._draft, name: value}
            self._error = ""
            if name == "autonomy":
                self.app.settings.setValue(LEVEL_KEY, value)
            self.changed.emit()

    @Slot(str)
    def setLevel(self, autonomy: str) -> None:
        """The open task's mode, changed on the way (to «Авто»: it stops asking before acting in a program)."""
        run_id = self._selected
        if not run_id or self.app.client is None:
            return

        def done(result: dict[str, Any]) -> None:
            name = dict((a, n) for a, n, _h in AUTONOMY).get(autonomy, autonomy)
            self.app.flash(f"Режим: «{name}»" + (" — ждавший вопрос разрешён" if result.get("answered") else ""))
            self._load_detail()

        self.app.call_or_fail(lambda: self.app.client.set_autonomy(run_id, autonomy), done,
                              lambda e: self.app.show_error(f"Не изменено: {e}"))  # fmt: skip

    @Slot(str, bool)
    def setFlag(self, name: str, value: bool) -> None:
        if name == "network":
            self._draft = {**self._draft, "network": value}
            self.changed.emit()

    # ---------- what it may reach beyond projects (9.4) ----------

    @Property(list, notify=changed)
    def accessFolders(self) -> list:
        return list(self._access.get("folders") or [])

    @Property(str, notify=changed)
    def domainsText(self) -> str:
        return "\n".join(self._access.get("domains") or [])

    @Slot()
    def loadAccess(self) -> None:
        self.app._call(lambda: self.app.client.agent_access(), self._show_access, lambda _e: None)

    def _show_access(self, access: dict[str, Any]) -> None:
        self._access = access
        self.changed.emit()

    def _save_access(self, **changes: Any) -> None:
        self.app.call_or_fail(lambda: self.app.client.set_agent_access(**changes), self._show_access,
                              lambda e: self.app.show_error(f"Не сохранено: {e}"))  # fmt: skip

    @Slot(str)
    def addFolder(self, url: str) -> None:
        path = QUrl(url).toLocalFile() or url
        folders = [f for f in self.accessFolders if f["path"] != path]
        self._save_access(folders=[*folders, {"path": path, "write": False}])

    @Slot(str)
    def removeFolder(self, path: str) -> None:
        self._save_access(folders=[f for f in self.accessFolders if f["path"] != path])

    @Slot(str, bool)
    def setFolderWrite(self, path: str, write: bool) -> None:
        self._save_access(folders=[{**f, "write": write} if f["path"] == path else f for f in self.accessFolders])

    @Slot(str)
    def setDomains(self, text: str) -> None:
        hosts = [h for h in text.replace(",", " ").split() if h]
        self._save_access(domains=hosts)
        self.app.flash("Список сайтов сохранён")

    @Slot()
    def resetDomains(self) -> None:
        self._save_access(domains=list(self._access.get("default_domains") or []))

    @Slot(str)
    def toggleProject(self, project_id: str) -> None:
        ids = list(self._draft["project_ids"])
        self._draft = {**self._draft, "project_ids": [p for p in ids if p != project_id] if project_id in ids
                       else [*ids, project_id]}  # fmt: skip
        self.changed.emit()

    @Slot(str)
    def applyTemplate(self, template: str) -> None:
        found = next((t for t in TEMPLATES if t[0] == template), None)
        if found:
            self._draft = {**self._draft, "goal": found[2], "done_when": found[3]}
            self.changed.emit()

    @Slot()
    def start(self) -> None:
        try:
            body = goal_body(self._draft)
        except ValueError as exc:
            self._error = str(exc)
            self.changed.emit()
            return

        def done(run: dict[str, Any]) -> None:
            self.app.flash("Задача отдана агенту")
            self._runs = [run, *self._runs]
            self.select(run["id"])
            self.load()
            self.app.load_conversations()

        def failed(error: Exception) -> None:
            self._error = f"Не началось: {error}"
            self.changed.emit()
            self.goalBack.emit(body["goal"])

        self.app.call_or_fail(lambda: self.app.client.start_autonomous(body), done, failed)

    # ================= the open task =================

    @Slot()
    def pause(self) -> None:
        self._act(lambda rid: self.app.client.pause_run(rid), "Пауза: агент остановится перед следующим шагом")

    @Slot()
    def resume(self) -> None:
        self._act(lambda rid: self.app.client.resume_run(rid), "Агент продолжает")

    @Slot()
    def stop(self) -> None:
        self._act(lambda rid: self.app.client.cancel_run(rid), "Агент остановлен")

    def _act(self, call, said: str) -> None:
        run_id = self._selected
        if not run_id:
            return
        self.app.call_or_fail(lambda: call(run_id), lambda _r: (self.app.flash(said), self.load()),
                              lambda e: self.app.show_error(f"Не вышло: {e}"))  # fmt: skip

    @Slot(str, str)
    def answer(self, question_id: str, text: str) -> None:
        run_id = self._selected
        if not text.strip() or not run_id:
            return
        self.app.call_or_fail(lambda: self.app.client.answer_question(run_id, question_id, text.strip()),
                              lambda _r: self.load(),
                              lambda e: self.app.show_error(f"Ответ не принят: {e}"))  # fmt: skip

    @Slot(str, str)
    def editPlan(self, text: str, reason: str) -> None:
        run_id = self._selected
        steps = steps_from_text(text)
        if not steps or not run_id:
            self.app.show_error("В плане нет ни одного шага")
            return
        self.app.call_or_fail(lambda: self.app.client.edit_run_plan(run_id, steps, reason.strip()),
                              lambda _r: (self.app.flash("План изменён — агент узнает перед следующим шагом"),
                                          self._load_detail()),
                              lambda e: self.app.show_error(f"План не изменён: {e}"))  # fmt: skip

    @Slot()
    def openLog(self) -> None:
        """The whole course of work - every model's turn and tool call - as a chat."""
        if self._selected:
            run_id = self._selected
            self.app.setMode("chats")
            self.app.openChat(run_id)
