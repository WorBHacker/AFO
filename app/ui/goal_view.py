"""A chat's goal in the window (stage 11.3, D44): the chat works turn after turn until the hub's check says it is
met (hub/goals.py).

- the bar over the composer: what the goal is, how it stands (at work / paused / waiting for you / at its limit /
  met), the turn and what is used of the limits, what the last check found; the criteria with ticks and the judge's
  word unfold below it; «Пауза», «Продолжить» (a goal at its limit - with higher limits), «Снять».
- the contract card (a sheet): the wish in the user's words, «Составить договор» - a model drafts the contract (the
  plan's goal compiler) -, then the objective, the criteria (a command in a project and its exit code, or a rule in
  words), the constraints, the limits with a guess of the price, who checks; «Подтвердить и запустить» sets the goal
  and approves its commands. «/цель …» in the composer and its «Цель» chip open it.
- while the goal works the composer's Enter writes to it (steering): read at the start of the next turn.
- a goal met, waiting for the user or at its limit: a Windows notice when the window is not in front (D21).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from common.i18n import N_, decimal, plural, tr

if TYPE_CHECKING:
    from .controller import Controller

STATUS = {"active": N_("идёт"), "paused": N_("на паузе"), "blocked": N_("ждёт вас"), "budget": N_("у предела"),
          "achieved": N_("достигнута"), "cleared": N_("снята")}  # fmt: skip
OPEN = ("active", "paused", "blocked", "budget")
NOTICES = {"achieved": N_("Цель достигнута"), "blocked": N_("Цель ждёт вас"), "budget": N_("Цель дошла до предела")}
VERDICTS = {"ACHIEVED": N_("выполнено"), "INCOMPLETE": N_("не выполнено"), "REGRESSED": N_("потеряно то, что работало")}
DEFAULT_LIMITS = {"turns": "25", "tokens": "500", "minutes": "60"}  # tokens in thousands (Q26)
ROUTES = {"continue": N_("дальше"), "last_turn": N_("последний ход"), "achieved": N_("достигнута"),
          "blocked": N_("ждёт вас"), "budget": N_("предел"), "paused": N_("пауза"),
          "investigated": N_("причину искали — дальше правка")}  # fmt: skip


def thousands(n: int | float) -> str:
    n = int(n)
    if n < 1000:
        return str(n)
    if n >= 1_000_000:
        return decimal(tr("{value:.1f} млн", value=n / 1_000_000))
    return tr("{value:.0f} тыс.", value=n / 1000)


def progress_text(goal: dict[str, Any]) -> str:
    """«ход 3 из 25 · 12 тыс. из 500 тыс. токенов · 4 из 60 мин · $0,02»."""
    parts = [tr("ход {used} из {limit}", used=goal["turns_used"], limit=goal["max_turns"]),
             tr("{used} из {limit} токенов", used=thousands(goal["tokens_used"]), limit=thousands(goal["max_tokens"])),
             tr("{used:.0f} из {limit} мин", used=goal["seconds_used"] / 60, limit=goal["max_minutes"])]  # fmt: skip
    if goal.get("cost_usd"):
        parts.append(decimal(f"${goal['cost_usd']:.2f}"))
    return " · ".join(parts)


def check_text(check: dict[str, Any] | None) -> str:
    """What the last check found, in a line."""
    if not check:
        return tr("проверка — после первого хода")
    verdict = VERDICTS.get(check.get("verdict") or "", "")
    missing = len(check.get("missing") or [])
    found = tr(verdict) if verdict else tr(ROUTES[check["route"]]) if check["route"] in ROUTES else check["route"]
    said = tr("проверка после хода {turn}: {found}", turn=check["turn"], found=found)
    if missing and check.get("verdict") != "ACHIEVED":
        said += f" — {missing} {plural(missing, 'пункт', 'пункта', 'пунктов')}"
    if check["route"] == "investigate":  # 11.4: the loop guard - the next turn looks for the cause
        said += " · " + tr("следующий ход — поиск причины, без записи")
    return said


def criteria_rows(goal: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in goal.get("criteria") or []:
        if c["kind"] == "command":
            if c.get("project_name"):
                text = tr("`{command}` в «{project}» → код {code}", command=c["command"], project=c["project_name"],
                          code=c["expected_exit_code"])  # fmt: skip
            else:
                text = tr("`{command}` → код {code}", command=c["command"], code=c["expected_exit_code"])
        else:
            text = c.get("rule") or ""
        out.append({"text": text, "met": bool(c.get("verified")), "words": c["kind"] == "semantic"})
    return out


def judge_word(check: dict[str, Any] | None) -> str:
    found = next((r for r in (check or {}).get("results") or [] if r.get("kind") == "judge"), None)
    return (found or {}).get("feedback") or ""


def stage_rows(goal: dict[str, Any]) -> list[dict[str, Any]]:
    """The goal's stages (11.5): done ones ticked with the worker's line, the one at work marked."""
    stages = goal.get("milestones") or []
    current = next((m["n"] for m in stages if m["status"] == "pending"), None)
    return [{"text": f"{m['n']}. {m['title']}", "done": m["status"] == "done", "current": m["n"] == current,
             "summary": m.get("summary") or ""} for m in stages]  # fmt: skip


def pending_stages(goal: dict[str, Any]) -> str:
    """The stages not done yet, a line each - what the user edits."""
    return "\n".join(m["title"] for m in goal.get("milestones") or [] if m["status"] == "pending")


def lines_of(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def blank_editor() -> dict[str, Any]:
    return {"wish": "", "objective": "", "criteria": [], "constraints": "", "milestones": "", "judge": "", "notes": [],
            "drafted": False, **DEFAULT_LIMITS}  # fmt: skip


def editor_of_draft(editor: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """The card filled from the model's draft: the wish stays, the rest is the draft's (limits it did not say stay)."""
    draft = found.get("draft") or {}
    limits = draft.get("limits") or {}
    return {**editor, "objective": draft.get("objective") or editor["wish"],
            "criteria": [{"kind": c["kind"], "command": c.get("command", ""), "project": c.get("project", ""),
                          "code": str(c.get("expected_exit_code", 0)), "rule": c.get("rule", "")}
                         for c in draft.get("criteria") or []],
            "constraints": "\n".join(draft.get("constraints") or []), "notes": found.get("notes") or [],
            "milestones": "\n".join(draft.get("milestones") or []),
            "turns": str(limits.get("max_turns") or editor["turns"]),
            "minutes": str(limits.get("max_minutes") or editor["minutes"]), "drafted": True}  # fmt: skip


def contract_of(editor: dict[str, Any], model_id: str, effort: str) -> tuple[dict[str, Any] | None, str]:
    """The card as the goal the hub takes - or None and what is wrong, in the user's words."""
    objective = " ".join((editor.get("objective") or editor.get("wish") or "").split())
    if not objective:
        return None, tr("Напишите, чего добиться")
    criteria = []
    for n, c in enumerate(editor.get("criteria") or [], 1):
        if c["kind"] == "command":
            command = " ".join((c.get("command") or "").split())
            if not command:
                continue
            try:
                code = int((c.get("code") or "0").strip())
            except ValueError:
                return None, tr("Критерий {n}: код выхода — целое число", n=n)
            criteria.append({"kind": "command", "command": command, "project": c.get("project") or "",
                             "expected_exit_code": code})  # fmt: skip
        elif (c.get("rule") or "").strip():
            criteria.append({"kind": "semantic", "rule": " ".join(c["rule"].split())})
    if not criteria:
        return None, tr("Нужен хотя бы один критерий: команда с кодом выхода или правило словами")
    limits = {}
    for field, key, scale in (("turns", "max_turns", 1), ("tokens", "max_tokens", 1000), ("minutes", "max_minutes", 1)):
        text = str(editor.get(field) or "").strip().replace(" ", "")
        if not text:
            continue
        try:
            limits[key] = int(float(text.replace(",", ".")) * scale)
        except ValueError:
            return None, tr("Пределы — числа")
    rules = lines_of(editor.get("constraints") or "")
    contract = {"objective": objective, "criteria": criteria, "model_id": model_id, "effort": effort,
                "constraints": rules, "limits": limits}  # fmt: skip
    if stages := lines_of(editor.get("milestones") or ""):  # 11.5
        contract["milestones"] = stages
    if editor.get("judge"):
        contract["judge_model_id"] = editor["judge"]
    return contract, ""


def cost_text(model: dict[str, Any] | None, tokens_k: str) -> str:
    """A guess of the most the worker may cost within the token limit (most of it is input)."""
    if not model:
        return ""
    prices = model.get("prices") or {}
    if not prices:
        return tr("Цена: бесплатно — модель локальная")
    try:
        tokens = float(str(tokens_k).replace(",", ".")) * 1000
    except ValueError:
        return ""
    most = tokens / 1_000_000 * (0.85 * prices.get("input", 0) + 0.15 * prices.get("output", 0))
    return tr("Цена: до ≈ ${cost} за работу модели (без проверяющей)", cost=decimal(f"{most:.2f}"))


class GoalView(QObject):
    changed = Signal()  # the open chat's goal
    editorChanged = Signal()  # the contract card
    openRequested = Signal()  # the card is to open
    started = Signal()  # the goal is set: the card closes
    noticed = Signal(str, str)  # a goal met / waiting / at its limit: for the notifier

    def __init__(self, app: Controller):
        super().__init__(app)
        self.app = app
        self._conversation = ""
        self._goal: dict[str, Any] | None = None
        self._checks: list[dict[str, Any]] = []
        self._steering: list[dict[str, Any]] = []
        self._checking = ""  # "", "commands", "judge": what the hub is doing between turns
        self._expanded = False
        self._dismissed: set[str] = set()  # met goals the user put away
        self._editor = blank_editor()
        self._drafting = False
        self._starting = False
        self._error = ""

    # ---------- the bar ----------

    @Property(bool, notify=changed)
    def shown(self) -> bool:
        goal = self._goal
        return goal is not None and (goal["status"] in OPEN or (goal["status"] == "achieved"
                                                                and goal["id"] not in self._dismissed))  # fmt: skip

    @Property(str, notify=changed)
    def status(self) -> str:
        return (self._goal or {}).get("status", "")

    @Property(str, notify=changed)
    def statusText(self) -> str:
        if self._checking == "judge":
            return tr("проверяет модель")
        if self._checking:
            return tr("проверка")
        return tr(STATUS[self.status]) if self.status in STATUS else ""

    @Property(str, notify=changed)
    def objective(self) -> str:
        return (self._goal or {}).get("objective", "")

    @Property(str, notify=changed)
    def detail(self) -> str:
        """Why it waits or stopped, as the hub says."""
        return (self._goal or {}).get("detail", "") if self.status != "active" else ""

    @Property(str, notify=changed)
    def progress(self) -> str:
        return progress_text(self._goal) if self._goal else ""

    @Property(str, notify=changed)
    def lastCheck(self) -> str:
        return check_text(self._checks[-1] if self._checks else None)

    @Property(list, notify=changed)
    def criteria(self) -> list:
        return criteria_rows(self._goal) if self._goal else []

    @Property(list, notify=changed)
    def stages(self) -> list:
        return stage_rows(self._goal) if self._goal else []

    @Property(str, notify=changed)
    def pendingStages(self) -> str:
        return pending_stages(self._goal) if self._goal else ""

    @Slot(str)
    def saveStages(self, text: str) -> None:
        """The stages not done yet, as the user wrote them (11.5) - the worker hears it at its next turn."""
        titles = lines_of(text)
        self._act(lambda cid: self.app.client.set_milestones(cid, titles),
                  tr("Этапы изменены: модель узнает об этом в начале следующего хода"))  # fmt: skip

    @Property(list, notify=changed)
    def missing(self) -> list:
        last = self._checks[-1] if self._checks else {}
        return [] if last.get("verdict") == "ACHIEVED" else list(last.get("missing") or [])

    @Property(str, notify=changed)
    def judgeWord(self) -> str:
        return judge_word(self._checks[-1] if self._checks else None)

    @Property(int, notify=changed)
    def waitingWords(self) -> int:
        """The user's words the worker has not read yet."""
        return len(self._steering)

    @Property(bool, notify=changed)
    def expanded(self) -> bool:
        return self._expanded

    @Property(bool, notify=changed)
    def steering(self) -> bool:
        """The goal works: the composer's words go to it, not as a message of their own."""
        return self.status == "active" and self._conversation == (self.app.current or "")

    @Slot()
    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.changed.emit()

    @Slot()
    def dismiss(self) -> None:
        if self._goal:
            self._dismissed.add(self._goal["id"])
            self.changed.emit()

    def load(self) -> None:
        cid = self.app.current or ""
        if self.app.client is None or not cid:
            self._show(cid, {"goal": None, "checks": [], "steering": []})
            return
        self.app._call(lambda: self.app.client.goal(cid), lambda data: self._show(cid, data), lambda _e: None)

    def _show(self, cid: str, data: dict[str, Any]) -> None:
        if cid != (self.app.current or ""):
            return
        if cid != self._conversation:
            self._expanded = False
        self._conversation, self._goal = cid, data.get("goal")
        self._checks, self._steering = data.get("checks") or [], data.get("steering") or []
        self._checking = "commands" if data.get("checking") else ""
        self.changed.emit()

    def _act(self, call, said: str) -> None:
        cid = self._conversation

        def done(goal: dict[str, Any]) -> None:
            if said:
                self.app.flash(said)
            self.load()

        self.app.call_or_fail(lambda: call(cid), done, lambda e: self.app.show_error(tr("Не вышло: {error}", error=e)))

    @Slot()
    def pause(self) -> None:
        self._act(self.app.client.pause_goal, tr("Цель на паузе: идущий ход доработает, следующий не начнётся"))

    @Slot()
    def resume(self) -> None:
        goal = self._goal or {}
        limits = None
        if goal.get("status") == "budget":  # at its limit: half as much again of what ran out
            limits = {"max_turns": max(goal["max_turns"], goal["turns_used"] + max(5, goal["max_turns"] // 2)),
                      "max_tokens": max(goal["max_tokens"], int(goal["tokens_used"] * 1.5) + 1000),
                      "max_minutes": max(goal["max_minutes"], int(goal["seconds_used"] / 60) + 30)}  # fmt: skip
        said = tr("Цель снова в работе — пределы подняты") if limits else tr("Цель снова в работе")
        self._act(lambda cid: self.app.client.resume_goal(cid, limits), said)

    @Slot()
    def clear(self) -> None:
        def decide(yes: bool) -> None:
            if yes:
                self._act(self.app.client.clear_goal, tr("Цель снята"))

        self.app._ask(tr("Снять цель?"),
                      tr("Идущий ход доработает, следующий не начнётся. История чата и проверок останется."),
                      tr("Снять"), decide)  # fmt: skip

    @Slot(str)
    def steer(self, text: str) -> None:
        text = text.strip()
        if not text or not self._conversation:
            return
        cid = self._conversation
        self.app.call_or_fail(lambda: self.app.client.steer_goal(cid, text),
                              lambda _r: (self.app.flash(tr("Уточнение передано: модель прочтёт его в начале "
                                                            "следующего хода")), self.load()),
                              lambda e: (self.app.show_error(tr("Не вышло: {error}", error=e)),
                                         self.app.draftRestored.emit(text)))  # fmt: skip

    def on_event(self, kind: str, payload: dict[str, Any]) -> None:
        cid = payload.get("conversation_id") or ""
        if kind == "goal.status" and payload.get("status") in NOTICES and not payload.get("proving"):
            goal = payload.get("goal") or {}
            notice = tr(NOTICES[payload["status"]])
            self.noticed.emit(notice, f"{goal.get('objective', '')}\n{payload.get('detail', '')}")
            if cid != (self.app.current or ""):
                self.app.flash(tr("{notice}: «{objective}»", notice=notice, objective=goal.get("objective", "")[:60]))
        if cid != (self.app.current or ""):
            return
        if kind == "goal.checking":
            self._checking = "commands"
            self.changed.emit()
        elif kind == "goal.judging":
            self._checking = "judge"
            self.changed.emit()
        else:
            self.load()

    # ---------- the contract card ----------

    @Property("QVariantMap", notify=editorChanged)
    def editor(self) -> dict:
        return self._editor

    @Property(bool, notify=editorChanged)
    def drafting(self) -> bool:
        return self._drafting

    @Property(bool, notify=editorChanged)
    def starting(self) -> bool:
        return self._starting

    @Property(str, notify=editorChanged)
    def error(self) -> str:
        return self._error

    @Property(list, notify=editorChanged)
    def projectNames(self) -> list:
        names = {p["id"]: p["name"] for p in self.app.projects.items}
        return [names[pid] for pid in self.app._chat_projects if pid in names]

    @Property(str, notify=editorChanged)
    def cost(self) -> str:
        return cost_text(self.app.models.get(self.app._model_id), self._editor.get("tokens", ""))

    @Property(str, notify=editorChanged)
    def workerName(self) -> str:
        model = self.app.models.get(self.app._model_id) or {}
        return model.get("name") or self.app._model_id or tr("модель не выбрана")

    @Property(list, notify=editorChanged)
    def judgeItems(self) -> list:
        role = self.app.role_model("judge")
        named = (self.app.models.get(role) or {}).get("name", "")
        chosen = self._editor.get("judge", "")
        items = [{"id": "", "name": tr("Как в «Ролях»: {name}", name=named or tr("не выбрана")), "icon": "check",
                  "note": tr("модель роли «Проверка цели»") if named else tr("без судьи цель из команд закрывают "
                  "команды, а критерии словами ждут вас"), "selected": not chosen}]  # fmt: skip
        for model_id, model in self.app.models.items():
            if model.get("provider") == "claude-code" or not model.get("enabled", True):
                continue
            items.append({"id": model_id, "name": model.get("name") or model_id,
                          "icon": "cloud" if model.get("locality") == "cloud" else "cpu",
                          "selected": chosen == model_id})  # fmt: skip
        return items

    @Property(str, notify=editorChanged)
    def judgeName(self) -> str:
        chosen = self._editor.get("judge", "")
        if chosen:
            return (self.app.models.get(chosen) or {}).get("name") or chosen
        role = self.app.role_model("judge")
        return ((self.app.models.get(role) or {}).get("name") or "") if role else tr("нет (только команды)")

    def _edit(self, **changes: Any) -> None:
        self._editor = {**self._editor, **changes}
        self._error = ""
        self.editorChanged.emit()

    @Slot(str)
    def openEditor(self, wish: str) -> None:
        """The contract card, with the user's words (from the composer or «/цель …»); with none, as it was left."""
        if wish.strip() or not (self._editor.get("wish") or self._editor.get("objective")):
            self._editor = {**blank_editor(), "wish": wish.strip()}
        self._error = ""
        self.editorChanged.emit()
        self.openRequested.emit()

    @Property(bool, notify=editorChanged)
    def available(self) -> bool:
        """A goal needs a model of the hub's own loop: Claude Code carries none yet (11.7)."""
        return (self.app.models.get(self.app._model_id) or {}).get("provider") != "claude-code"

    @Property(str, notify=editorChanged)
    def drafterName(self) -> str:
        """Who drafts the contract: the model of «Проверка цели», else the chat's own."""
        role = self.app.role_model("judge")
        return (self.app.models.get(role) or {}).get("name", "") if role else self.workerName

    @Slot(str, str)
    def setField(self, name: str, value: str) -> None:
        if name in ("wish", "objective", "constraints", "milestones", "turns", "tokens", "minutes", "judge"):
            self._edit(**{name: value})

    @Slot(int, str, str)
    def setCriterion(self, index: int, field: str, value: str) -> None:
        criteria = [dict(c) for c in self._editor["criteria"]]
        if 0 <= index < len(criteria) and field in ("kind", "command", "project", "code", "rule"):
            criteria[index][field] = value
            self._edit(criteria=criteria)

    @Slot(str)
    def addCriterion(self, kind: str) -> None:
        project = (self.projectNames or [""])[0]
        row = {"kind": "semantic" if kind == "semantic" else "command", "command": "", "project": project,
               "code": "0", "rule": ""}  # fmt: skip
        self._edit(criteria=[*self._editor["criteria"], row])

    @Slot(int)
    def removeCriterion(self, index: int) -> None:
        criteria = list(self._editor["criteria"])
        if 0 <= index < len(criteria):
            del criteria[index]
            self._edit(criteria=criteria)

    @Slot()
    def draft(self) -> None:
        """«Составить договор»: a model drafts the contract from the wish."""
        wish = self._editor.get("wish", "").strip()
        if not wish:
            self._error = tr("Напишите, чего хотите добиться")
            self.editorChanged.emit()
            return
        model_id = self.app._model_id
        self._drafting = True
        self._error = ""
        self.editorChanged.emit()

        def failed(error: Exception) -> None:
            self._drafting = False
            self._error = tr("Черновик не получился: {error}", error=error)
            self.editorChanged.emit()

        def drafted(found: dict[str, Any]) -> None:
            self._drafting = False
            self._editor = editor_of_draft(self._editor, found)
            self.editorChanged.emit()

        self.app.ensure_chat(lambda cid: self.app.call_or_fail(
            lambda: self.app.client.draft_goal(cid, wish, model_id), drafted, failed), failed)  # fmt: skip

    @Slot()
    def start(self) -> None:
        """«Подтвердить и запустить»: the goal is set (its commands approved), its first turn starts."""
        contract, wrong = contract_of(self._editor, self.app._model_id, self.app._effort)
        if contract is None:
            self._error = wrong
            self.editorChanged.emit()
            return
        self._starting = True
        self._error = ""
        self.editorChanged.emit()

        def failed(error: Exception) -> None:
            self._starting = False
            self._error = tr("Цель не поставлена: {error}", error=error)
            self.editorChanged.emit()

        def started(goal: dict[str, Any]) -> None:
            self._starting = False
            self._editor = blank_editor()
            self.editorChanged.emit()
            self.started.emit()
            self.app.flash(tr("Цель поставлена: модель начала работу, хаб проверит её после каждого хода"))
            self.load()

        self.app.ensure_chat(lambda cid: self.app.call_or_fail(
            lambda: self.app.client.set_goal(cid, contract), started, failed), failed)  # fmt: skip
