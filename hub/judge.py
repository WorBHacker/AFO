"""The judge of a goal (stage 11.2, D44, Q26; the user's plan - docs/goal_engine.md, 4.3): what the worker changed,
the hub's own checks of it, and a model that only reads and says whether the goal is met.

What it looks at:
- git projects: where the files stood when the goal began - a tree written through a separate index (`git add -A`
  into a temporary index, then `write-tree`: git's own objects only - no commit, no branch, the user's index is not
  touched) - and the diff from there to the files now, taken the same way: new files too, ignored ones not, secret
  files never. Projects without git: the hub's own writes in this conversation since then (hub/changes.py) - what a
  command changed there is not seen.
- the first contour, without a model: the changed files still parse (hub/syntax.py); tests deleted, skipped or
  commented out - REGRESSED by the hub itself (the plan's rule 2: a green build bought by losing tests).
- the judge - the model of the role «Проверка цели» (or the one the goal names): the contract, what the hub's checks
  found, the diff, and the worker's last words marked as unverified. It answers JSON: ACHIEVED / INCOMPLETE /
  REGRESSED, what is missing, a word for the worker. A cloud judge only for projects that allow the cloud (D10); all
  it reads passes the secret filter; its use is recorded like any answer's (D12).
The autonomous agent's report «goal met» is checked the same way (D44): its plan, its report and its writes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import budgets, changes, fit, model_settings, projects, store, syntax
from .claude_code import PROVIDER as CLAUDE_CODE
from .db import Database
from .events import EventBus
from .gateway import Gateway, GatewayError, blocks_to_prompt_text
from .gateway.base import cost_usd
from .projects import Project
from .secret_filter import clean, is_secret_file

ROLE = "judge"  # the role of the model that checks goals (D39, 8.4)
VERDICTS = ("ACHIEVED", "INCOMPLETE", "REGRESSED")
GIT_TIMEOUT = 120
PATCH_CHARS = 60_000  # of the diff the judge reads, over all projects
SCAN_CHARS = 2_000_000  # of the test files' diff the hub scans itself
MAX_PATHS = 300
WORDS_CHARS = 3_000
EFFORT = "medium"
TRIES = 2  # an answer that is not the JSON asked for is asked once more
FORM = '{"status": ..., "missing_items": [...], "feedback_for_worker": ...}'

SYSTEM = """You are an independent auditor of whether a goal is met (the judge). You cannot change anything: you only \
read what the hub gives you and judge it.
Judge strictly and skeptically:
1. Do not take the worker's words for it: judge only from the actual diff and the commands' output.
2. A test deleted, commented out or skipped to make the build green, or a hard constraint broken: status REGRESSED, \
and say what was done.
3. ACHIEVED only when the objective and every criterion are met by what the diff and the output show.
4. Otherwise INCOMPLETE: list what is missing, concretely (a file, a function, a behaviour).
Answer with JSON only, nothing around it:
{"status": "ACHIEVED" | "INCOMPLETE" | "REGRESSED", "missing_items": ["..."], "feedback_for_worker": "..."}"""

TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|Tests?\.cs$|Tests?\.java$",
    re.IGNORECASE,
)
TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)|^\s*(?:it|test)\s*\(\s*['\"`]([^'\"`]+)|^\s*func\s+(Test\w+)"
                      r"|^\s*public\s+(?:async\s+)?\w+\s+(\w+)\s*\(\s*\)\s*$")  # fmt: skip
SKIP = re.compile(
    r"@pytest\.mark\.(?:skip|xfail)|pytest\.skip\(|@unittest\.skip|\b(?:it|test|describe)\.skip\("
    r"|\bx(?:it|test|describe)\(|\[Ignore\]|\(Skip\s*=|\bt\.Skip\(|@Disabled\b|#\[ignore\]|@skip\b"
)
COMMENTED = re.compile(r"^\s*(?:#|//)\s*(?:async\s+)?(?:def\s+test\w*|(?:it|test|describe)\s*\(|func\s+Test)")


class JudgeError(Exception):
    """The judge could not say: no model, a model that failed, a budget, an answer not in the form asked."""


# ---------------------------------------------------------------- what changed


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    try:
        done = projects.git(root, *args, timeout=GIT_TIMEOUT, env=env)  # paths as they are, no locks, no prompts
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"git did not run: {exc}") from exc
    if done.returncode != 0:
        raise JudgeError((done.stderr or done.stdout).decode("utf-8", "replace").strip()[:300] or "git failed")
    return done.stdout.decode("utf-8", "replace")


def is_git(root: Path) -> bool:
    return (root / ".git").exists()


def snapshot(root: Path) -> str:
    """The working files of a git project as a git tree - tracked and new ones, without the ignored - written
    through a temporary copy of the index: no commit, no branch, the user's own index untouched (blocking)."""
    index = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    with tempfile.TemporaryDirectory(prefix="localchat-goal-") as folder:
        temp = Path(folder) / "index"
        if index.is_file():  # its file times spare git re-reading files that did not change
            shutil.copyfile(index, temp)
        env = {"GIT_INDEX_FILE": str(temp)}
        _git(root, "add", "-A", env=env)
        return _git(root, "write-tree", env=env).strip()


STATUS = {"A": "added", "D": "deleted", "M": "changed"}


def tree_changes(root: Path, base: str, head: str) -> list[dict[str, str]]:
    """What differs between the two trees, secret files left out: [{path, status}]."""
    out = []
    found = _git(root, "diff", "--name-status", "--no-renames", "-z", base, head).split("\0")
    for status, path in zip(found[0::2], found[1::2], strict=False):  # -z: a path with Cyrillic comes as it is
        if status and path and not projects.secret_file(root, path):
            out.append({"path": path, "status": STATUS.get(status[:1], "changed")})
    return out


def tree_patch(root: Path, base: str, head: str, paths: list[str]) -> str:
    return _git(root, "diff", "--no-renames", "--unified=3", base, head, "--", *paths) if paths else ""


def tree_counts(root: Path, base: str, head: str) -> dict[str, tuple[int, int]]:
    """Lines added and removed in each file between the two trees (a binary file: 0, 0)."""
    out = {}
    for entry in _git(root, "diff", "--numstat", "--no-renames", "-z", base, head).split("\0"):
        added, _, rest = entry.partition("\t")
        removed, _, path = rest.partition("\t")
        if path:
            out[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)
    return out


def line_counts(patch: str) -> tuple[int, int]:
    """One file's unified diff: lines added, lines removed."""
    minus, plus = _lines(patch)
    return len(plus), len(minus)


def recorded(conn: sqlite3.Connection, folder: Path | None, conversation_id: str, since: str,
             project_ids: list[str]) -> dict[str, list[dict[str, str]]]:  # fmt: skip
    """The hub's own writes in this conversation since `since`, per project: each file once, from before the first
    write to after the last: [{path, status, patch}]."""
    rows = conn.execute(
        "SELECT project_id, path, before_hash, after_hash FROM file_changes WHERE conversation_id = ? AND"
        " created_at >= ? AND reverted_at IS NULL ORDER BY id",
        (conversation_id, since),
    ).fetchall()
    first: dict[tuple[str, str], str | None] = {}
    last: dict[tuple[str, str], str] = {}
    for r in rows:
        if r["project_id"] not in project_ids or is_secret_file(r["path"]):
            continue
        key = (r["project_id"], r["path"])
        first.setdefault(key, r["before_hash"])
        last[key] = r["after_hash"]
    out: dict[str, list[dict[str, str]]] = {}
    for (pid, path), after in last.items():
        before = first[(pid, path)]
        patch = ""
        if folder is not None:
            try:
                old = changes.load(folder, before) if before else None
                patch = changes.diff(path, old, changes.load(folder, after))
            except changes.WriteError:  # its kept versions are gone (D30)
                patch = ""
        out.setdefault(pid, []).append({"path": path, "status": "added" if before is None else "changed",
                                        "patch": patch})  # fmt: skip
    return out


# ---------------------------------------------------------------- the first contour


def _lines(patch: str) -> tuple[list[str], list[str]]:
    """One file's unified diff: its removed lines and its added lines."""
    minus, plus = [], []
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git ", "index ", "@@")):
            continue
        if line.startswith("-"):
            minus.append(line[1:])
        elif line.startswith("+"):
            plus.append(line[1:])
    return minus, plus


def _test_names(lines: list[str]) -> set[str]:
    names = set()
    for line in lines:
        match = TEST_DEF.match(line)
        if match:
            names.add(next(g for g in match.groups() if g))
    return names


def sabotage(patches: dict[str, str]) -> list[str]:
    """Tests deleted, skipped or commented out, found in the diffs of test files (`patches`: path -> its diff)."""
    found: list[str] = []
    removed: dict[str, set[str]] = {}
    added: set[str] = set()
    for path, patch in patches.items():
        minus, plus = _lines(patch)
        added |= _test_names(plus)
        if not TEST_FILE.search(path):
            continue
        hidden: set[str] = set()  # commented out: said so, not said again as removed
        for line in plus:
            if SKIP.search(line):
                found.append(f"{path}: a test is skipped now - «{line.strip()[:120]}»")
            elif COMMENTED.match(line):
                found.append(f"{path}: a test is commented out - «{line.strip()[:120]}»")
                hidden |= _test_names([re.sub(r"^\s*(?:#|//)\s*", "", line)])
        removed[path] = _test_names(minus) - _test_names(plus) - hidden
    for path, names in removed.items():
        gone = sorted(names - added)  # moved to another file is not lost
        if gone:
            found.append(f"{path}: test(s) removed - {', '.join(gone[:10])}")
    return found


# ---------------------------------------------------------------- the evidence


@dataclass
class Evidence:
    """What the judge reads about the workspace, and what the hub found there itself."""

    projects: list[dict[str, Any]] = field(default_factory=list)  # {name, how, files [{path, status}], patch}
    syntax: list[dict[str, Any]] = field(default_factory=list)  # {project, path, line, error}
    sabotage: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what could not be seen, and why

    def summary(self) -> list[dict[str, Any]]:
        """For the check's record: each project's changed files (not the diff)."""
        return [{"name": p["name"], "how": p["how"], "files": p["files"]} for p in self.projects]

    def missing(self) -> list[str]:
        out = [f"{e['project']}/{e['path']}, line {e['line']}: does not parse ({e['error']})" for e in self.syntax]
        return out + self.sabotage


def gather(projects: list[Project], baselines: dict[str, str], recorded_changes: dict[str, list[dict[str, str]]],
           ) -> Evidence:  # fmt: skip
    """Each project's changes since the goal began - git from its tree, else the hub's writes -, the syntax of the
    changed files and a scan of the tests (blocking: git, files, a process)."""
    evidence = Evidence()
    scanned: dict[str, str] = {}
    to_parse: list[tuple[Project, str]] = []
    budget = PATCH_CHARS
    for project in projects:
        base = baselines.get(project.id)
        if base and is_git(project.root):
            try:
                head = snapshot(project.root)
                files = tree_changes(project.root, base, head)[:MAX_PATHS]
                paths = [f["path"] for f in files]
                patch = tree_patch(project.root, base, head, paths)
            except JudgeError as exc:
                evidence.notes.append(f"«{project.name}»: its changes could not be read ({exc})")
                continue
            how = "git"
            tests = [p for p in paths if TEST_FILE.search(p)]
            if tests:
                scanned.update(_by_file(tree_patch(project.root, base, head, tests)[:SCAN_CHARS]))
        else:
            mine = recorded_changes.get(project.id, [])
            files = [{"path": c["path"], "status": c["status"]} for c in mine]
            patch = "\n".join(c["patch"] for c in mine)
            how = "hub"
            scanned.update({c["path"]: c["patch"] for c in mine if TEST_FILE.search(c["path"])})
            why = "no starting point was taken" if is_git(project.root) else "not a git repository"
            evidence.notes.append(f"«{project.name}»: {why} - only the hub's own writes are listed; what commands "
                                  "changed there is not seen")  # fmt: skip
        patch = clean(patch)
        shown = patch[:budget]
        budget -= len(shown)
        if len(shown) < len(patch):
            shown += f"\n[... the diff is cut here: {len(patch) - len(shown)} more characters]"
        evidence.projects.append({"name": project.name, "how": how, "files": files, "patch": shown})
        to_parse += [(project, f["path"]) for f in files if f["status"] != "deleted"]
    evidence.sabotage = sabotage(scanned)
    try:
        bad = syntax.check([p.root / path for p, path in to_parse])
    except syntax.SyntaxCheckError as exc:
        evidence.notes.append(str(exc))
        bad = []
    names = {str(p.root / path): (p.name, path) for p, path in to_parse}
    for e in bad:
        project_name, path = names.get(e["path"], ("", e["path"]))
        evidence.syntax.append({"project": project_name, "path": path, "line": e["line"], "error": e["error"]})
    return evidence


def _by_file(patch: str) -> dict[str, str]:
    """A diff of several files as path -> that file's own diff."""
    out: dict[str, list[str]] = {}
    current = None
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[-1]
            out[current] = []
        if current is not None:
            out[current].append(line)
    return {path: "\n".join(lines) for path, lines in out.items()}


# ---------------------------------------------------------------- the judge


def parse(text: str) -> dict[str, Any] | None:
    """The judge's JSON - also inside a code fence or with words around it; None when there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    status = str(data.get("status") or "").strip().upper() if isinstance(data, dict) else ""
    if status not in VERDICTS:
        return None
    missing = data.get("missing_items") or []
    missing = [" ".join(str(m).split())[:500] for m in missing if str(m).strip()][:20] if isinstance(missing, list) \
        else [str(missing)[:500]]  # fmt: skip
    return {"status": status, "missing": missing, "feedback": " ".join(str(data.get("feedback_for_worker") or "")
                                                                       .split())[:2000]}  # fmt: skip


def workspace_text(evidence: Evidence) -> str:
    parts = []
    for p in evidence.projects:
        files = ", ".join(f"{f['status']} {f['path']}" for f in p["files"]) or "nothing"
        how = "git, from where the files stood when the goal began" if p["how"] == "git" else "the hub's own writes"
        parts.append(f'<workspace project="{p["name"]}">\nCHANGED ({how}): {files}\nDIFF:\n{p["patch"] or "(none)"}\n'
                     "</workspace>")  # fmt: skip
    found = evidence.missing()
    parts.append("THE HUB'S OWN CHECKS: " + ("; ".join(found) if found else "every changed file parses; no test was "
                                            "removed, skipped or commented out"))  # fmt: skip
    if evidence.notes:
        parts.append("NOT SEEN: " + "; ".join(evidence.notes))
    return "\n".join(parts)


def goal_prompt(goal: dict[str, Any], results: list[dict[str, Any]], evidence: Evidence, words: str) -> str:
    """What the judge reads about a chat's goal: the contract with what the hub's commands found, the workspace,
    the worker's last words - unverified."""
    lines = []
    for c in goal["criteria"]:
        result = next((r for r in results if r["n"] == c["n"]), {})
        if c["kind"] == "command":
            found = "met" if result.get("met") else f"NOT met - {result.get('why') or 'not run'}"
            lines.append(f"{c['n']}. `{c['command']}` in «{c['project_name'] or c['project_id']}» must end with exit "
                         f"code {c['expected_exit_code']} - the hub ran it: {found}")  # fmt: skip
        else:
            lines.append(f"{c['n']}. (in words, yours to judge) {c['rule']}")
    rules = "\n".join(f"- {c}" for c in goal["constraints"]) or "- none"
    return clean(
        f"<goal_contract>\nOBJECTIVE: {goal['objective']}\nCRITERIA:\n" + "\n".join(lines) +
        f"\nHARD CONSTRAINTS:\n{rules}\n</goal_contract>\n{workspace_text(evidence)}\n"
        f'<worker_claims unverified="true">\n{words[-WORDS_CHARS:] or "(nothing)"}\n</worker_claims>\n'
        "Is the goal met? Answer with the JSON only."
    )  # fmt: skip


def _step_line(step: dict[str, Any]) -> str:
    """A step of the agent's plan: its words, then what the hub saw it change (25.1)."""
    line = f"{step['n']}. [{step['status']}] {step['title']}" + (f" - {step['note']}" if step.get("note") else "")
    if step.get("files") is not None:
        line += " | CHANGED, by the hub's diff: " + (", ".join(f"{f['status']} {f['path']}" for f in step["files"])
                                                      or "nothing")  # fmt: skip
    if step.get("mismatch"):
        line += f" | AGAINST THE CONDITION: {step['mismatch']}"
    return line


def run_prompt(run: dict[str, Any], plan: dict[str, Any] | None, report: dict[str, Any], evidence: Evidence) -> str:
    """What the judge reads about an autonomous agent's «goal met»: the goal, its plan with how each step was
    checked and what the hub saw it change, its report - the agent's words, unverified - and its writes."""
    steps = "\n".join(_step_line(s) for s in (plan or {}).get("steps") or []) or "(no plan)"
    told = "\n".join([report.get("summary") or "", *("done: " + d for d in report.get("done") or []),
                      *("not done: " + d for d in report.get("not_done") or [])])  # fmt: skip
    return clean(
        f"<goal_contract>\nOBJECTIVE: {run['goal']}\nDONE WHEN: {run.get('done_when') or '(not said)'}\n"
        f"</goal_contract>\n<agent_plan>\n{steps}\n</agent_plan>\n{workspace_text(evidence)}\n"
        f'<worker_claims unverified="true">\n{told[-WORDS_CHARS:]}\n</worker_claims>\n'
        "The agent says the goal is met. Is it? Answer with the JSON only."
    )  # fmt: skip


def last_words(conn: sqlite3.Connection, conversation_id: str) -> str:
    """The worker's last text in the conversation."""
    for message in reversed(store.get_messages(conn, conversation_id)):
        if message.role == "assistant":
            text = blocks_to_prompt_text(message.content).strip()
            if text:
                return text
    return ""


def role_model(conn: sqlite3.Connection) -> str | None:
    roles = model_settings.current(conn, model_settings.ALL)[1].get("roles") or {}
    return roles.get(ROLE) or None


def check_model(conn: sqlite3.Connection, model_id: str, projects: list[Project]) -> dict[str, Any]:
    """The judge's model, if it may judge here: offered, not Claude Code, a cloud one only where every project
    allows the cloud (D10)."""
    model = store.get_model(conn, model_id)
    if model is None or not model["enabled"]:
        raise JudgeError(f"the judge's model {model_id!r} is not available now")
    if model["provider"] == CLAUDE_CODE:
        raise JudgeError("Claude Code is an agent, not a judge: choose another model to check goals")
    local_only = [p.name for p in projects if not p.cloud_ok]
    if model["locality"] == "cloud" and local_only:
        names = ", ".join(f"«{n}»" for n in local_only)
        raise JudgeError(f"the judge «{model['name']}» is a cloud model, and {names} only local models may see "
                         "(D10): choose a local judge or allow the cloud in the project's settings")  # fmt: skip
    return model


class Judge:
    """Gathers the evidence and asks the judge's model; the goals (hub/goals.py) and the agent's reports use it."""

    def __init__(self, db: Database, bus: EventBus, gateway: Gateway, snapshots: Path | None):
        self._db = db
        self._bus = bus
        self._gateway = gateway
        self.snapshots = snapshots

    async def evidence(self, conversation_id: str, projects: list[Project], baselines: dict[str, str],
                       since: str) -> Evidence:  # fmt: skip
        mine = await self._db.acall(lambda conn: recorded(conn, self.snapshots, conversation_id, since,
                                                          [p.id for p in projects]))  # fmt: skip
        return await asyncio.to_thread(gather, projects, baselines, mine)

    async def ask(self, model_id: str, prompt: str, *, conversation_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        """The judge's verdict: {status, missing, feedback, model_id, tokens, cost_usd}. Raises JudgeError when it
        cannot say (the model failed or is held back by a budget, or twice answered other than asked)."""
        return await self.complete(model_id, SYSTEM, prompt, parse, conversation_id=conversation_id,
                                   meta={"purpose": "judge", **meta}, form=FORM)  # fmt: skip

    async def complete(self, model_id: str, system: str, prompt: str, read: Any, *, conversation_id: str | None,
                       meta: dict[str, Any], form: str) -> dict[str, Any]:  # fmt: skip
        """One question to a model, outside any conversation's turns (the judge, the goal's draft): its answer
        read by `read` (None: not in the form asked - asked once more, with `form`); its use recorded under `meta`
        (D12). {**what read gave, model_id, tokens, cost_usd}; JudgeError when there is no such answer."""
        model = await self._db.acall(lambda conn: store.get_model(conn, model_id))
        if model is None or not model["enabled"]:
            raise JudgeError(f"the model {model_id!r} is not available now")
        provider, model_ref = self._gateway.resolve(model_id)
        own = (await self._db.acall(lambda conn: model_settings.current(conn, model_id)))[1]
        params = model_settings.clean_params(own.get("params") or {}, self._gateway.accepts(model_id))
        extra = {"params": params} if params else {}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if model["prices"]:  # a used-up budget, or less left than the question may cost (stage 20.2)
            estimate = budgets.upper_cost(model, fit.estimate(messages), params)
            over = await self._db.acall(lambda conn: budgets.too_dear(conn, model_id, estimate))
            if over and all(b["state"] == "hard" for b in over):
                raise JudgeError(f"the budget of «{model['name']}» is used up")
            if over:
                raise JudgeError(budgets.held_back(over, f"a question to «{model['name']}»"))
        tokens, cost = 0, 0.0
        for _attempt in range(TRIES):
            text, end = [], None
            try:
                async for event in provider.stream_chat(model_ref, messages, EFFORT, asyncio.Event(), **extra):
                    if event.kind == "content":
                        text.append(event.text)
                    elif event.kind == "end":
                        end = event
            except GatewayError as exc:
                raise JudgeError(str(exc)) from exc
            usage = (end.usage if end else None) or {}
            spent = cost_usd(usage, model["prices"])
            tokens += max(int(usage.get("input_tokens") or 0) - int(usage.get("cached_input_tokens") or 0), 0)
            tokens += int(usage.get("output_tokens") or 0)
            cost = round(cost + spent, 6)

            def write(conn, usage=usage, spent=spent):
                store.record_usage(conn, client_id=None, conversation_id=conversation_id, message_id=None,
                                   model_id=model_id, usage=usage, cost_usd=spent, meta=meta)  # fmt: skip
                return budgets.crossed(conn, model_id, spent)

            for kind, budget in await self._db.acall(write):
                await self._bus.publish(f"budget.{kind}", {"model_id": model_id, "budget": budget})
            if end is not None and end.refusal:
                raise JudgeError(f"the model declined: {end.refusal}")
            answer = "".join(text)
            found = read(answer)
            if found is not None:
                return {**found, "model_id": model_id, "tokens": tokens, "cost_usd": cost}
            messages += [{"role": "assistant", "content": answer},
                         {"role": "user", "content": f"That is not the JSON asked for. Answer with the JSON object "
                          f"only: {form}"}]  # fmt: skip
        raise JudgeError("the model did not answer in the form asked (JSON)")
