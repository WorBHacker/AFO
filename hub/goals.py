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
Stages (11.5, the user's plan 5, level 2): the contract may split the work into stages, in order. The worker ends a
turn with «STAGE DONE: …» when the stage at work is done; the hub accepts it when its own check finds nothing broken
(files parse, no test lost), and from then on that stage's turns go to the model as one line (`compact`) - the
contract's note, the first of the goal, stays where it is, and the stored conversation stays whole (D14). The user may
change the stages not done yet.
Checkpoints (11.6, hub/checkpoints.py): each git project's files at the goal's start and at each stage done, kept
by refs of the hub's own. Where the loop guard would stop the goal, it goes back to the last checkpoint instead (once
each) and the worker hears to take another way - in «Песочница» at once, in the project after a card (the files are
the user's); refused, the goal waits for the human as before.
Going round in circles (11.4, hub/guard.py): the same failure twice, a pendulum, one file changed turn after turn -
by whoever wrote it, the projects' trees at a turn's start and end say (21.1) -
the next turn investigates (writing and commands refused, the root cause asked for); after that, the human. The
contract's constraints in the forms the hub can check are refused at the call; three tries in a turn - the human.
Two models (11.7, the user's plan, idea 5) only when the contract names a strong one: it takes the first turn (the
plan), an investigation and the fix after it, and the turn after going back to a checkpoint; the other turns are
the worker's. Each note says who works the turn and why - the window shows it on the note's line (D11). Claude Code
may be the worker, alone: the hub starts its turns the same way, and its guard is a denial of its permission
requests (hub/mcp_server.py).
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
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from common.i18n import N_, tr

from . import approvals, checkpoints, commands, guard, judge, sandbox, store
from .autonomous import NOTE
from .chat import ChatService, Limits, Outcome
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database, utc_now
from .events import EventBus
from .gateway import GatewayError
from .projects import ProjectError, get_project
from .secret_filter import clean
from .store import MessageRow
from .tools import ToolError

log = logging.getLogger("hub.goals")

OPEN = ("active", "paused", "blocked", "budget")
DEFAULTS = {"max_turns": 25, "max_tokens": 500_000, "max_minutes": 60, "soft_ratio": 0.88}  # Q26
BOUNDS = {"max_turns": (1, 500), "max_tokens": (1_000, 50_000_000), "max_minutes": (1, 24 * 60)}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TURN_STEPS = 30  # tool steps within one turn of a goal (a chat's own answer has 12)
MAX_CRITERIA = 10
MAX_CONSTRAINTS = 20
MAX_MILESTONES = 8
MILESTONE_CHARS = 200
SUMMARY_CHARS = 300  # a stage's «STAGE DONE» line, kept
STAGE_DONE = re.compile(r"^\W*(?:STAGE DONE|ЭТАП ГОТОВ)\W*:\s*(?P<said>.+?)\s*$", re.M | re.I)  # i18n: keep
CHECK_TIMEOUT_S = 300  # a criterion's command, by default
TAIL_LINES = 30  # of a failed criterion's command, for the worker
TITLE_CHARS = 48
NO_JUDGE = "none"  # `judge_model_id` that says: no judge, not even the role's (the proving ground's exams, D45)
REGRESSED_AGAIN = N_("второй раз подряд потеряно то, что работало (тесты или ограничения)")
ASK_PERSON = N_(": решите, как быть, и продолжите или снимите цель")  # left out of an exam's table (D45)
HARD = ("plan", "investigate", "fix", "another_way")  # the turns a goal's strong model takes (11.7)
PLAN_LINE = ("THIS TURN IS THE STRONG MODEL'S (the contract's choice): lay out the plan and do its hardest part. A "
             "lighter model takes the next turns - write the plan down in your answer, step by step.")  # fmt: skip


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


def _project_of(attached: list, ref: Any, n: int) -> str:
    """The chat's attached project a criterion runs in: by id or by name; the only one when it says none."""
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
    criteria = check_criteria(data.get("criteria"), _attached(conn, conversation_id))
    milestones = check_milestones(data.get("milestones") or [])
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
    strong_id = str(data.get("strong_model_id") or "") or None
    strong = None
    if strong_id == model_id:
        strong_id = None  # the same model: nothing to switch to
    if strong_id:
        strong = store.get_model(conn, strong_id)
        if strong is None or not strong["enabled"]:
            raise GoalError(f"the strong model {strong_id!r} is not available now")
    _check_workers(conn, conversation_id, model, strong, criteria)
    effort = str(data.get("effort") or "medium")
    if effort not in EFFORTS:
        raise GoalError(f"effort: one of {', '.join(EFFORTS)}")
    limits = _limits(data.get("limits"), DEFAULTS)
    # the judge (11.2): the one named, else the role «Проверка цели» (read again at each check while none is named)
    named = str(data.get("judge_model_id") or "") or None
    judge_id = None if named == NO_JUDGE else named or judge.role_model(conn)
    if judge_id:  # said now, not after the first turn
        try:
            judge.check_model(conn, judge_id, _attached(conn, conversation_id))
        except judge.JudgeError as exc:
            raise GoalError(str(exc)) from None
    return {"objective": objective, "criteria": criteria, "constraints": constraints, "model_id": model_id,
            "strong_model_id": strong_id, "effort": effort, "judge_model_id": named, "milestones": milestones,
            **limits}  # fmt: skip


def _check_workers(conn: sqlite3.Connection, conversation_id: str, model: dict, strong: dict | None,
                   criteria: list[dict[str, Any]]) -> None:  # fmt: skip
    """Who may work on the goal (11.7). Claude Code alone, and not in «Песочница»: it works in the project itself
    (hub/claude_code.py). A cloud worker not on a criterion in a project only local models may see: the check's
    output goes to it in the notes (D10). With two models each sees the whole chat: a cloud one only where every
    project allows the cloud."""
    pair = [m for m in (model, strong) if m is not None]
    if strong is not None and any(m["provider"] == CLAUDE_CODE for m in pair):
        raise GoalError("Claude Code works on a goal alone: no strong model with it, and not as one")
    if model["provider"] == CLAUDE_CODE and store.context_settings(conn, conversation_id)["mode"] == "sandbox":
        raise GoalError("Claude Code works in the project itself, not in «Песочница»: "  # i18n: keep
                        "choose other rights for the chat, or another model")  # fmt: skip
    local_only = [p for p in _attached(conn, conversation_id) if not p.cloud_ok]
    names = ", ".join(f"«{p.name}»" for p in local_only)
    cloud = [m for m in pair if m["locality"] == "cloud"]
    if strong is not None and cloud and local_only:
        raise GoalError(f"«{cloud[0]['name']}» is a cloud model, and {names} only local models may see (D10): with "
                        "two models both see the whole chat - choose local ones, or allow the cloud in the "
                        "project's settings")  # fmt: skip
    if model["locality"] != "cloud":
        return
    for n, c in enumerate(criteria, 1):
        found = next((p for p in local_only if p.id == c.get("project_id")), None)
        if found is not None:
            raise GoalError(f"criterion {n}: «{found.name}» only local models may see (D10), and «{model['name']}» "
                            "is a cloud model - the check's output would go to it")  # fmt: skip


def worker(goal: dict[str, Any], hard: str) -> tuple[str, str]:
    """The model of the goal's next turn and why it is the strong one (11.7): `hard` - the kind of turn (HARD), ""
    for the worker's own; the strong model only when the contract names one."""
    if goal.get("strong_model_id") and hard:
        return goal["strong_model_id"], hard
    return goal["model_id"], ""


def check_milestones(raw: Any, done: int = 0) -> list[str]:
    """A goal's stages (11.5): a list of titles, in order - `done` of them done already count too."""
    if not isinstance(raw, list):
        raise GoalError("`milestones` is a list of stages")
    found = [" ".join(str(m).split()) for m in raw if str(m).strip()]
    if done + len(found) > MAX_MILESTONES:
        raise GoalError(f"at most {MAX_MILESTONES} stages")
    if any(len(m) > MILESTONE_CHARS for m in found):
        raise GoalError(f"a stage is at most {MILESTONE_CHARS} characters")
    return found


def check_criteria(raw: Any, attached: list) -> list[dict[str, Any]]:
    """A goal's criteria - or a coordinator's task's (16.3, D50) - checked: each a command that only looks at a
    project (`attached`: where it may run) with the exit code it must end with, or a rule in words for the judge."""
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
        if (approvals.destructive("run_command", {"command": command})
                or approvals.network("run_command", {"command": command})
                or approvals.hub_data("run_command", {"command": command})):  # fmt: skip
            raise GoalError(f"criterion {n}: «{command}» changes things, goes to the network or reads the hub's own "
                            "data - a check only looks at the project")  # fmt: skip
        expected = item.get("expected_exit_code", 0)
        timeout = item.get("timeout_s", CHECK_TIMEOUT_S)
        for name, value in (("expected_exit_code", expected), ("timeout_s", timeout)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise GoalError(f"criterion {n}: `{name}` is a whole number")
        path = item.get("path") or ""
        if not isinstance(path, str):
            raise GoalError(f"criterion {n}: `path` is a folder inside the project")
        criteria.append({"kind": "command", "project_id": _project_of(attached, item.get("project"), n),
                         "command": command, "path": path.strip(), "expected_exit_code": expected,
                         "timeout_s": max(1, min(timeout, commands.MAX_TIMEOUT_S))})  # fmt: skip
    return criteria


def create_goal(conn: sqlite3.Connection, conversation_id: str, contract: dict[str, Any], *, by: str) -> str:
    if goal_of(conn, conversation_id) is not None:
        raise GoalConflict("the chat has a goal already: clear it first")
    goal_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO goals (id, conversation_id, objective, constraints, model_id, effort, max_turns, max_tokens,"
        " max_seconds, soft_ratio, created_by, judge_model_id, strong_model_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (goal_id, conversation_id, contract["objective"], json.dumps(contract["constraints"], ensure_ascii=False),
         contract["model_id"], contract["effort"], contract["max_turns"], contract["max_tokens"],
         contract["max_minutes"] * 60, contract["soft_ratio"], by, contract.get("judge_model_id"),
         contract.get("strong_model_id")),
    )  # fmt: skip
    for n, c in enumerate(contract["criteria"], 1):
        conn.execute(
            "INSERT INTO goal_criteria (goal_id, n, kind, project_id, command, path, expected_exit_code, timeout_s,"
            " rule) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal_id, n, c["kind"], c.get("project_id"), c.get("command"), c.get("path", ""),
             c.get("expected_exit_code"), c.get("timeout_s"), c.get("rule")),
        )  # fmt: skip
    conn.executemany("INSERT INTO goal_milestones (goal_id, n, title) VALUES (?, ?, ?)",
                     [(goal_id, n, title) for n, title in enumerate(contract.get("milestones") or [], 1)])  # fmt: skip
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
    goal["milestones"] = [{k: v for k, v in dict(m).items() if k != "goal_id"} for m in conn.execute(
        "SELECT * FROM goal_milestones WHERE goal_id = ? ORDER BY n", (row["id"],))]  # fmt: skip
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
    shares = [(goal["turns_used"] / goal["max_turns"],
               tr("ходов {used} из {limit}", used=goal["turns_used"], limit=goal["max_turns"])),
              (goal["tokens_used"] / goal["max_tokens"],
               tr("токенов {used} из {limit}", used=goal["tokens_used"], limit=goal["max_tokens"])),
              (goal["seconds_used"] / goal["max_seconds"],
               tr("минут {used:.0f} из {limit}", used=goal["seconds_used"] / 60,
                  limit=goal["max_minutes"]))]  # fmt: skip
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


def stages_text(goal: dict[str, Any]) -> str:
    """The stages for the worker (11.5): done ones with their line, the one at work marked; "" with none."""
    stages = goal.get("milestones") or []
    if not stages:
        return ""
    current = next((m["n"] for m in stages if m["status"] == "pending"), None)
    lines = [f"{m['n']}. {m['title']}" + (f"  (done: {m['summary']})" if m["status"] == "done" else "  <- now"
                                          if m["n"] == current else "") for m in stages]  # fmt: skip
    said = ("When the stage marked «now» is done, end your turn with a line «STAGE DONE: what is done, and where» "
            "- the hub checks it and takes you to the next.")  # fmt: skip
    return "STAGES (in order):\n" + "\n".join(lines) + "\n" + said + "\n"


def stage_text(found: dict[str, Any], goal: dict[str, Any]) -> str:
    """What the hub made of the worker's «STAGE DONE» (11.5)."""
    stage = found.get("stage")
    if not stage:
        return ""
    if not stage["accepted"]:
        return f"STAGE DONE was not accepted: {stage['why']}. Fix that, then say it again.\n"
    title = next((m["title"] for m in goal.get("milestones") or [] if m["n"] == stage["n"]), "")
    return f"STAGE {stage['n']} ACCEPTED: {title}.\n"


def start_note(goal: dict[str, Any], *, head: str = "", said: list[str] | None = None) -> str:
    """The first turn's word: the contract and how the work goes (`head`: why it starts again - a restart)."""
    rules = "\n".join(f"- {c}" + (" (the hub refuses it)" if guard.checkable(c) else "") for c in goal["constraints"])
    words = "\n".join(said or [])
    return (
        f'{NOTE} <goal_loop type="start">\n'
        + (f"{head}\n" if head else "")
        + "You work toward a goal over as many turns as it takes (goal mode). After each of your turns the hub checks "
        "the goal itself - it runs the criteria's commands - and tells you what is missing. You cannot declare the "
        "goal met: the check does.\n"
        f"GOAL: {goal['objective']}\n"
        f"DONE WHEN (the hub checks each after every turn):\n{criteria_text(goal)}\n"
        + stages_text(goal)
        + (f"HARD CONSTRAINTS - never break them:\n{rules}\n" if rules else "")
        + f"LIMITS: {goal['max_turns']} turns, {goal['max_tokens']} tokens, {goal['max_minutes']} minutes.\n"
        + (f"THE USER ADDED:\n{words}\n" if words else "")
        + "Work with your tools now. End each turn with two short lines: what you did, what is next. No greetings and "
        "no reports to the user between turns.\n"
        "</goal_loop>"
    )


def missing_text(results: list[dict[str, Any]], extra: list[str] | None = None) -> str:
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


def check_text(found: dict[str, Any]) -> str:
    missing = missing_text(found["results"], found.get("extra") or [])
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
        + stage_text(found, goal)
        + ("THE USER CHANGED THE STAGES - work by the list below.\n" if goal.get("stages_changed") else "")
        + stages_text(goal)
        + check_text(found)
        + (f"THE USER ADDED WHILE YOU WORKED - it comes first in this step:\n{words}\n" if words else "")
        + f"{usage_line(goal)}\n"
        "Do the next step toward the goal.\n"
        "</goal_loop>"
    )


ROLLBACK = "goal_rollback"  # the card that asks to put a project's files back (11.6)
POINTS = {"start": (N_("начало цели"), "the goal's start")}  # a checkpoint's name: in the user's words, for the model


def point_words(name: str) -> tuple[str, str]:
    """A checkpoint's label: (the window's, the worker's) - «начало цели» / «этап 2»."""
    if name in POINTS:
        return tr(POINTS[name][0]), POINTS[name][1]
    n = name.removeprefix("stage-")
    return tr("этап {n}", n=n), f"stage {n} done"


def rolled_back_note(goal: dict[str, Any], label: str, why: str, files: str) -> str:
    """After going back to a checkpoint (11.6, the user's plan, idea 2): the approach was a dead end - another way."""
    return (
        f'{NOTE} <goal_loop type="rolled_back">\n'
        f"ROLLED BACK: the hub's loop guard found a dead end ({why}), so the files are back as they were at {label} "
        f"({files}). Your last approach did not work: do the work another way, from there.\n"
        f"GOAL: {goal['objective']}\n" + stages_text(goal) + f"{usage_line(goal)}\n"
        "</goal_loop>"
    )


INVESTIGATED = ("INVESTIGATION DONE: your tools are back. Make the fix you found - at the root cause, not another "
                "guess.")  # fmt: skip


def investigation_note(goal: dict[str, Any], found: dict[str, Any], why: str) -> str:
    """The investigator's turn (the user's plan, idea 3): no writing, no commands - find the root cause."""
    return (
        f'{NOTE} <goal_loop type="investigation">\n'
        f"The hub's loop guard: {why}. So for this one turn writing and commands are refused.\n"
        f"GOAL: {goal['objective']}\n"
        + check_text(found)
        + "Find the root cause: read the code around the failure, follow the calls, compare with what the criteria "
        "expect. End this turn with two things: the root cause you found, and the exact fix you will make next turn "
        "(the file and what changes). Your tools for writing and commands come back next turn.\n"
        f"{usage_line(goal)}\n"
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
        + check_text(found)
        + (f"THE USER ADDED WHILE YOU WORKED:\n{words}\n" if words else "")
        + "</goal_loop>"
    )


# ---------------------------------------------------------------- the goal's draft (the plan's «goal compiler», 7.6)

DRAFT_SYSTEM = """You turn what a user wants into a goal contract for an AI worker. The worker works on it turn after \
turn in the user's projects; after each turn the hub checks the contract's criteria itself.
Make it measurable. Prefer criteria the hub can run: one PowerShell command line in an attached project and the exit \
code that means done - the project's tests, a build, a check such as `if (Test-Path done.txt) { exit 0 } else { exit \
1 }`. Add a criterion in words only for what no command can check. A command only looks: never install, download, \
push or delete. A bigger goal may be split into 2 to 6 stages, in order (`milestones`, short titles); a small \
one needs none. Add the hard constraints the wish implies (what must not be touched). The hub refuses breaking three \
forms of them itself - write a constraint so when it fits: «не трогать: path, ...» (no writing there; a folder ends \
with /, a pattern like *.lock), «только: path, ...» (writing only there), «не запускать: command, ...» (such commands \
refused) - in English «don't touch:», «only:», «never run:». Suggest limits: turns (5 to 40) and minutes. Write the \
objective, the rules and the constraints in the user's language.
Answer with JSON only:
{"objective": "...", "criteria": [{"command": "...", "project": "<an attached project's name>", \
"expected_exit_code": 0}, {"rule": "..."}], "milestones": ["..."], "constraints": ["..."], \
"limits": {"max_turns": 20, "max_minutes": 60}}"""  # i18n: keep
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
            if (approvals.destructive("run_command", {"command": command})
                    or approvals.network("run_command", {"command": command})
                    or approvals.hub_data("run_command", {"command": command})):  # fmt: skip
                notes.append(tr("команда «{command}» убрана: проверка только смотрит", command=command))
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
    raw = data.get("milestones") if isinstance(data.get("milestones"), list) else []
    stages = [" ".join(str(m).split())[:MILESTONE_CHARS] for m in raw if str(m).strip()][:MAX_MILESTONES]
    return {"objective": " ".join(str(data["objective"]).split())[:4000], "criteria": criteria[:MAX_CRITERIA],
            "constraints": constraints[:MAX_CONSTRAINTS], "limits": out_limits, "notes": notes,
            **({"milestones": stages} if stages else {})}  # fmt: skip


def _problems(found: dict[str, Any]) -> list[str]:
    """What the hub's own check found broken - files that do not parse, tests lost - not the judge's words."""
    said_by_judge = set((found.get("judge") or {}).get("missing") or [])
    return [x for x in found.get("extra") or [] if x not in said_by_judge]


def _hub_note(row: MessageRow) -> bool:
    return row.role == "user" and any(b.get("hub") for b in row.content)


PLAN_CHARS = 6000  # the first turn's words kept through the fold (19.5)


def compacted(history: list[MessageRow], done: list[dict[str, Any]], start: int | None,
              keep_plan: bool = False) -> list[MessageRow]:  # fmt: skip
    """The history with each stage `done` as one line (11.5): from after the notes that opened it (the first of them
    the contract) to its last message, the stage's turns are one message of the worker - «[STAGE n DONE - turns a-b]
    title: its line»; the context layers stay. A stage the user wrote in is left as it is. `keep_plan` (the contract
    names a strong model, 11.7): what its first turn said - the plan it wrote for the lighter model - stays in the
    first stage's line, as the contract stays (19.5)."""
    lo, first_turn = (start or 0) - 1, 1
    plan = _first_words(history, lo) if keep_plan else ""
    for stage in sorted(done, key=lambda m: m["n"]):
        hi = stage["closed_seq"]
        turns = f"turn {stage['turn']}" if stage["turn"] == first_turn else f"turns {first_turn}-{stage['turn']}"
        line = f"[STAGE {stage['n']} DONE - {turns}] {stage['title']}: {stage['summary']}"
        if first_turn == 1 and plan:
            line += f"\nWHAT THE GOAL'S FIRST TURN SAID (the plan, kept):\n{plan}"
        history = _fold(history, lo, hi, line)
        lo, first_turn = hi, (stage["turn"] or first_turn) + 1
    return history


def _first_words(history: list[MessageRow], lo: int) -> str:
    """The text of the goal's first turn: its answers after the notes that opened it, to the next note."""
    inside = [row for row in history if row.seq > lo]
    k = 0
    while k < len(inside) and _hub_note(inside[k]):
        k += 1
    said = []
    for row in inside[k:]:
        if row.role == "user":
            break  # the next turn's note, or the user's own words
        if row.role == "assistant":
            said += [b["text"].strip() for b in row.content if b.get("type") == "text" and b.get("text", "").strip()]
    text = "\n".join(said)
    return text if len(text) <= PLAN_CHARS else text[: PLAN_CHARS - 1] + "…"


def _fold(history: list[MessageRow], lo: int, hi: int, line: str) -> list[MessageRow]:
    inside = [i for i, row in enumerate(history) if lo < row.seq <= hi]
    k = 0
    while k < len(inside) and _hub_note(history[inside[k]]):
        k += 1  # the notes that opened the stage stay
    gone = []
    for i in inside[k:]:
        row = history[i]
        if row.role == "system":
            continue  # the context layers stay
        if row.role == "user" and not _hub_note(row):
            return history  # the user's own words are never folded away
        gone.append(i)
    if not gone:
        return history
    first = history[gone[0]]
    one = MessageRow(0, first.seq, "assistant", None, [{"type": "text", "text": line}], first.created_at)
    drop = set(gone)
    return [kept for i, row in enumerate(history) for kept in ([one] if i == gone[0] else []) + (
        [row] if i not in drop else [])]  # fmt: skip


def limit_words(resets: float) -> str:
    """Why a goal of Claude Code waits (20.3): its subscription's limit, and until when (`resets` - 0: not said)."""
    if not resets:
        return tr("лимит подписки Claude Code исчерпан — продолжите цель, когда он сбросится")
    at = datetime.fromtimestamp(resets)
    when = at.strftime("%H:%M") if at.date() == datetime.now().date() else at.strftime("%d.%m %H:%M")
    return tr("лимит подписки Claude Code до {when} — потом продолжите цель", when=when)


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")  # as the database keeps times


def waiting(asked: list[tuple[str, str | None]], since: datetime, until: datetime) -> float:
    """The seconds of a turn (`since` to `until`) its cards waited for the human (19.7): each from asked to answered
    (one still open - to the turn's end), the time two of them waited together counted once."""
    spans = []
    for created, decided in asked:
        start = max(datetime.fromisoformat(created), since)
        end = min(datetime.fromisoformat(decided) if decided else until, until)
        if end > start:
            spans.append((start, end))
    total, reached = 0.0, since
    for start, end in sorted(spans):
        if end > reached:
            total += (end - max(start, reached)).total_seconds()
            reached = end
    return total


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
        chat.compact = self.compact  # a stage done goes to the model as one line (11.5)
        self._offers: dict[str, asyncio.Task] = {}  # a card asking to go back to a checkpoint, by goal (11.6)
        self._locks: dict[str, asyncio.Lock] = {}  # one check at a time per chat
        self._counted: set[str] = set()  # chats whose running turn is the goal's: it counts even if paused meanwhile
        self._began: dict[str, dict[str, str]] = {}  # a goal's turn running: its projects' trees at its start (21.1)

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
        await self._turn(goal, start_note(goal), hard="plan")
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
        role = tr("Проверка цели")  # the role's name as the window shows it
        if model is None or not model["enabled"]:
            raise GoalError(f"choose the chat's model first: it drafts the goal (or the model of «{role}»)")
        if model["provider"] == CLAUDE_CODE:
            raise GoalError(f"Claude Code does not draft goals: choose another model, or one for «{role}»")
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
        kept = ("objective", "criteria", "constraints", "limits", "milestones")
        return {"draft": {k: found[k] for k in kept if k in found}, "notes": found["notes"], "model_id": chosen,
                "cost_usd": found["cost_usd"]}  # fmt: skip

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
            await self._keep(goal, project, "start", tree)  # the first checkpoint (11.6)

    async def _sandboxed(self, goal: dict[str, Any]) -> bool:
        mode = (await self._db.acall(lambda conn: store.context_settings(conn, goal["conversation_id"])))["mode"]
        return mode == "sandbox"

    async def _keep(self, goal: dict[str, Any], project, name: str, tree: str | None = None) -> None:
        """A checkpoint of one git project's files (11.6): its commit and ref, and its row. In «Песочница» the commit
        remembers the sandbox's base (19.3)."""
        try:
            base = None
            if await self._sandboxed(goal):
                base = await asyncio.to_thread(sandbox.base_of, project.root, goal["conversation_id"])
            saved = await asyncio.to_thread(checkpoints.save, project.root, goal["id"], name, tree, base)
        except (judge.JudgeError, sandbox.SandboxError) as exc:
            log.warning("goal %s: no checkpoint %s of %s: %s", goal["id"], name, project.name, exc)
            return
        await self._db.acall(lambda conn: conn.execute(
            "INSERT OR REPLACE INTO goal_checkpoints (goal_id, name, project_id, commit_id, tree, turn)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (goal["id"], name, project.id, saved["commit"], saved["tree"], goal["turns_used"])))  # fmt: skip

    async def _checkpoint(self, goal: dict[str, Any], name: str) -> None:
        for project in await self._chat.tool_projects(goal["conversation_id"]):
            if judge.is_git(project.root):
                await self._keep(goal, project, name)

    async def pause(self, conversation_id: str, *, by: str) -> dict[str, Any]:
        """No next turn starts; a turn running now finishes (and counts)."""
        goal = await self._open(conversation_id)
        if goal["status"] != "active":
            raise GoalConflict(f"the goal is {goal['status']}, not at work")
        return await self._status(goal, "paused", tr("на паузе ({by})", by=by))

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
        if (offer := self._offers.pop(goal["id"], None)) is not None:
            offer.cancel()  # resumed as it is: the card to go back closes unanswered
        goal = await self._status(goal, "active", "")
        if conversation_id not in self._chat.busy:  # else the turn that runs now hands over when it ends
            await self._next(goal, head=f"The user resumed the goal ({by}).")
        return await self._db.acall(lambda conn: get_goal(conn, goal["id"]))

    async def clear(self, conversation_id: str, *, by: str) -> dict[str, Any]:
        """The goal is given up: no next turn (a turn running now finishes)."""
        goal = await self._open(conversation_id)
        if (offer := self._offers.pop(goal["id"], None)) is not None:
            offer.cancel()
        return await self._status(goal, "cleared", tr("снята ({by})", by=by), ended=True)

    async def steer(self, conversation_id: str, text: str, *, by: str) -> dict[str, Any]:
        """The user's word while the goal works: the worker reads it at the start of its next turn."""
        text = text.strip()[:4000]
        if not text:
            raise GoalError(tr("Пустое сообщение"))
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

    async def _status(self, goal: dict[str, Any], status: str, detail: str, *, ended: bool = False,
                      subscription: float | None = None) -> dict:  # fmt: skip
        """The goal's status, kept and told; `subscription`: it waits for Claude Code's limit to reset (20.3) - when
        (0: not said), for the window's and Telegram's notice."""
        now = utc_now()
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE goals SET status = ?, detail = ?, updated_at = ?, ended_at = ? WHERE id = ?",
            (status, detail, now, now if ended else None, goal["id"])))  # fmt: skip
        changed = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
        # a run of the proving ground (D45) is not the person's goal: its end is told by the exam, not by a notice
        await self._bus.publish("goal.status", {"conversation_id": goal["conversation_id"], "goal_id": goal["id"],
                                                "status": status, "detail": detail, "goal": changed,
                                                "proving": goal["conversation_id"] in self._chat.proving,
                                                "subscription": subscription})  # fmt: skip
        return changed

    # ---------- after a turn ----------

    async def turn_ended(self, conversation_id: str, outcome: Outcome) -> None:
        """A turn of the chat ended (hub/chat.py): what the goal used, the check, and the way on."""
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            goal = await self._db.acall(lambda conn: goal_of(conn, conversation_id))
            mine = conversation_id in self._counted
            self._counted.discard(conversation_id)
            turn = self._chat.guards.pop(conversation_id, None) if mine else None  # its tries at the constraints
            began = self._began.pop(conversation_id, None) if mine else None  # its projects' trees then (21.1)
            if goal is None and mine:  # cleared while its turn ran: the turn still counts
                goal = await self._db.acall(lambda conn: latest_goal(conn, conversation_id))
            if goal is None or not (goal["status"] == "active" or mine):
                return  # no goal, or the user chatted while it waited: not the goal's turn
            goal = await self._count(goal, outcome)
            if goal["status"] != "active":
                return
            if outcome.status == "cancelled":
                await self._checked(goal, "paused", None, {}, tr("остановлено: ход прерван"))
                return
            if outcome.status == "error" and outcome.subscription is not None:  # the subscription's limit (20.3)
                await self._checked(goal, "paused", None, {}, limit_words(outcome.subscription),
                                    subscription=outcome.subscription)  # fmt: skip
                return
            if outcome.status == "error" and not outcome.limited:
                await self._checked(goal, "blocked", None, {}, tr("ход не удался: {error}", error=outcome.detail))
                return
            if goal["investigating"]:  # the investigator's turn (11.4): nothing could change, nothing is run
                await self._db.acall(lambda conn: conn.execute("UPDATE goals SET investigating = 0 WHERE id = ?",
                                                               (goal["id"],)))  # fmt: skip
                last = await self._db.acall(lambda conn: checks(conn, goal["id"], 1))
                found = _found_of(last[0]) if last else {"results": [], "verdict": None}
                await self._go_on({**goal, "investigating": 0}, None, found, head=INVESTIGATED, route="investigated",
                                  detail=tr("причину искали — дальше правка"), kept={})  # fmt: skip
                return
            if turn is not None and len(turn.refused) >= guard.TRIES:
                tried = "; ".join(dict.fromkeys(r.split("«", 1)[-1].split("»", 1)[0] for r in turn.refused))
                said = tr("{n} попытки нарушить ограничения за ход: {rules}", n=len(turn.refused), rules=tried)
                await self._checked(goal, "blocked", None, {}, said + tr(ASK_PERSON))
                return
            await self._bus.publish("goal.checking", {"conversation_id": conversation_id, "goal_id": goal["id"]},
                                    persist=False)  # fmt: skip
            changed = await self._changed(conversation_id, began or {})  # before the checks' commands run
            results = await self.run_checks(goal)
            found = await self.contour(goal, results)
            found["turn_files"] = changed
            if (stage := await self._stage(goal, found)) is not None:  # «STAGE DONE» (11.5)
                found["stage"] = stage
            goal = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))  # the judge's use counted
            verdict = found["verdict"]
            if verdict == "ACHIEVED":
                await self._checked(goal, "achieved", "ACHIEVED", found, found["detail"])
                return
            if found.get("blocked"):
                await self._checked(goal, "blocked", verdict, found, found["blocked"])
                return
            if verdict == "REGRESSED" and await self._regressed_before(goal):
                await self._checked(goal, "blocked", verdict, found, tr(REGRESSED_AGAIN) + tr(ASK_PERSON))
                return
            # going round in circles (11.4): the judge's words differ each time - not part of the fingerprint
            extra = _problems(found)
            mark = (guard.fingerprint(found["results"], extra), guard.failing(found["results"], extra))
            history = await self._history(goal)
            edited, edits = guard.thrashing(history, mark[1], found["turn_files"])
            accepted = (found.get("stage") or {}).get("accepted")  # a stage done is progress (19.1)
            loop = None if accepted else guard.signal(history, mark[0], mark[1], edits, edited)
            if loop is not None and loop[0] == "blocked":
                if await self._back(goal, verdict, found, mark, loop[1]):  # a dead end: a checkpoint (11.6)
                    return
                await self._checked(goal, "blocked", verdict, found, loop[1] + tr(ASK_PERSON), mark)
                return
            await self._go_on(goal, verdict, found, mark=mark, investigate=loop[1] if loop else "")

    async def _stage(self, goal: dict[str, Any], found: dict[str, Any]) -> dict[str, Any] | None:
        """The worker said «STAGE DONE» this turn (11.5): the stage at work is done when the hub's own check found
        nothing broken - kept with the worker's line and where its turns end. {kind: stage, n, accepted, why}."""
        current = next((m for m in goal["milestones"] if m["status"] == "pending"), None)
        if current is None:
            return None

        def said(conn):
            rows = conn.execute("SELECT seq, role, content FROM messages WHERE conversation_id = ? ORDER BY seq DESC"
                                " LIMIT 400", (goal["conversation_id"],)).fetchall()  # fmt: skip
            for row in rows:  # the newest first: this turn's last word, back to the note that opened the turn
                content = json.loads(row["content"])
                if row["role"] == "user" and any(b.get("hub") for b in content):
                    return None  # the turn ended with no word of its own: an older line is not this turn's
                if row["role"] == "assistant":
                    text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
                    claims = list(STAGE_DONE.finditer(text))
                    return (claims[-1]["said"], rows[0]["seq"]) if claims else None
            return None

        claim = await self._db.acall(said)
        if claim is None:
            return None
        problems = _problems(found)
        if found.get("verdict") == "REGRESSED" or problems:
            why = problems[0] if problems else "something that worked was lost (tests or constraints)"
            return {"kind": "stage", "n": current["n"], "accepted": False, "why": why}
        summary = clean(" ".join(claim[0].split()))[:SUMMARY_CHARS]
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE goal_milestones SET status = 'done', summary = ?, turn = ?, closed_seq = ?, done_at = ?"
            " WHERE goal_id = ? AND n = ?",
            (summary, goal["turns_used"], claim[1], utc_now(), goal["id"], current["n"])))  # fmt: skip
        await self._checkpoint(goal, f"stage-{current['n']}")  # where it stands: a checkpoint (11.6)
        return {"kind": "stage", "n": current["n"], "accepted": True, "why": ""}

    async def _point(self, goal: dict[str, Any]) -> tuple[str, list[tuple[Any, str, dict]]] | None:
        """The last checkpoint not gone back to yet, and what going back would do in each of its projects - None when
        there is none, or nothing would change."""
        rows = await self._db.acall(lambda conn: conn.execute(
            "SELECT name, project_id, commit_id, tree FROM goal_checkpoints WHERE goal_id = ? AND rolled_back = 0"
            " ORDER BY rowid DESC", (goal["id"],)).fetchall())  # fmt: skip
        if not rows:
            return None
        name = rows[0]["name"]
        roots = {p.id: p for p in await self._chat.tool_projects(goal["conversation_id"])}
        sandboxed = await self._sandboxed(goal)
        found = []
        for row in rows:
            if row["name"] == name and row["project_id"] in roots:
                project = roots[row["project_id"]]
                try:
                    tree = row["tree"]
                    if sandboxed:  # what the user applied since stays (19.3)
                        base = await asyncio.to_thread(sandbox.base_of, project.root, goal["conversation_id"])
                        tree = await asyncio.to_thread(checkpoints.keeping_applied, project.root, row["commit_id"],
                                                       base)  # fmt: skip
                    planned = await asyncio.to_thread(checkpoints.plan, project.root, tree)
                except (judge.JudgeError, sandbox.SandboxError) as exc:
                    log.warning("goal %s: no way back to %s in %s: %s", goal["id"], name, project.name, exc)
                    continue
                if planned["restore"] or planned["delete"]:
                    found.append((project, tree, planned))
        return (name, found) if found else None

    @staticmethod
    def _files(found: list[tuple[Any, str, dict]]) -> str:
        """What going back does, in a line: «proj: a.py, b.py - back; c.txt - removed»."""
        parts = []
        for project, _tree, planned in found:
            said = []
            if planned["restore"]:
                said.append(", ".join(planned["restore"][:10]) + (" …" if len(planned["restore"]) > 10 else "")
                            + " — " + tr("как были"))  # fmt: skip
            if planned["delete"]:
                said.append(", ".join(planned["delete"][:10]) + (" …" if len(planned["delete"]) > 10 else "")
                            + " — " + tr("удалить"))  # fmt: skip
            parts.append(f"{project.name}: " + "; ".join(said))
        return "\n".join(parts)

    async def _back(self, goal: dict[str, Any], verdict: str | None, found: dict[str, Any],
                    mark: tuple[str | None, int], why: str) -> bool:  # fmt: skip
        """A dead end (11.6): back to the last checkpoint - in «Песочница» at once, in the project after a card (the
        goal waits meanwhile). False when there is none to go back to: the goal waits for the human as before."""
        point = await self._point(goal)
        if point is None:
            return False
        name, plans = point
        label = point_words(name)[0]
        if await self._sandboxed(goal):  # the sandbox's own files: nobody's work is lost
            await self._go_back(goal, name, plans, why, verdict=verdict, found=found, mark=mark)
            return True
        offer = tr(" — или откатить файлы к точке «{point}»: ответ — в карточке подтверждения", point=label)
        await self._checked(goal, "blocked", verdict, found, why + offer, mark)
        paths = [str(project.root / p) for project, _tree, planned in plans
                 for p in planned["restore"] + planned["delete"]]  # fmt: skip
        data = {"checkpoint": label, "goal": goal["objective"], "files": self._files(plans), "why": why,
                "paths": paths}  # the editor's unsaved files among them: the card says so (19.6)  # fmt: skip

        async def ask() -> None:
            decision = await self._chat.approvals.ask(ROLLBACK, data, conversation_id=goal["conversation_id"],
                                                      cover=False)  # fmt: skip
            current = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
            if decision.allow and current["status"] == "blocked":
                await self._status(current, "active", "")
                await self._go_back(current, name, plans, why)

        if self._chat.approvals is not None:
            self._offers[goal["id"]] = asyncio.create_task(ask())
            self._offers[goal["id"]].add_done_callback(lambda t, g=goal["id"]: self._offers.pop(g, None))
        return True

    async def _go_back(self, goal: dict[str, Any], name: str, plans: list[tuple[Any, str, dict]], why: str, *,
                       verdict: str | None = None, found: dict[str, Any] | None = None,
                       mark: tuple[str | None, int] = (None, 0)) -> None:  # fmt: skip
        """The files back at checkpoint `name` (once), the check kept, and the next turn told to go another way."""
        done = []
        for project, tree, _planned in plans:
            try:
                done.append((project, tree, await asyncio.to_thread(checkpoints.restore, project.root, tree)))
            except (judge.JudgeError, OSError) as exc:
                log.warning("goal %s: going back to %s in %s failed: %s", goal["id"], name, project.name, exc)
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE goal_checkpoints SET rolled_back = 1 WHERE goal_id = ? AND name = ?",
            (goal["id"], name)))  # fmt: skip
        label, said = point_words(name)
        files = self._files(done)
        detail = tr("файлы возвращены к точке «{point}»: {files}", point=label, files=files)
        await self._checked(goal, "rolled_back", verdict, found or {}, detail, mark)
        current = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
        await self._turn(current, rolled_back_note(current, said, why, files.replace("\n", "; ")), hard="another_way")

    async def set_milestones(self, conversation_id: str, titles: list[str], *, by: str) -> dict[str, Any]:
        """The user's stages in place of those not done yet (11.5); the next note tells the worker."""
        goal = await self._open(conversation_id)
        done = [m for m in goal["milestones"] if m["status"] == "done"]
        found = check_milestones(titles, len(done))

        def write(conn):
            conn.execute("DELETE FROM goal_milestones WHERE goal_id = ? AND status = 'pending'", (goal["id"],))
            conn.executemany("INSERT INTO goal_milestones (goal_id, n, title) VALUES (?, ?, ?)",
                             [(goal["id"], len(done) + n, t) for n, t in enumerate(found, 1)])  # fmt: skip
            conn.execute("UPDATE goals SET stages_changed = 1, updated_at = ? WHERE id = ?", (utc_now(), goal["id"]))
            return get_goal(conn, goal["id"])

        changed = await self._db.acall(write)
        await self._bus.publish("goal.status", {"conversation_id": conversation_id, "goal_id": goal["id"],
                                                "status": changed["status"], "detail": changed["detail"],
                                                "goal": changed, "proving": False, "by": by})  # fmt: skip
        return changed

    async def compact(self, conversation_id: str, history: list[MessageRow]) -> list[MessageRow]:
        """The request's history with each stage done as one line (11.5) - for the open goal of the chat."""

        def read(conn):
            goal = goal_of(conn, conversation_id)
            done = [m for m in (goal or {}).get("milestones") or [] if m["status"] == "done" and m["closed_seq"]]
            if not done:
                return None
            start = conn.execute("SELECT min(seq) FROM messages WHERE conversation_id = ? AND created_at >= ?",
                                 (conversation_id, goal["created_at"])).fetchone()[0]  # fmt: skip
            return done, start, bool(goal["strong_model_id"])

        found = await self._db.acall(read)
        return compacted(history, *found) if found else history

    async def _go_on(self, goal: dict[str, Any], verdict: str | None, found: dict[str, Any], *,
                     mark: tuple[str | None, int] = (None, 0), investigate: str = "", head: str = "",
                     route: str = "continue", detail: str = "",
                     kept: dict[str, Any] | None = None) -> None:  # fmt: skip
        """The way on when the goal is not met and nothing stops it: parked at the limit, the last turn, an
        investigation (11.4: `investigate` - why), or the next turn - its check kept as `route` with `detail` (after
        an investigation: «investigated», what it keeps - `kept` - empty: nothing was checked)."""
        share = used(goal)
        if goal["last_turn"] or share >= 1.0:
            await self._checked(goal, "budget", verdict, found, tr("предел: {limit}", limit=_limit_words(goal)), mark)
            return
        said = await self._take_steering(goal)
        if share >= goal["soft_ratio"]:
            await self._db.acall(lambda conn: conn.execute("UPDATE goals SET last_turn = 1 WHERE id = ?",
                                                           (goal["id"],)))  # fmt: skip
            await self._checked(goal, "last_turn", verdict, found,
                                tr("последний ход: {limit}", limit=_limit_words(goal)), mark)  # fmt: skip
            await self._turn(goal, last_turn_note(goal, found, said))
            return
        if goal.get("stages_changed"):  # the note says so this once
            await self._db.acall(lambda conn: conn.execute("UPDATE goals SET stages_changed = 0 WHERE id = ?",
                                                           (goal["id"],)))  # fmt: skip
        if investigate:
            await self._db.acall(lambda conn: conn.execute("UPDATE goals SET investigating = 1 WHERE id = ?",
                                                           (goal["id"],)))  # fmt: skip
            await self._checked(goal, "investigate", verdict, found, investigate, mark)
            if said:  # what the user wrote waits for the turn after the investigation
                await self._db.acall(lambda conn: conn.executemany(
                    "INSERT INTO goal_steering (goal_id, text, by) VALUES (?, ?, 'hub')",
                    [(goal["id"], text) for text in said]))  # fmt: skip
            await self._turn({**goal, "investigating": 1}, investigation_note(goal, found, investigate),
                             hard="investigate")  # fmt: skip
            return
        await self._checked(goal, route, verdict, found if kept is None else kept, detail, mark)
        hard = "fix" if route == "investigated" else ""  # what the investigation found: the strong model fixes it
        await self._turn(goal, continue_note(goal, found, said, head=head), hard=hard)

    async def _history(self, goal: dict[str, Any]) -> list[dict[str, Any]]:
        """The checks since the goal last stopped (a block, a limit, a pause: resumed, it starts afresh) or closed a
        stage (19.1), oldest first - what the loop guard reads (11.4)."""
        found = await self._db.acall(lambda conn: checks(conn, goal["id"], 50))
        cuts = [i for i, c in enumerate(found) if guard.afresh(c)]
        return found[cuts[-1] + 1 :] if cuts else found

    async def _trees(self, conversation_id: str) -> dict[str, str]:
        """Each git project's files as a tree now (in «Песочница» - the sandbox's), by project id."""
        trees = {}
        for project in await self._chat.tool_projects(conversation_id):
            if judge.is_git(project.root):
                try:
                    trees[project.id] = await asyncio.to_thread(judge.snapshot, project.root)
                except judge.JudgeError as exc:
                    log.warning("goal of %s: no tree of %s: %s", conversation_id, project.name, exc)
        return trees

    async def _changed(self, conversation_id: str, began: dict[str, str]) -> list[str]:
        """What the turn changed (21.1): each project's tree at its start against now - whoever wrote it, the hub's
        tools, Claude Code or a command. With several projects a file is «<project>/<path>»."""
        projects = await self._chat.tool_projects(conversation_id)
        now = await self._trees(conversation_id) if began else {}
        out = []
        for project in projects:
            if project.id in began and project.id in now:
                try:
                    found = await asyncio.to_thread(judge.tree_changes, project.root, began[project.id],
                                                    now[project.id])  # fmt: skip
                except judge.JudgeError as exc:
                    log.warning("goal of %s: what %s changed is unknown: %s", conversation_id, project.name, exc)
                    continue
                out += [c["path"] if len(projects) == 1 else f"{project.name}/{c['path']}" for c in found]
        return out

    async def contour(self, goal: dict[str, Any], results: list[dict[str, Any]],
                      bases: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
        """The check after the commands (Q26): what changed since the goal began, the hub's own look at it (syntax,
        tests lost), then - when that is clean - the judge. {verdict, results, extra, feedback, evidence, judge?,
        detail? (achieved), blocked? (why the human is needed)}. `bases`: where each git project's files stood at the
        start, when the caller keeps them (a coordinator's task, 16.3) - else the goal's own, from the database."""
        conversation_id = goal["conversation_id"]
        projects = await self._chat.tool_projects(conversation_id)
        if bases is None:
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
            if judge_id == NO_JUDGE:  # the commands alone decide (an exam without a judge, D45)
                judge_id = None
            if judge_id:
                judge.check_model(conn, judge_id, projects)
            return judge_id, judge.last_words(conn, conversation_id)

        try:
            judge_id, words = await self._db.acall(choose)
        except judge.JudgeError as exc:
            said = tr("проверяющая модель не может проверить: {error}", error=exc)
            return {**found, "verdict": None, "blocked": said}
        if not judge_id:
            if semantic:
                nobody = tr("команды прошли, а критерии словами проверить некому: выберите модель для роли «Проверка "
                            "цели» и продолжите — или проверьте сами и снимите цель")  # fmt: skip
                return {**found, "verdict": None, "blocked": nobody}
            return {**found, "verdict": "ACHIEVED", "detail": tr("цель достигнута: все критерии выполнены")}
        await self._bus.publish("goal.judging", {"conversation_id": conversation_id, "goal_id": goal["id"],
                                                 "model_id": judge_id}, persist=False)  # fmt: skip
        try:
            said = await self._judge.ask(judge_id, judge.goal_prompt(goal, results, evidence, words),
                                         conversation_id=conversation_id, meta={"goal_id": goal["id"]})  # fmt: skip
        except judge.JudgeError as exc:
            return {**found, "verdict": None, "blocked": tr("проверяющая модель не ответила: {error}", error=exc)}
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
            found["detail"] = tr("цель достигнута: проверки прошли, проверяющая модель согласна")
        return found

    async def _regressed_before(self, goal: dict[str, Any]) -> bool:
        last = await self._db.acall(lambda conn: checks(conn, goal["id"], 1))
        return bool(last) and last[0]["verdict"] == "REGRESSED"

    async def _count(self, goal: dict[str, Any], outcome: Outcome) -> dict[str, Any]:
        """What the turn used: a turn, its tokens (not those read from the cache), its money - and its time, less
        what it waited for the human's answer to a card (19.7: the limit is the model's work, not the user's
        absence)."""
        totals = outcome.totals
        usage = totals.usage if totals is not None else {}  # input_tokens is the whole prompt, the cache inside it
        tokens = max(int(usage.get("input_tokens") or 0) - int(usage.get("cached_input_tokens") or 0), 0) + int(
            usage.get("output_tokens") or 0
        )
        until = datetime.now(UTC)
        since = until - timedelta(seconds=outcome.seconds)

        def write(conn):
            asked = conn.execute("SELECT created_at, decided_at FROM approvals WHERE conversation_id = ? AND"
                                 " created_at >= ?", (goal["conversation_id"], _iso(since))).fetchall()  # fmt: skip
            seconds = max(outcome.seconds - waiting([tuple(r) for r in asked], since, until), 0.0)
            conn.execute(
                "UPDATE goals SET turns_used = turns_used + ?, tokens_used = tokens_used + ?, seconds_used ="
                " seconds_used + ?, cost_usd = cost_usd + ?, updated_at = ? WHERE id = ?",
                (1 if totals is not None else 0, tokens, round(seconds, 3),
                 totals.cost if totals is not None else 0.0, utc_now(), goal["id"]),
            )  # fmt: skip
            return get_goal(conn, goal["id"])

        return await self._db.acall(write)

    async def run_checks(self, goal: dict[str, Any]) -> list[dict[str, Any]]:
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
                                              timeout=c["timeout_s"], by="goal",
                                              title=tr("Цель: {command}", command=short))  # fmt: skip
            except Exception as exc:  # a folder gone, a terminal that would not start
                log.warning("goal %s: criterion %s did not run: %s", goal["id"], c["n"], exc)
                results.append({**result, "why": f"did not run: {exc}"})
                continue
            tail = clean("\n".join(done["lines"][-TAIL_LINES:]))
            if done["status"] == "asked":  # stopped already (hub/commands.py): nobody answers a check
                results.append({**result, "why": f"stopped: it waited for an answer ({done['question']})",
                                "tail": tail, "terminal": done["terminal"]})  # fmt: skip
                continue
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
                       detail: str, mark: tuple[str | None, int] = (None, 0), *,
                       subscription: float | None = None) -> None:  # fmt: skip
        """The check is appended (D14), and the goal takes the way it found. `found` (from contour): the criteria's
        results, what else is missing, the judge's word, what changed - kept with the check. `subscription`: it
        waits for Claude Code's limit (20.3)."""
        criteria = found.get("results") or []
        missing = [line[2:] for line in missing_text(criteria, found.get("extra")).splitlines()
                   if line.startswith("- ")]  # fmt: skip
        results = [*criteria, {"kind": "found", "extra": found.get("extra") or [], "feedback": found.get("feedback")
                               or ""}]  # fmt: skip
        if found.get("evidence"):
            results.append({"kind": "changes", "projects": found["evidence"]})
        if found.get("judge"):
            results.append({"kind": "judge", **found["judge"]})
        if found.get("turn_files") is not None:  # what the turn changed: the third signal reads it later (21.1)
            results.append({"kind": "turn", "files": found["turn_files"]})
        if found.get("stage"):
            results.append(found["stage"])

        def write(conn):
            cur = conn.execute(
                "INSERT INTO goal_checks (goal_id, turn, route, verdict, results, missing, detail, fingerprint,"
                " failing) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (goal["id"], goal["turns_used"], route, verdict, json.dumps(results, ensure_ascii=False),
                 json.dumps(missing, ensure_ascii=False), detail, *mark),
            )  # fmt: skip
            return checks(conn, goal["id"], 1)[0] if cur.lastrowid else None

        check = await self._db.acall(write)
        await self._bus.publish("goal.check", {"conversation_id": goal["conversation_id"], "goal_id": goal["id"],
                                               "check": check})  # fmt: skip
        status = {"achieved": "achieved", "blocked": "blocked", "budget": "budget", "paused": "paused"}.get(route)
        if status is not None:
            await self._status(goal, status, detail, ended=status == "achieved", subscription=subscription)

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
            note, hard = start_note(goal, head=head, said=said), "plan"
        elif goal["investigating"] and last:  # it was to investigate (11.4): it still is
            note, hard = investigation_note(goal, _found_of(last[0]), last[0]["detail"]), "investigate"
        else:  # the fix after an investigation, another way after going back: still the strong model's (11.7)
            note = continue_note(goal, _found_of(last[0]) if last else {"results": []}, said, head=head)
            hard = {"investigated": "fix", "rolled_back": "another_way"}.get(last[0]["route"] if last else "", "")
        await self._turn(goal, note, hard=hard)

    async def _turn(self, goal: dict[str, Any], note: str, *, hard: str = "") -> None:
        """Starts the goal's next turn - the strong model's when it is a `hard` one and the contract names one
        (11.7); one that cannot start stops the goal for the human (why - in its detail)."""
        conversation_id = goal["conversation_id"]
        if conversation_id in self._chat.busy:  # the user is chatting: their turn hands over when it ends
            return
        model_id, why = worker(goal, hard)
        if why == "plan":
            end = note.rfind("</goal_loop>")
            note = f"{note[:end]}{PLAN_LINE}\n{note[end:]}"
        mark = {"model_id": model_id, "why": why} if goal.get("strong_model_id") else None  # seen in the feed (D11)
        self._began[conversation_id] = await self._trees(conversation_id)  # what the turn changes: from here (21.1)
        try:
            self._counted.add(conversation_id)
            # what the chat asks at each tool call of this turn (11.4): the constraints, whether it investigates
            self._chat.guards[conversation_id] = guard.TurnGuard(guard.rules(goal["constraints"]),
                                                                 bool(goal["investigating"]))  # fmt: skip
            await self._chat.go_on(conversation_id, model_id, note, effort=goal["effort"],
                                   limits=Limits(max_steps=TURN_STEPS), mark=mark)  # fmt: skip
        except GatewayError as exc:
            self._counted.discard(conversation_id)
            self._chat.guards.pop(conversation_id, None)
            self._began.pop(conversation_id, None)
            if conversation_id in self._chat.busy:
                return
            current = await self._db.acall(lambda conn: get_goal(conn, goal["id"]))
            if current["status"] == "active":
                await self._status(current, "blocked", tr("следующий ход не начался: {error}", error=exc))
