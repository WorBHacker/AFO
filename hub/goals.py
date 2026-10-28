"""A chat's goal (stage 11.1, D44, Q26; the user's plan - docs/goal_engine.md): the chat works turn after turn until
the hub's check says the goal is met, a limit is reached, or it needs the human.

Three parts, and only the check closes a goal. The worker is the chat's model with its tools and rights (D17): it
does the work but cannot declare the goal met. The orchestrator is this module - code, not a model: when a turn of
the chat ends (hub/chat.py calls turn_ended) it counts what the goal used, runs the checks and takes one of four ways:
    continue  - not met, the limits are fine: a note of the hub (what is missing, what the user added meanwhile),
                then the next turn;
    achieved  - every criterion is met;
    blocked   - it needs the human: the turn failed (the model, a budget), the judge could not say, nobody can judge
                the criteria in words, or tests were lost twice in a row;
    budget    - a limit is near: from soft_ratio of one on the worker gets its last turn (leave the files working,
                write down what is done and what is left); after it the goal is parked.
The user may pause it (the running turn finishes, no next one starts), resume it (a parked one with higher limits),
clear it, and write to it while it works: steering, read at the start of the next turn. Active goals go on after a
hub restart (D16).

The check has two contours (Q26, hub/judge.py). First without a model: each criterion "command" runs in a hub
terminal of its project (in «Песочница» - in the worktree) and must end with its exit code; the files changed since
the goal began must still parse; no test may be removed, skipped or commented out (that is REGRESSED by itself). A
failure goes straight back to the worker - the command's last lines, the file and line. When all of that is clean,
the judge - the model of the role «Проверка цели», or the one the goal names, that only reads - compares the
contract with the diff and the output and says ACHIEVED / INCOMPLETE / REGRESSED with what is missing; its word goes
to the worker. With no judge a goal of commands is met by them; one with criteria in words waits for the human.

The contract lives apart from the conversation (migrations 031-032); each check is only appended (D14), and the notes
between turns are stored as the hub's messages of the chat, visible like the rest.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import uuid
from typing import Any

from . import approvals, commands, judge, store
from .autonomous import NOTE
from .chat import ChatService, Limits, Outcome
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database, utc_now
from .events import EventBus
from .gateway import GatewayError
from .projects import ProjectError, get_project
from .secret_filter import clean
from .tools import ToolError

log = logging.getLogger("hub.goals")

OPEN = ("active", "paused", "blocked", "budget")
DEFAULTS = {"max_turns": 25, "max_tokens": 500_000, "max_minutes": 60, "soft_ratio": 0.88}  # Q26
BOUNDS = {"max_turns": (1, 500), "max_tokens": (1_000, 50_000_000), "max_minutes": (1, 24 * 60)}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TURN_STEPS = 30  # tool steps within one turn of a goal (a chat's own answer has 12)
MAX_CRITERIA = 10
MAX_CONSTRAINTS = 20
CHECK_TIMEOUT_S = 300  # a criterion's command, by default
TAIL_LINES = 30  # of a failed criterion's command, for the worker
TITLE_CHARS = 48


class GoalError(ValueError):
    status = 422


class GoalConflict(GoalError):
    status = 409


class GoalMissing(GoalError):
    status = 404


# ---------------------------------------------------------------- the contract as data


def _limits(data: Any, base: dict[str, Any]) -> dict[str, Any]:
    """The goal's limits: `base` with what `data` asks, each within its bounds."""
    data = data if isinstance(data, dict) else {}
    out = dict(base)
    for key, (low, high) in BOUNDS.items():
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise GoalError(f"{key}: a whole number from {low} to {high}")
        out[key] = value
    ratio = data.get("soft_ratio")
    if ratio is not None:
        if isinstance(ratio, bool) or not isinstance(ratio, int | float) or not 0.5 <= ratio <= 1.0:
            raise GoalError("soft_ratio: a share from 0.5 to 1.0")
        out["soft_ratio"] = float(ratio)
    return out


def _attached(conn: sqlite3.Connection, conversation_id: str) -> list:
    attached = []
    for pid in store.conversation_projects(conn, conversation_id):
        with contextlib.suppress(ProjectError):
            attached.append(get_project(conn, pid))
    return attached


def _project_of(conn: sqlite3.Connection, conversation_id: str, ref: Any, n: int) -> str:
    """The chat's attached project a criterion runs in: by id or by name; the only one when it says none."""
    attached = _attached(conn, conversation_id)
    ref = str(ref or "").strip()
    if not ref and len(attached) == 1:
        return attached[0].id
    found = next((p for p in attached if p.id == ref or p.name.casefold() == ref.casefold()), None)
    if found is None:
        names = ", ".join(f"«{p.name}»" for p in attached) or "none"
        raise GoalError(f"criterion {n}: name the project it runs in - one attached to the chat ({names})")
    return found.id


def check_contract(conn: sqlite3.Connection, conversation_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """The goal as the user set it, checked: what is wanted, the criteria, the constraints, the worker, the limits."""
    if not store.conversation_exists(conn, conversation_id):
        raise GoalMissing("no such conversation")
    if conn.execute("SELECT 1 FROM agent_runs WHERE conversation_id = ?", (conversation_id,)).fetchone():
        raise GoalError("this is an agent's task: it has a goal of its own")
    objective = " ".join(str(data.get("objective") or "").split())[:4000]
    if not objective:
        raise GoalError("say what the goal is (`objective`)")
    raw = data.get("criteria")
    if not isinstance(raw, list) or not raw:
        raise GoalError("a goal needs at least one criterion: a command and its exit code, or a rule in words")
    if len(raw) > MAX_CRITERIA:
        raise GoalError(f"at most {MAX_CRITERIA} criteria")
    criteria = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise GoalError(f"criterion {n}: an object - {{command, project, expected_exit_code}} or {{rule}}")
        kind = item.get("kind") or ("command" if item.get("command") else "semantic")
        if kind == "semantic":
            rule = " ".join(str(item.get("rule") or "").split())[:1000]
            if not rule:
                raise GoalError(f"criterion {n}: say the rule in words (`rule`)")
            criteria.append({"kind": "semantic", "rule": rule})
            continue
        if kind != "command":
            raise GoalError(f"criterion {n}: kind is command or semantic")
        try:
            command = commands.command_of(item)
        except ToolError as exc:
            raise GoalError(f"criterion {n}: {exc}") from None
        if approvals.destructive("run_command", {"command": command}) or approvals.network(
            "run_command", {"command": command}
        ):
            raise GoalError(f"criterion {n}: «{command}» changes things or goes to the network - a check only looks")
        expected = item.get("expected_exit_code", 0)
        timeout = item.get("timeout_s", CHECK_TIMEOUT_S)
        for name, value in (("expected_exit_code", expected), ("timeout_s", timeout)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise GoalError(f"criterion {n}: `{name}` is a whole number")
        path = item.get("path") or ""
        if not isinstance(path, str):
            raise GoalError(f"criterion {n}: `path` is a folder inside the project")
        criteria.append({"kind": "command", "project_id": _project_of(conn, conversation_id, item.get("project"), n),
                         "command": command, "path": path.strip(), "expected_exit_code": expected,
                         "timeout_s": max(1, min(timeout, commands.MAX_TIMEOUT_S))})  # fmt: skip
    constraints = data.get("constraints") or []
    if not isinstance(constraints, list):
        raise GoalError("`constraints` is a list of rules")
    constraints = [" ".join(str(c).split())[:500] for c in constraints if str(c).strip()]
    if len(constraints) > MAX_CONSTRAINTS:
        raise GoalError(f"at most {MAX_CONSTRAINTS} constraints")
    model_id = str(data.get("model_id") or "")
    model = store.get_model(conn, model_id) if model_id else None
    if model is None:
        raise GoalError("choose the model that works on the goal (`model_id`)")
    if not model["enabled"]:
        raise GoalError(f"model {model_id!r} is not available now")
    if model["provider"] == CLAUDE_CODE:
        raise GoalError("Claude Code carries no goal yet (stage 11.7): choose another model")
    effort = str(data.get("effort") or "medium")
    if effort not in EFFORTS:
        raise GoalError(f"effort: one of {', '.join(EFFORTS)}")
    limits = _limits(data.get("limits"), DEFAULTS)
    # the judge (11.2): the one named, else the role «Проверка цели» (read again at each check while none is named)
    named = str(data.get("judge_model_id") or "") or None
    judge_id = named or judge.role_model(conn)
    if judge_id:  # said now, not after the first turn
        try:
            judge.check_model(conn, judge_id, _attached(conn, conversation_id))
        except judge.JudgeError as exc:
            raise GoalError(str(exc)) from None
    return {"objective": objective, "criteria": criteria, "constraints": constraints, "model_id": model_id,
            "effort": effort, "judge_model_id": named, **limits}  # fmt: skip


def create_goal(conn: sqlite3.Connection, conversation_id: str, contract: dict[str, Any], *, by: str) -> str:
    if goal_of(conn, conversation_id) is not None:
        raise GoalConflict("the chat has a goal already: clear it first")
    goal_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO goals (id, conversation_id, objective, constraints, model_id, effort, max_turns, max_tokens,"
        " max_seconds, soft_ratio, created_by, judge_model_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (goal_id, conversation_id, contract["objective"], json.dumps(contract["constraints"], ensure_ascii=False),
         contract["model_id"], contract["effort"], contract["max_turns"], contract["max_tokens"],
         contract["max_minutes"] * 60, contract["soft_ratio"], by, contract.get("judge_model_id")),
    )  # fmt: skip
    for n, c in enumerate(contract["criteria"], 1):
        conn.execute(
            "INSERT INTO goal_criteria (goal_id, n, kind, project_id, command, path, expected_exit_code, timeout_s,"
            " rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal_id, n, c["kind"], c.get("project_id"), c.get("command"), c.get("path", ""),
             c.get("expected_exit_code"), c.get("timeout_s"), c.get("rule")),
        )  # fmt: skip
    return goal_id


def _row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    goal = dict(row)
    goal["constraints"] = json.loads(row["constraints"])
    goal["max_minutes"] = round(row["max_seconds"] / 60)
    goal["used"] = round(used(goal), 3)
    found = conn.execute(
        "SELECT c.*, p.name AS project_name FROM goal_criteria c LEFT JOIN projects p ON p.id = c.project_id"
        " WHERE c.goal_id = ? ORDER BY c.n",
        (row["id"],),
    ).fetchall()
    goal["criteria"] = [{k: v for k, v in dict(c).items() if k != "goal_id"} | {"verified": bool(c["verified"])}
                        for c in found]  # fmt: skip
    return goal


def get_goal(conn: sqlite3.Connection, goal_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise GoalMissing("no such goal")
    return _row(conn, row)


def goal_of(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any] | None:
    """The chat's open goal (active, paused, blocked or parked), or None."""
    row = conn.execute(
        f"SELECT * FROM goals WHERE conversation_id = ? AND status IN ({', '.join('?' * len(OPEN))})",
        (conversation_id, *OPEN),
    ).fetchone()
    return _row(conn, row) if row is not None else None


def latest_goal(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any] | None:
    """The open goal, else the last one the chat had (achieved or cleared)."""
    row = conn.execute("SELECT * FROM goals WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                       (conversation_id,)).fetchone()  # fmt: skip
    return _row(conn, row) if row is not None else None


def checks(conn: sqlite3.Connection, goal_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """The goal's checks, the newest last."""
    rows = conn.execute("SELECT * FROM goal_checks WHERE goal_id = ? ORDER BY id DESC LIMIT ?",
                        (goal_id, limit)).fetchall()  # fmt: skip
    return [{**dict(r), "results": json.loads(r["results"]), "missing": json.loads(r["missing"])}
            for r in reversed(rows)]  # fmt: skip


def steering(conn: sqlite3.Connection, goal_id: str, *, pending: bool = True) -> list[dict[str, Any]]:
    where = " AND taken_at IS NULL" if pending else ""
    rows = conn.execute(f"SELECT * FROM goal_steering WHERE goal_id = ?{where} ORDER BY id", (goal_id,)).fetchall()
    return [dict(r) for r in rows]


def used(goal: dict[str, Any]) -> float:
    """How much of its nearest limit the goal used: 1.0 - a limit is reached."""
    return max(goal["turns_used"] / goal["max_turns"], goal["tokens_used"] / goal["max_tokens"],
               goal["seconds_used"] / goal["max_seconds"])  # fmt: skip


def usage_line(goal: dict[str, Any], *, ahead: int = 1) -> str:
    return (f"Turn {goal['turns_used'] + ahead} of {goal['max_turns']} · tokens {goal['tokens_used']} of "
            f"{goal['max_tokens']} · minutes {goal['seconds_used'] / 60:.0f} of {goal['max_minutes']}.")  # fmt: skip


def _limit_words(goal: dict[str, Any]) -> str:
    """Which limit is the nearest, in the user's words."""
    shares = [(goal["turns_used"] / goal["max_turns"], f"ходов {goal['turns_used']} из {goal['max_turns']}"),
              (goal["tokens_used"] / goal["max_tokens"], f"токенов {goal['tokens_used']} из {goal['max_tokens']}"),
              (goal["seconds_used"] / goal["max_seconds"],
               f"минут {goal['seconds_used'] / 60:.0f} из {goal['max_minutes']}")]  # fmt: skip
    return max(shares)[1]


# ---------------------------------------------------------------- the notes the worker reads


def criteria_text(goal: dict[str, Any]) -> str:
    lines = []
    for c in goal["criteria"]:
        if c["kind"] == "command":
            where = f" in «{c['project_name'] or c['project_id']}»" + (f" (folder {c['path']})" if c["path"] else "")
            lines.append(f"{c['n']}. `{c['command']}`{where} ends with exit code {c['expected_exit_code']}")
        else:
            lines.append(f"{c['n']}. {c['rule']}")
    return "\n".join(lines)


def start_note(goal: dict[str, Any], *, head: str = "", said: list[str] | None = None) -> str:
    """The first turn's word: the contract and how the work goes (`head`: why it starts again - a restart)."""
    rules = "\n".join(f"- {c}" for c in goal["constraints"])
    words = "\n".join(said or [])
    return (
        f'{NOTE} <goal_loop type="start">\n'
        + (f"{head}\n" if head else "")
        + "You work toward a goal over as many turns as it takes (goal mode). After each of your turns the hub checks "
        "the goal itself - it runs the criteria's commands - and tells you what is missing. You cannot declare the "
        "goal met: the check does.\n"
        f"GOAL: {goal['objective']}\n"
        f"DONE WHEN (the hub checks each after every turn):\n{criteria_text(goal)}\n"
        + (f"HARD CONSTRAINTS - never break them:\n{rules}\n" if rules else "")
        + f"LIMITS: {goal['max_turns']} turns, {goal['max_tokens']} tokens, {goal['max_minutes']} minutes.\n"
        + (f"THE USER ADDED:\n{words}\n" if words else "")
        + "Work with your tools now. End each turn with two short lines: what you did, what is next. No greetings and "
        "no reports to the user between turns.\n"
        "</goal_loop>"
    )


def _missing_text(results: list[dict[str, Any]], extra: list[str] | None = None) -> str:
    """What a check found missing, as lines: the criteria not met, then what else the check found (`extra`: files
    that do not parse, tests lost, the judge's items)."""
    out = []
    for r in results:
        if r.get("met") or r["kind"] not in ("command", "semantic"):
            continue
        if r["kind"] == "semantic":
            out.append(f"- criterion {r['n']} (in words, the judge checks it): {r['rule']}")
            continue
        out.append(f"- criterion {r['n']}: `{r['command']}` {r['why']}" + (
            f". Its last lines:\n{r['tail']}" if r.get("tail") else ""))  # fmt: skip
    return "\n".join([*out, *(f"- {item}" for item in extra or [])])


def _check_text(found: dict[str, Any]) -> str:
    missing = _missing_text(found["results"], found.get("extra") or [])
    verdict = found.get("verdict") or "INCOMPLETE"
    head = {"REGRESSED": "REGRESSED - something that worked was lost (see below); put it back first",
            "INCOMPLETE": "not met"}.get(verdict, "not met")  # fmt: skip
    text = f"CHECK OF YOUR LAST TURN: {head}.\n{missing}\n" if missing else ""
    return text + (f"THE JUDGE SAYS: {found['feedback']}\n" if found.get("feedback") else "")


def continue_note(goal: dict[str, Any], found: dict[str, Any], said: list[str], *, head: str = "") -> str:
    """The next turn's word (the user's plan 4.1): the goal, what the check found missing, what the user added."""
    words = "\n".join(said)
    return (
        f'{NOTE} <goal_loop type="continuation">\n'
        + (f"{head}\n" if head else "")
        + "The work on the goal goes on. No greetings, no report to the user - go straight to your tools.\n"
        f"GOAL: {goal['objective']}\n"
        + _check_text(found)
        + (f"THE USER ADDED WHILE YOU WORKED - it comes first in this step:\n{words}\n" if words else "")
        + f"{usage_line(goal)}\n"
        "Do the next step toward the goal.\n"
        "</goal_loop>"
    )


def last_turn_note(goal: dict[str, Any], found: dict[str, Any], said: list[str]) -> str:
    """The last turn before the limit (the user's plan 4.2): stop experimenting, leave it working, hand it over."""
    words = "\n".join(said)
    return (
        f'{NOTE} <goal_loop type="budget_limit">\n'
        f"The goal's limit is almost used up ({usage_line(goal, ahead=0)}). This is your LAST turn before the hub "
        "stops the work.\n"
        f"GOAL: {goal['objective']}\n"
        "Do only this:\n"
        "1. Start nothing new - no refactoring, no new modules.\n"
        "2. Leave every file you changed in a working state - no half-written code.\n"
        "3. End with a handover report: what is done and checked; where the work stopped (the file and the line); "
        "what to do when the goal is resumed.\n"
        + _check_text(found)
        + (f"THE USER ADDED WHILE YOU WORKED:\n{words}\n" if words else "")
        + "</goal_loop>"
    )


# ---------------------------------------------------------------- the goal's draft (the plan's «goal compiler», 7.6)

DRAFT_SYSTEM = """You turn what a user wants into a goal contract for an AI worker. The worker works on it turn after \
turn in the user's projects; after each turn the hub checks the contract's criteria itself.
Make it measurable. Prefer criteria the hub can run: one PowerShell command line in an attached project and the exit \
code that means done - the project's tests, a build, a check such as `if (Test-Path done.txt) { exit 0 } else { exit \
1 }`. Add a criterion in words only for what no command can check. A command only looks: never install, download, \
push or delete. Add the hard constraints the wish implies (what must not be touched). Suggest limits: turns (5 to 40) \
and minutes. Write the objective, the rules and the constraints in the user's language.
Answer with JSON only:
{"objective": "...", "criteria": [{"command": "...", "project": "<an attached project's name>", \
"expected_exit_code": 0}, {"rule": "..."}], "constraints": ["..."], "limits": {"max_turns": 20, "max_minutes": 60}}"""
DRAFT_FORM = '{"objective": ..., "criteria": [...], "constraints": [...], "limits": {...}}'
MANIFESTS = ("package.json", "pyproject.toml", "pytest.ini", "setup.cfg", "Cargo.toml", "go.mod", "Makefile",
             "CMakeLists.txt", "build.gradle", "pom.xml")  # fmt: skip
LISTED = 60  # a project's top-level names the draft sees
MANIFEST_LINES = 40


def draft_context(projects: list, *, cloud: bool) -> str:
    """What the draft's model learns of the chat's projects: the top level and the build files - for a cloud model
    only of projects that allow the cloud (D10), the others by name (blocking: the disk)."""
    parts = []
    for p in projects:
        if cloud and not p.cloud_ok:
            parts.append(f'<project name="{p.name}">only local models may see it</project>')
            continue
        try:
            names = sorted(n.name + ("/" if n.is_dir() else "") for n in p.root.iterdir() if not n.name.startswith("."))
        except OSError:
            names = []
        found = [f"{name}:\n" + clean("\n".join((p.root / name).read_text(encoding="utf-8", errors="replace")
                                               .splitlines()[:MANIFEST_LINES]))
                 for name in MANIFESTS if (p.root / name).is_file()]  # fmt: skip
        parts.append(f'<project name="{p.name}">\nTOP LEVEL: {", ".join(names[:LISTED])}\n' + "\n".join(found) +
                     "\n</project>")  # fmt: skip
    return "\n".join(parts) or "(no project is attached: criteria in words only, or the chat's own folder)"


def read_draft(text: str, project_names: list[str]) -> dict[str, Any] | None:
    """The draft's JSON made fit for the contract form: what does not fit is left out (and said in `notes`)."""
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not str(data.get("objective") or "").strip():
        return None
    notes: list[str] = []
    criteria = []
    for item in data.get("criteria") or []:
        if not isinstance(item, dict):
            continue
        command = " ".join(str(item.get("command") or "").split())
        if command:
            if approvals.destructive("run_command", {"command": command}) or approvals.network(
                "run_command", {"command": command}
            ):
                notes.append(f"команда «{command}» убрана: проверка только смотрит")
                continue
            project = str(item.get("project") or "")
            match = next((n for n in project_names if n.casefold() == project.casefold()), None)
            if match is None and project_names:
                match = project_names[0]
            code = item.get("expected_exit_code", 0)
            criteria.append({"kind": "command", "command": command, "project": match or "",
                             "expected_exit_code": code if isinstance(code, int) and not isinstance(code, bool)
                             else 0})  # fmt: skip
        elif str(item.get("rule") or "").strip():
            criteria.append({"kind": "semantic", "rule": " ".join(str(item["rule"]).split())[:1000]})
    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    out_limits = {}
    for key in ("max_turns", "max_minutes"):
        low, high = BOUNDS[key]
        value = limits.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            out_limits[key] = max(low, min(value, high))
    constraints = [" ".join(str(c).split())[:500] for c in data.get("constraints") or [] if str(c).strip()]
    return {"objective": " ".join(str(data["objective"]).split())[:4000], "criteria": criteria[:MAX_CRITERIA],
            "constraints": constraints[:MAX_CONSTRAINTS], "limits": out_limits, "notes": notes}  # fmt: skip


def _found_of(check: dict[str, Any]) -> dict[str, Any]:
    """A stored check as the notes read it (the next turn after a pause or a restart)."""
    criteria = [r for r in check["results"] if r.get("kind") in ("command", "semantic")]
    extra = next((r for r in check["results"] if r.get("kind") == "found"), {})
    return {"results": criteria, "extra": extra.get("extra") or [], "feedback": extra.get("feedback") or "",
            "verdict": check["verdict"]}  # fmt: skip


# ---------------------------------------------------------------- the orchestrator


class Goals:
    """Sets, pauses, resumes and clears chats' goals; after each turn of such a chat checks it and goes on, or not."""

    def __init__(self, db: Database, bus: EventBus, chat: ChatService, judging: judge.Judge):
        self._db = db
        self._bus = bus
        self._chat = chat
        self._judge = judging  # what changed and the judge's verdict (11.2)
        self._locks: dict[str, asyncio.Lock] = {}  # one check at a time per chat
        self._counted: set[str] = set()  # chats whose running turn is the goal's: it counts even if paused meanwhile

    async def start(self) -> list[str]:
        """Goals the last hub left at work go on (D16): each active one gets its next turn."""
        found = await self._db.acall(lambda conn: [r[0] for r in conn.execute(
            "SELECT conversation_id FROM goals WHERE status = 'active' ORDER BY created_at")])  # fmt: skip
        for conversation_id in found:
            goal = await self._db.acall(lambda conn, c=conversation_id: goal_of(conn, c))
            if goal is not None and conversation_id not in self._chat.busy:
                await self._next(goal, head="The hub restarted: your last turn may have been cut off - look where the "
                                            "work stands first.")  # fmt: skip
        return found

    async def get(self, conversation_id: str) -> dict[str, Any]:
        def read(conn):
            if not store.conversation_exists(conn, conversation_id):
                raise GoalMissing("no such conversation")
            goal = latest_goal(conn, conversation_id)
            if goal is None:
                return {"goal": None, "checks": [], "steering": []}
            return {"goal": goal, "checks": checks(conn, goal["id"]), "steering": steering(conn, goal["id"])}

        found = await self._db.acall(read)
        found["checking"] = conversation_id in self._locks and self._locks[conversation_id].locked()
        return found

    async def set(self, conversation_id: str, data: dict[str, Any], *, by: str) -> dict[str, Any]:
        """A new goal for the chat (the app's: setting it approves its criteria's commands); its first turn starts."""
        if conversation_id in self._chat.busy:
            raise GoalConflict("the chat is answering now: set the goal when the answer is done")

        def create(conn):
            contract = check_contract(conn, conversation_id, data)
            return get_goal(conn, create_goal(conn, conversation_id, contract, by=by))

        try:
            goal = await self._db.acall(create)
        except sqlite3.IntegrityError:
            raise GoalConflict("the chat has a goal already: clear it first") from None
        await self._baselines(goal)
        await self._bus.publish("goal.set", {"conversation_id": conversation_id, "goal": goal})
        await self._turn(goal, start_note(goal))
        return await self._db.acall(lambda conn: get_goal(conn, goal["id"]))

    async def draft(self, conversation_id: str, text: str, model_id: str) -> dict[str, Any]:
        """A contract drafted by a model from the user's words (the plan's goal compiler): the model of the role
        «Проверка цели», else the chat's own. Nothing is set: the window shows it to be checked and changed."""
        text = text.strip()[:4000]
        if not text:
            raise GoalError("say what you want (`text`)")

        def read(conn):
            if not store.conversation_exists(conn, conversation_id):
                raise GoalMissing("no such conversation")
            chosen = judge.role_model(conn) or model_id
            return chosen, store.get_model(conn, chosen) if chosen else None, _attached(conn, conversation_id)

        chosen, model, projects = await self._db.acall(read)
        if model is None or not model["enabled"]:
            raise GoalError("choose the chat's model first: it drafts the goal (or the model of «Проверка цели»)")
        if model["provider"] == CLAUDE_CODE:
            raise GoalError("Claude Code does not draft goals: choose another model, or one for «Проверка цели»")
        context = await asyncio.to_thread(draft_context, projects, cloud=model["locality"] == "cloud")
        names = [p.name for p in projects]
        prompt = clean(f"<projects>\n{context}\n</projects>\n<wish>\n{text}\n</wish>\nWrite the goal contract. "
                       "Answer with the JSON only.")  # fmt: skip
        try:
            found = await self._judge.complete(chosen, DRAFT_SYSTEM, prompt, lambda answer: read_draft(answer, names),
                                               conversation_id=conversation_id, meta={"purpose": "goal_draft"},
                                               form=DRAFT_FORM)  # fmt: skip
        except judge.JudgeError as exc:
            raise GoalError(f"the draft did not come: {exc}") from None
        return {"draft": {k: found[k] for k in ("objective", "criteria", "constraints", "limits")},
                "notes": found["notes"], "model_id": chosen, "cost_usd": found["cost_usd"]}  # fmt: skip

    async def _baselines(self, goal: dict[str, Any]) -> None:
        """Where each git project's files stand as the goal begins: the judge's diff runs from there (11.2)."""
        for project in await self._chat.tool_projects(goal["conversation_id"]):
            if not judge.is_git(project.root):
                continue
            try:
                tree = await asyncio.to_thread(judge.snapshot, project.root)
            except judge.JudgeError as exc:
                log.warning("goal %s: no starting point for %s: %s", goal["id"], project.name, exc)
                continue
            await self._db.acall(lambda conn, p=project.id, t=tree: conn.execute(
                "INSERT OR REPLACE INTO goal_baselines (goal_id, project_id, tree) VALUES (?, ?, ?)",
                (goal["id"], p, t)))  # fmt: skip

    async def pause(self, conversation_id: str, *, by: str) -> dict[str, Any]:
        """No next turn starts; a turn running now finishes (and counts)."""
        goal = await self._open(conversation_id)
        if goal["status"] != "active":
            raise GoalConflict(f"the goal is {goal['status']}, not at work")
        return await self._status(goal, "paused", f"на паузе ({by})")

    async def resume(self, conversation_id: str, limits: dict[str, Any] | None, *, by: str) -> dict[str, Any]:
        """Back to work - a parked goal with limits it has not used up yet; the next turn starts."""
        goal = await self._open(conversation_id)
        if goal["status"] == "active":
            raise GoalConflict("the goal is at work already")
        if limits:
            new = _limits(limits, {"max_turns": goal["max_turns"], "max_tokens": goal["max_tokens"],
                                   "max_minutes": goal["max_minutes"], "soft_ratio": goal["soft_ratio"]})  # fmt: skip
            raised = (new["max_turns"], new["max_tokens"], new["max_minutes"] * 60, new["soft_ratio"], goal["id"])
            await self._db.acall(lambda conn: conn.execute(
                "UPDATE goals SET max_turns = ?, max_tokens = ?, max_seconds = ?, soft_ratio = ? WHERE id = ?",
                raised))  # fmt: skip
            goal = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
        if goal["status"] == "budget":
            if used(goal) >= 1.0:
                raise GoalConflict("its limits are used up: raise them to resume it")
            await self._db.acall(lambda conn: conn.execute("UPDATE goals SET last_turn = 0 WHERE id = ?",
                                                           (goal["id"],)))  # fmt: skip
        goal = await self._status(goal, "active", "")
        if conversation_id not in self._chat.busy:  # else the turn that runs now hands over when it ends
            await self._next(goal, head=f"The user resumed the goal ({by}).")
        return await self._db.acall(lambda conn: get_goal(conn, goal["id"]))

    async def clear(self, conversation_id: str, *, by: str) -> dict[str, Any]:
        """The goal is given up: no next turn (a turn running now finishes)."""
        goal = await self._open(conversation_id)
        return await self._status(goal, "cleared", f"снята ({by})", ended=True)

    async def steer(self, conversation_id: str, text: str, *, by: str) -> dict[str, Any]:
        """The user's word while the goal works: the worker reads it at the start of its next turn."""
        text = text.strip()[:4000]
        if not text:
            raise GoalError("Пустое сообщение")
        goal = await self._open(conversation_id)
        row_id = await self._db.acall(lambda conn: conn.execute(
            "INSERT INTO goal_steering (goal_id, text, by) VALUES (?, ?, ?)", (goal["id"], text, by)
        ).lastrowid)  # fmt: skip
        await self._bus.publish("goal.steered", {"conversation_id": conversation_id, "goal_id": goal["id"],
                                                 "id": row_id, "text": text})  # fmt: skip
        return {"id": row_id, "text": text}

    async def _open(self, conversation_id: str) -> dict[str, Any]:
        def read(conn):
            if not store.conversation_exists(conn, conversation_id):
                raise GoalMissing("no such conversation")
            return goal_of(conn, conversation_id)

        goal = await self._db.acall(read)
        if goal is None:
            raise GoalMissing("the chat has no goal")
        return goal

    async def _status(self, goal: dict[str, Any], status: str, detail: str, *, ended: bool = False) -> dict:
        now = utc_now()
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE goals SET status = ?, detail = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, detail, now, now if ended else None, goal["id"])))  # fmt: skip
        changed = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
        await self._bus.publish("goal.status", {"conversation_id": goal["conversation_id"], "goal_id": goal["id"],
                                                "status": status, "detail": detail, "goal": changed})  # fmt: skip
        return changed

    # ---------- after a turn ----------

    async def turn_ended(self, conversation_id: str, outcome: Outcome) -> None:
        """A turn of the chat ended (hub/chat.py): what the goal used, the check, and the way on."""
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            goal = await self._db.acall(lambda conn: goal_of(conn, conversation_id))
            mine = conversation_id in self._counted
            self._counted.discard(conversation_id)
            if goal is None and mine:  # cleared while its turn ran: the turn still counts
                goal = await self._db.acall(lambda conn: latest_goal(conn, conversation_id))
            if goal is None or not (goal["status"] == "active" or mine):
                return  # no goal, or the user chatted while it waited: not the goal's turn
            goal = await self._count(goal, outcome)
            if goal["status"] != "active":
                return
            if outcome.status == "cancelled":
                await self._checked(goal, "paused", None, {}, "остановлено: ход прерван")
                return
            if outcome.status == "error" and not outcome.limited:
                await self._checked(goal, "blocked", None, {}, f"ход не удался: {outcome.detail}")
                return
            await self._bus.publish("goal.checking", {"conversation_id": conversation_id, "goal_id": goal["id"]},
                                    persist=False)  # fmt: skip
            results = await self._run_checks(goal)
            found = await self._contour(goal, results)
            goal = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))  # the judge's use counted
            verdict = found["verdict"]
            if verdict == "ACHIEVED":
                await self._checked(goal, "achieved", "ACHIEVED", found, found["detail"])
                return
            if found.get("blocked"):
                await self._checked(goal, "blocked", verdict, found, found["blocked"])
                return
            if verdict == "REGRESSED" and await self._regressed_before(goal):
                again = ("второй раз подряд потеряно то, что работало (тесты или ограничения): решите, как быть, и "
                         "продолжите или снимите цель")  # fmt: skip
                await self._checked(goal, "blocked", verdict, found, again)
                return
            share = used(goal)
            if goal["last_turn"] or share >= 1.0:
                await self._checked(goal, "budget", verdict, found, f"предел: {_limit_words(goal)}")
                return
            said = await self._take_steering(goal)
            if share >= goal["soft_ratio"]:
                await self._db.acall(lambda conn: conn.execute("UPDATE goals SET last_turn = 1 WHERE id = ?",
                                                               (goal["id"],)))  # fmt: skip
                await self._checked(goal, "last_turn", verdict, found, f"последний ход: {_limit_words(goal)}")
                await self._turn(goal, last_turn_note(goal, found, said))
                return
            await self._checked(goal, "continue", verdict, found, "")
            await self._turn(goal, continue_note(goal, found, said))

    async def _contour(self, goal: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        """The check after the commands (Q26): what changed since the goal began, the hub's own look at it (syntax,
        tests lost), then - when that is clean - the judge. {verdict, results, extra, feedback, evidence, judge?,
        detail? (achieved), blocked? (why the human is needed)}."""
        conversation_id = goal["conversation_id"]
        projects = await self._chat.tool_projects(conversation_id)
        bases = await self._db.acall(lambda conn: {r[0]: r[1] for r in conn.execute(
            "SELECT project_id, tree FROM goal_baselines WHERE goal_id = ?", (goal["id"],))})  # fmt: skip
        try:
            evidence = await self._judge.evidence(conversation_id, projects, bases, goal["created_at"])
        except Exception as exc:  # what changed is unknown: the commands still count
            log.exception("goal %s: the changes could not be read", goal["id"])
            evidence = judge.Evidence(notes=[f"the changes could not be read: {exc}"])
        found: dict[str, Any] = {"results": results, "extra": evidence.missing(), "feedback": "",
                                 "evidence": evidence.summary()}  # fmt: skip
        commands_met = all(r["met"] for r in results if r["kind"] == "command")
        semantic = [r for r in results if r["kind"] == "semantic"]
        if evidence.sabotage:  # a green build bought by losing tests (the plan's rule 2): no judge needed
            return {**found, "verdict": "REGRESSED", "feedback": "Tests were removed, skipped or commented out. Put "
                    "them back and make the code pass them instead."}  # fmt: skip
        if not commands_met or evidence.syntax:  # the first contour failed: straight back to the worker
            return {**found, "verdict": "INCOMPLETE"}

        def choose(conn):
            judge_id = goal["judge_model_id"] or judge.role_model(conn)  # the role may be chosen since
            if judge_id:
                judge.check_model(conn, judge_id, projects)
            return judge_id, judge.last_words(conn, conversation_id)

        try:
            judge_id, words = await self._db.acall(choose)
        except judge.JudgeError as exc:
            return {**found, "verdict": None, "blocked": f"проверяющая модель не может проверить: {exc}"}
        if not judge_id:
            if semantic:
                nobody = ("команды прошли, а критерии словами проверить некому: выберите модель для роли «Проверка "
                          "цели» и продолжите — или проверьте сами и снимите цель")  # fmt: skip
                return {**found, "verdict": None, "blocked": nobody}
            return {**found, "verdict": "ACHIEVED", "detail": "цель достигнута: все критерии выполнены"}
        await self._bus.publish("goal.judging", {"conversation_id": conversation_id, "goal_id": goal["id"],
                                                 "model_id": judge_id}, persist=False)  # fmt: skip
        try:
            said = await self._judge.ask(judge_id, judge.goal_prompt(goal, results, evidence, words),
                                         conversation_id=conversation_id, meta={"goal_id": goal["id"]})  # fmt: skip
        except judge.JudgeError as exc:
            return {**found, "verdict": None, "blocked": f"проверяющая модель не ответила: {exc}"}
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE goals SET tokens_used = tokens_used + ?, cost_usd = cost_usd + ? WHERE id = ?",
            (said["tokens"], said["cost_usd"], goal["id"])))  # fmt: skip
        found["judge"] = {k: said[k] for k in ("status", "missing", "feedback", "model_id")}
        found = {**found, "verdict": said["status"], "extra": [*found["extra"], *said["missing"]],
                 "feedback": said["feedback"]}  # fmt: skip
        if said["status"] == "ACHIEVED":
            for r in semantic:
                r["met"] = True
            await self._db.acall(lambda conn: conn.executemany(
                "UPDATE goal_criteria SET verified = 1 WHERE goal_id = ? AND n = ?",
                [(goal["id"], r["n"]) for r in semantic]))  # fmt: skip
            found["detail"] = "цель достигнута: проверки прошли, проверяющая модель согласна"
        return found

    async def _regressed_before(self, goal: dict[str, Any]) -> bool:
        last = await self._db.acall(lambda conn: checks(conn, goal["id"], 1))
        return bool(last) and last[0]["verdict"] == "REGRESSED"

    async def _count(self, goal: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
        totals = outcome.totals
        usage = totals.usage if totals is not None else {}  # input_tokens is the whole prompt, the cache inside it
        tokens = max(int(usage.get("input_tokens") or 0) - int(usage.get("cached_input_tokens") or 0), 0) + int(
            usage.get("output_tokens") or 0
        )

        def write(conn):
            conn.execute(
                "UPDATE goals SET turns_used = turns_used + ?, tokens_used = tokens_used + ?, seconds_used ="
                " seconds_used + ?, cost_usd = cost_usd + ?, updated_at = ? WHERE id = ?",
                (1 if totals is not None else 0, tokens, round(outcome.seconds, 3),
                 totals.cost if totals is not None else 0.0, utc_now(), goal["id"]),
            )  # fmt: skip
            return get_goal(conn, goal["id"])

        return await self._db.acall(write)

    async def _run_checks(self, goal: dict[str, Any]) -> list[dict[str, Any]]:
        """The first contour: each command criterion, run in a terminal of its project; met - its exit code."""
        projects = {p.id: p for p in await self._chat.tool_projects(goal["conversation_id"])}
        results = []
        for c in goal["criteria"]:
            if c["kind"] == "semantic":
                results.append({"n": c["n"], "kind": "semantic", "met": None, "rule": c["rule"]})
                continue
            result = {"n": c["n"], "kind": "command", "command": c["command"], "met": False, "exit_code": None}
            project = projects.get(c["project_id"])
            if project is None:
                results.append({**result, "why": "was not run: its project is no longer attached to the chat"})
                continue
            short = c["command"] if len(c["command"]) <= TITLE_CHARS else c["command"][: TITLE_CHARS - 1] + "…"
            try:
                done = await commands.execute(self._chat.terminals, project, c["command"], folder=c["path"],
                                              timeout=c["timeout_s"], by="goal", title=f"Цель: {short}")  # fmt: skip
            except Exception as exc:  # a folder gone, a terminal that would not start
                log.warning("goal %s: criterion %s did not run: %s", goal["id"], c["n"], exc)
                results.append({**result, "why": f"did not run: {exc}"})
                continue
            tail = clean("\n".join(done["lines"][-TAIL_LINES:]))
            if done["status"] == "running":  # a check that hangs is a failed check: it does not go on
                with contextlib.suppress(Exception):
                    await self._chat.terminals.kill(done["terminal"])
                results.append({**result, "why": f"did not end within {done['timeout']} s", "tail": tail,
                                "terminal": done["terminal"]})  # fmt: skip
                continue
            met = done["exit_code"] == c["expected_exit_code"]
            why = "" if met else f"ended with exit code {done['exit_code']} (expected {c['expected_exit_code']})"
            results.append({**result, "met": met, "exit_code": done["exit_code"], "why": why,
                            "tail": "" if met else tail, "terminal": done["terminal"]})  # fmt: skip
        await self._db.acall(lambda conn: conn.executemany(
            "UPDATE goal_criteria SET verified = ? WHERE goal_id = ? AND n = ?",
            [(1 if r["met"] else 0, goal["id"], r["n"]) for r in results]))  # fmt: skip
        return results

    async def _checked(self, goal: dict[str, Any], route: str, verdict: str | None, found: dict[str, Any],
                       detail: str) -> None:  # fmt: skip
        """The check is appended (D14), and the goal takes the way it found. `found` (from _contour): the criteria's
        results, what else is missing, the judge's word, what changed - kept with the check."""
        criteria = found.get("results") or []
        missing = [line[2:] for line in _missing_text(criteria, found.get("extra")).splitlines()
                   if line.startswith("- ")]  # fmt: skip
        results = [*criteria, {"kind": "found", "extra": found.get("extra") or [], "feedback": found.get("feedback")
                               or ""}]  # fmt: skip
        if found.get("evidence"):
            results.append({"kind": "changes", "projects": found["evidence"]})
        if found.get("judge"):
            results.append({"kind": "judge", **found["judge"]})

        def write(conn):
            cur = conn.execute(
                "INSERT INTO goal_checks (goal_id, turn, route, verdict, results, missing, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (goal["id"], goal["turns_used"], route, verdict, json.dumps(results, ensure_ascii=False),
                 json.dumps(missing, ensure_ascii=False), detail),
            )  # fmt: skip
            return checks(conn, goal["id"], 1)[0] if cur.lastrowid else None

        check = await self._db.acall(write)
        await self._bus.publish("goal.check", {"conversation_id": goal["conversation_id"], "goal_id": goal["id"],
                                               "check": check})  # fmt: skip
        status = {"achieved": "achieved", "blocked": "blocked", "budget": "budget", "paused": "paused"}.get(route)
        if status is not None:
            await self._status(goal, status, detail, ended=status == "achieved")

    async def _take_steering(self, goal: dict[str, Any]) -> list[str]:
        def take(conn):
            rows = steering(conn, goal["id"])
            conn.executemany("UPDATE goal_steering SET taken_at = ? WHERE id = ?",
                             [(utc_now(), r["id"]) for r in rows])  # fmt: skip
            return [r["text"] for r in rows]

        return await self._db.acall(take)

    async def _next(self, goal: dict[str, Any], *, head: str) -> None:
        """The next turn after a pause, a block or a restart: from the last check, with what the user added."""
        last = await self._db.acall(lambda conn: checks(conn, goal["id"], 1))
        said = await self._take_steering(goal)
        if not goal["turns_used"]:  # it never got through a turn: the contract again
            note = start_note(goal, head=head, said=said)
        else:
            note = continue_note(goal, _found_of(last[0]) if last else {"results": []}, said, head=head)
        await self._turn(goal, note)

    async def _turn(self, goal: dict[str, Any], note: str) -> None:
        """Starts the goal's next turn; one that cannot start stops the goal for the human (why - in its detail)."""
        conversation_id = goal["conversation_id"]
        if conversation_id in self._chat.busy:  # the user is chatting: their turn hands over when it ends
            return
        try:
            self._counted.add(conversation_id)
            await self._chat.go_on(conversation_id, goal["model_id"], note, effort=goal["effort"],
                                   limits=Limits(max_steps=TURN_STEPS))  # fmt: skip
        except GatewayError as exc:
            self._counted.discard(conversation_id)
            if conversation_id in self._chat.busy:
                return
            current = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
            if current["status"] == "active":
                await self._status(current, "blocked", f"следующий ход не начался: {exc}")
