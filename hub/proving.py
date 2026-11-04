"""The proving ground and the models' exam (stage 12, D45; the user's word 2026-10-30: «песочница для примеров и для
задач и жёсткие экзамены между большими [моделями]»).

A task lives in the program (hub/proving_tasks/<task>/): `task.json` - its title, level, what it is about, and a goal
contract (D44): the objective, criteria (commands and their exit codes), constraints, limits; `files/` - a small
project to work on; `exam/` - hidden checks the model never sees (they stay in the program, not in its copy). In the
contract `{python}` is this program's own Python (the user's computer may have none), `{exam}` the hidden checks'
folder.

A run is one task for one model: a fresh copy of the task's files in data/proving/<run> (a git repository with a first
commit, so the judge sees a diff), a project and a chat of their own - hidden from the lists of projects and chats and
from other chats' map of projects (D27) -, the rights «Доверенный», and the goal. On the proving ground nobody answers
(hub/chat.py): what would be asked - something destructive, the network, a question to the user - is refused at once.
When the goal ends (met, waiting, at its limit, cleared) or its hard time is up, the hub runs the hidden checks in the
copy: passed = the goal met and every hidden check passed.

«Попробовать» is a run of its own: the user watches its chat. An exam is runs of chosen tasks on chosen models, one
after another (local models share one GPU anyway), each on its own copy; its table says, per task and model, passed
or not and why, the turns, tokens, minutes and money. Exams are kept (D14) until the user deletes one - with its
copies, chats and projects. A run going when the hub stops does not go on: it is marked stopped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from common import i18n
from common.i18n import tr

from . import commands, projects, store
from .db import Database, utc_now
from .events import EventBus
from .goals import ASK_PERSON, GoalError, Goals
from .projects import ProjectError, add_project, get_project, remove_project
from .secret_filter import clean

log = logging.getLogger("hub.proving")

TASKS_DIR = Path(__file__).with_name("proving_tasks")
LEVELS = {"лёгкое": 1, "среднее": 2, "трудное": 3}  # task.json's level, as it is written there  # i18n: keep
GRADES = {1: "easy", 2: "medium", 3: "hard"}  # the level without words: the window colours the hard ones
GRACE_S = 120  # a run's hard time: the task's minutes and this
CHECK_TIMEOUT_S = 120
ENDED = ("achieved", "blocked", "budget", "cleared")  # a goal's ways to end a run
EFFORT = "medium"


class ProvingError(ValueError):
    status = 422


class ProvingMissing(ProvingError):
    status = 404


# ---------------------------------------------------------------- the tasks


def python_command() -> str:
    """This program's own Python, as PowerShell runs it - the console one, not pythonw (it has no output)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        exe = exe.parent / "python.exe"
    return f'& "{exe}"'


def _spec(folder: Path) -> dict[str, Any]:
    data = json.loads((folder / "task.json").read_text(encoding="utf-8"))
    return {"id": folder.name, **data}


def shown(spec: dict[str, Any], field: str) -> str:
    """What people read of a task - its title, level, about - in the hub's language: task.json's own words are
    Russian, its "en" holds the English. The objective and the constraints are the task the model gets: they stay as
    written, the same in every language, so that exams stay comparable."""
    words = spec.get(i18n.language(), {}) if i18n.language() != i18n.SOURCE else {}
    return words.get(field) or spec.get(field, "")


def check_why(spec: dict[str, Any], n: int) -> str:
    """What the n-th hidden check (from 1) looks at, in the hub's language (task.json's "en" "why": one per check)."""
    words = spec.get(i18n.language(), {}).get("why", []) if i18n.language() != i18n.SOURCE else []
    return (words[n - 1] if n <= len(words) else "") or spec["exam"][n - 1].get("why", "")


def tasks() -> list[dict[str, Any]]:
    """The tasks, the easy ones first: what the window shows (without the hidden checks' commands)."""
    found = [_spec(f) for f in TASKS_DIR.iterdir() if (f / "task.json").is_file()]
    found.sort(key=lambda t: (LEVELS.get(t.get("level", ""), 9), shown(t, "title")))
    return [{"id": t["id"], "title": shown(t, "title"), "level": shown(t, "level"),
             "grade": GRADES.get(LEVELS.get(t.get("level", ""), 0), ""), "about": shown(t, "about"),
             "objective": substitute(t["objective"], t["id"]), "limits": t.get("limits", {}),
             "criteria": len(t.get("criteria", [])), "hidden": len(t.get("exam", []))} for t in found]  # fmt: skip


def task(task_id: str) -> dict[str, Any]:
    folder = TASKS_DIR / task_id
    if not task_id.replace("_", "").isalnum() or not (folder / "task.json").is_file():
        raise ProvingMissing(f"no such task: {task_id}")
    return _spec(folder)


def substitute(text: str, task_id: str) -> str:
    return text.replace("{python}", python_command()).replace("{exam}", str(TASKS_DIR / task_id / "exam"))


def contract(spec: dict[str, Any], project_name: str, model_id: str, *, judge: bool) -> dict[str, Any]:
    """The task's goal, ready for hub/goals.py: its commands with this program's Python, its project named."""
    criteria = [{"command": substitute(c["command"], spec["id"]), "project": project_name,
                 "expected_exit_code": c.get("expected_exit_code", 0)} for c in spec.get("criteria", [])]  # fmt: skip
    return {"objective": substitute(spec["objective"], spec["id"]), "criteria": criteria,
            "constraints": list(spec.get("constraints", [])), "model_id": model_id, "effort": EFFORT,
            "limits": dict(spec.get("limits", {})), "judge_model_id": "" if judge else "none"}  # fmt: skip


def copy_task(spec: dict[str, Any], folder: Path) -> None:
    """The task's files in a fresh folder, as a git repository with one commit (blocking)."""
    shutil.copytree(TASKS_DIR / spec["id"] / "files", folder)
    (folder / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")  # Python's caches are not changes
    # the user's own git settings stay out of it (D46): their hooks, their signing - a copy is not their project
    own = ["-c", "user.name=AFO", "-c", "user.email=proving@afo", "-c", "commit.gpgsign=false",
           "-c", f"core.hooksPath={folder / '.git' / 'no-hooks'}"]  # fmt: skip
    steps = (("init", "-q", "-b", "main"), (*own, "add", "-A"), (*own, "commit", "-q", "-m", "the task as given"))
    for args in steps:
        step = next(a for a in args if a in ("init", "add", "commit"))
        try:
            done = projects.git(folder, *args, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:  # without git the judge reads the hub's writes
            log.warning("proving copy %s: git %s did not run: %s", folder.name, step, exc)
            return
        if done.returncode != 0:
            log.warning("proving copy %s: git %s failed: %s", folder.name, step,
                        done.stderr.decode("utf-8", "replace").strip()[:300])  # fmt: skip
            return


def _remove_tree(folder: Path) -> None:
    """A copy gone, git's read-only files too (blocking)."""

    def writable(func, path, _info):
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IWRITE)
            func(path)

    if folder.exists():
        shutil.rmtree(folder, onexc=writable)


# ---------------------------------------------------------------- runs and exams as data


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["checks"] = json.loads(row["checks"] or "[]")
    return out


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proving_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise ProvingMissing("no such run")
    return _run_row(row)


def runs_of(conn: sqlite3.Connection, exam_id: str | None) -> list[dict[str, Any]]:
    """An exam's runs in their order; None: the tries («Попробовать»), the newest first."""
    if exam_id is None:
        rows = conn.execute("SELECT * FROM proving_runs WHERE exam_id IS NULL ORDER BY created_at DESC LIMIT 50")
    else:
        rows = conn.execute("SELECT * FROM proving_runs WHERE exam_id = ? ORDER BY n", (exam_id,))
    return [_run_row(r) for r in rows.fetchall()]


def summary(runs: list[dict[str, Any]], model_ids: list[str]) -> list[dict[str, Any]]:
    """Per model: how many tasks it passed of how many, and what it took."""
    out = []
    for model_id in model_ids:
        mine = [r for r in runs if r["model_id"] == model_id]
        out.append({"model_id": model_id, "passed": sum(r["status"] == "passed" for r in mine), "tasks": len(mine),
                    "done": sum(r["status"] not in ("waiting", "running") for r in mine),
                    "turns": sum(r["turns"] for r in mine), "tokens": sum(r["tokens"] for r in mine),
                    "cost_usd": round(sum(r["cost_usd"] for r in mine), 6),
                    "seconds": round(sum(r["seconds"] for r in mine), 1)})  # fmt: skip
    return out


def get_exam(conn: sqlite3.Connection, exam_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM proving_exams WHERE id = ?", (exam_id,)).fetchone()
    if row is None:
        raise ProvingMissing("no such exam")
    exam = dict(row)
    exam["models"], exam["tasks"] = json.loads(row["models"]), json.loads(row["tasks"])
    exam["runs"] = runs_of(conn, exam_id)
    exam["summary"] = summary(exam["runs"], exam["models"])
    return exam


def list_exams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ids = [r[0] for r in conn.execute("SELECT id FROM proving_exams ORDER BY created_at DESC LIMIT 30")]
    return [get_exam(conn, i) for i in ids]


# ---------------------------------------------------------------- the ground


class ProvingGround:
    """Makes the runs, waits for their goals, checks them; keeps the exams' queue."""

    def __init__(self, db: Database, bus: EventBus, chat: Any, goals: Goals, root: Path):
        self._db = db
        self._bus = bus
        self._chat = chat
        self._goals = goals
        self._root = root  # data/proving
        self._exams: dict[str, asyncio.Task] = {}
        self._stopping: set[str] = set()  # exams told to stop

    async def start(self) -> int:
        """What the last hub left going stops: its runs and exams are marked so, their goals cleared; the chats of
        the proving ground's runs are known again (nobody answers there)."""

        def mark(conn) -> tuple[list[str], list[str]]:
            going = [r[0] for r in conn.execute(
                "SELECT conversation_id FROM proving_runs WHERE status IN ('running', 'waiting')")]  # fmt: skip
            conn.execute("UPDATE proving_runs SET status = 'stopped', detail = ?, ended_at = ?"
                         " WHERE status IN ('running', 'waiting')", (tr("хаб перезапустился"), utc_now()))  # fmt: skip
            conn.execute("UPDATE proving_exams SET status = 'stopped', ended_at = ? WHERE status = 'running'",
                         (utc_now(),))  # fmt: skip
            known = [r[0] for r in conn.execute("SELECT conversation_id FROM proving_runs WHERE conversation_id"
                                                " IS NOT NULL")]  # fmt: skip
            return going, known

        going, known = await self._db.acall(mark)
        self._chat.proving.update(known)
        for conversation_id in going:
            if conversation_id:
                with contextlib.suppress(GoalError):
                    await self._goals.clear(conversation_id, by="hub")
        return len(going)

    # ---------- «Попробовать» ----------

    async def try_task(self, task_id: str, model_id: str, *, by: str) -> dict[str, Any]:
        """A run the user watches: its chat is ready when this returns; the hidden checks come after its goal."""
        spec = task(task_id)
        run = await self._db.acall(lambda conn: self._new_run(conn, spec, model_id, None, 0))
        run = await self._prepare(run, spec, judge=True)
        if run["status"] == "running":
            task_ = asyncio.create_task(self._finish_when_done(run, spec))
            task_.add_done_callback(lambda t: t.cancelled() or t.exception())
        return run

    # ---------- the exam ----------

    async def start_exam(self, model_ids: list[str], task_ids: list[str], *, judge: bool, by: str) -> dict:
        model_ids = list(dict.fromkeys(m for m in model_ids if m))
        task_ids = list(dict.fromkeys(t for t in task_ids if t)) or [t["id"] for t in tasks()]
        if not model_ids:
            raise ProvingError("choose at least one model")
        specs = [task(t) for t in task_ids]

        def check(conn):
            for model_id in model_ids:
                model = store.get_model(conn, model_id)
                if model is None or not model["enabled"]:
                    raise ProvingError(f"model {model_id!r} is not available now")
                if model["provider"] == "claude-code":
                    raise ProvingError("Claude Code carries no goal yet (stage 11.7): leave it out of the exam")
            if conn.execute("SELECT 1 FROM proving_exams WHERE status = 'running'").fetchone():
                raise ProvingError("an exam is going already: one at a time")
            exam_id = uuid.uuid4().hex[:12]
            conn.execute("INSERT INTO proving_exams (id, models, tasks, judge, created_by) VALUES (?, ?, ?, ?, ?)",
                         (exam_id, json.dumps(model_ids), json.dumps(task_ids), int(judge), by))  # fmt: skip
            n = 0
            for spec in specs:  # task by task: every model meets a task before the next one
                for model_id in model_ids:
                    n += 1
                    self._new_run(conn, spec, model_id, exam_id, n)
            return exam_id

        exam_id = await self._db.acall(check)
        exam = await self._db.acall(lambda conn: get_exam(conn, exam_id))
        await self._bus.publish("proving.exam", {"exam": exam})
        task_ = self._exams[exam_id] = asyncio.create_task(self._go(exam_id, judge))
        task_.add_done_callback(lambda t: t.cancelled() or t.exception())
        return exam

    async def stop_exam(self, exam_id: str) -> dict[str, Any]:
        """The run going now ends (its goal cleared, its turn stopped); those waiting are skipped."""
        exam = await self._db.acall(lambda conn: get_exam(conn, exam_id))
        if exam["status"] != "running":
            raise ProvingError(f"the exam is {exam['status']}")
        self._stopping.add(exam_id)
        for run in exam["runs"]:
            if run["status"] == "running" and run["conversation_id"]:
                await self._end_goal(run["conversation_id"])
        return await self._db.acall(lambda conn: get_exam(conn, exam_id))

    async def delete_exam(self, exam_id: str) -> None:
        exam = await self._db.acall(lambda conn: get_exam(conn, exam_id))
        if exam["status"] == "running":
            await self.stop_exam(exam_id)
            going = self._exams.get(exam_id)
            if going is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(asyncio.shield(going), 30)
        for run in exam["runs"]:
            await self._remove_run(run)
        await self._db.acall(lambda conn: conn.execute("DELETE FROM proving_exams WHERE id = ?", (exam_id,)))
        await self._bus.publish("proving.deleted", {"exam_id": exam_id})

    async def delete_run(self, run_id: str) -> None:
        """A try gone, with its copy, chat and project."""
        run = await self._db.acall(lambda conn: get_run(conn, run_id))
        if run["exam_id"]:
            raise ProvingError("a run of an exam goes with its exam")
        if run["status"] == "running" and run["conversation_id"]:
            await self._end_goal(run["conversation_id"])
        await self._remove_run(run)
        await self._bus.publish("proving.deleted", {"run_id": run_id})

    async def _go(self, exam_id: str, judge: bool) -> None:
        try:
            for run in await self._db.acall(lambda conn: runs_of(conn, exam_id)):
                if exam_id in self._stopping:
                    await self._set(run["id"], status="skipped", detail=tr("экзамен остановлен"))
                    continue
                spec = task(run["task_id"])
                run = await self._prepare(run, spec, judge=judge)
                if run["status"] == "running":
                    await self._finish_when_done(run, spec)
            status = "stopped" if exam_id in self._stopping else "done"
        except Exception:
            log.exception("the exam %s broke off", exam_id)
            status = "stopped"
        finally:
            self._stopping.discard(exam_id)
            self._exams.pop(exam_id, None)
        ended = (status, utc_now(), exam_id)
        await self._db.acall(lambda conn: conn.execute(
            "UPDATE proving_exams SET status = ?, ended_at = ? WHERE id = ?", ended))  # fmt: skip
        exam = await self._db.acall(lambda conn: get_exam(conn, exam_id))
        await self._bus.publish("proving.exam", {"exam": exam})

    # ---------- one run ----------

    def _new_run(self, conn, spec: dict[str, Any], model_id: str, exam_id: str | None, n: int) -> dict[str, Any]:
        model = store.get_model(conn, model_id)
        if model is None or not model["enabled"]:
            raise ProvingError(f"model {model_id!r} is not available now")
        run_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO proving_runs (id, exam_id, n, task_id, title, model_id, model_name, status, folder)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?)",
            (run_id, exam_id, n, spec["id"], shown(spec, "title"), model_id, model["name"], str(self._root / run_id)),
        )
        return get_run(conn, run_id)

    async def _prepare(self, run: dict[str, Any], spec: dict[str, Any], *, judge: bool) -> dict[str, Any]:
        """The copy, its project and chat, the goal set: the run is going - or failed to start, and says why."""
        folder = Path(run["folder"])
        try:
            await asyncio.to_thread(copy_task, spec, folder)

            def make(conn):
                # the project's name is the model's to read (its criteria name it): as written, like the objective
                project = add_project(conn, str(folder), f"полигон · {spec['title']}")  # i18n: keep
                conn.execute("UPDATE projects SET scratch = 1, cloud_ok = 1 WHERE id = ?", (project.id,))
                title = tr("Полигон: {title} · {model}", title=shown(spec, "title"), model=run["model_name"])
                cid = store.create_conversation(conn, title=title, home_project=project.id)
                conn.execute("UPDATE conversations SET archived = 1 WHERE id = ?", (cid,))  # not in the chats' list
                store.set_context_settings(conn, cid, mode="trusted")
                conn.execute("UPDATE proving_runs SET project_id = ?, conversation_id = ? WHERE id = ?",
                             (project.id, cid, run["id"]))  # fmt: skip
                return get_project(conn, project.id), cid

            project, cid = await self._db.acall(make)
            self._chat.proving.add(cid)
            await self._set(run["id"], status="running", started_at=utc_now())
            await self._goals.set(cid, contract(spec, project.name, run["model_id"], judge=judge), by="proving")
        except (OSError, ProjectError, GoalError) as exc:
            await self._set(run["id"], status="error", detail=tr("не началось: {error}", error=exc), ended_at=utc_now())
        return await self._db.acall(lambda conn: get_run(conn, run["id"]))

    async def _finish_when_done(self, run: dict[str, Any], spec: dict[str, Any]) -> None:
        """Waits for the goal to end (or the hard time), then the hidden checks; the run's verdict."""
        cid = run["conversation_id"]
        limit = (spec.get("limits", {}).get("max_minutes") or 60) * 60 + GRACE_S
        subscription = self._bus.subscribe(("goal.status",))
        deadline = time.monotonic() + limit
        timed_out = False
        try:
            goal = (await self._goals.get(cid))["goal"] or {}
            while goal.get("status") not in ENDED:
                left = deadline - time.monotonic()
                if left <= 0:
                    timed_out = True
                    break
                with contextlib.suppress(TimeoutError):
                    event = await asyncio.wait_for(subscription.queue.get(), min(left, 5.0))
                    if event.payload.get("conversation_id") != cid:
                        continue
                goal = (await self._goals.get(cid))["goal"] or {}
        finally:
            self._bus.unsubscribe(subscription)
        if timed_out:
            await self._end_goal(cid)
        await self._idle(cid)
        if (await self._db.acall(lambda conn: get_run(conn, run["id"])))["status"] != "running":
            return  # stopped meanwhile (a hub restart): its mark stays
        goal = (await self._goals.get(cid))["goal"] or {}
        checks = await self._hidden_checks(run, spec)
        met = goal.get("status") == "achieved"
        failed = [c for c in checks if not c["passed"]]
        if timed_out:
            detail = tr("время вышло")
        elif not met:
            said = (goal.get("detail") or goal.get("status", "")).removesuffix(tr(ASK_PERSON))
            detail = tr("цель не достигнута: {detail}", detail=said)
        elif failed:
            first = failed[0]
            detail = tr("скрытая проверка не прошла: {why} — {said}", why=first["why"], said=first["said"][:200])
        else:
            detail = tr("сдано")
        found = json.dumps(checks, ensure_ascii=False)
        await self._set(run["id"], status="passed" if met and not failed and not timed_out else "failed",
                        detail=detail, goal_status=goal.get("status", ""), checks=found,
                        turns=goal.get("turns_used", 0), tokens=goal.get("tokens_used", 0),
                        cost_usd=goal.get("cost_usd", 0.0), seconds=round(goal.get("seconds_used", 0.0), 1),
                        ended_at=utc_now())  # fmt: skip

    async def _hidden_checks(self, run: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
        project = await self._db.acall(lambda conn: get_project(conn, run["project_id"]))
        out = []
        for n, check in enumerate(spec.get("exam", []), 1):
            command = substitute(check["command"], spec["id"])
            title = tr("Экзамен: {title} · {n}", title=shown(spec, "title"), n=n)
            try:
                done = await commands.execute(self._chat.terminals, project, command, timeout=CHECK_TIMEOUT_S,
                                              by="proving", title=title)  # fmt: skip
            except Exception as exc:
                out.append({"n": n, "why": check_why(spec, n), "passed": False, "said": f"did not run: {exc}"})
                continue
            if done["status"] == "running":
                with contextlib.suppress(Exception):
                    await self._chat.terminals.kill(done["terminal"])
            said = clean("\n".join(done["lines"][-10:]))
            out.append({"n": n, "why": check_why(spec, n), "said": said,
                        "passed": done["status"] != "running" and done["exit_code"] == 0})  # fmt: skip
        return out

    async def _end_goal(self, conversation_id: str) -> None:
        with contextlib.suppress(GoalError):
            await self._goals.clear(conversation_id, by="proving")
        self._chat.cancel(conversation_id)

    async def _idle(self, conversation_id: str, timeout: float = 60.0) -> None:
        """The last turn has ended (its files are as the model left them)."""
        deadline = time.monotonic() + timeout
        while conversation_id in self._chat.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

    async def _set(self, run_id: str, **fields: Any) -> None:
        names = ", ".join(f"{k} = ?" for k in fields)
        await self._db.acall(lambda conn: conn.execute(f"UPDATE proving_runs SET {names} WHERE id = ?",
                                                       (*fields.values(), run_id)))  # fmt: skip
        run = await self._db.acall(lambda conn: get_run(conn, run_id))
        await self._bus.publish("proving.run", {"run": run})

    async def _remove_run(self, run: dict[str, Any]) -> None:
        def forget(conn):
            if run["conversation_id"]:
                store.delete_conversation(conn, run["conversation_id"])
            if run["project_id"]:
                remove_project(conn, run["project_id"])
            conn.execute("DELETE FROM proving_runs WHERE id = ?", (run["id"],))

        await self._db.acall(forget)
        self._chat.proving.discard(run["conversation_id"])
        await asyncio.to_thread(_remove_tree, Path(run["folder"]))
