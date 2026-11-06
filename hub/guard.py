"""The goal's guard (stage 11.4, D44; the user's plan 6 and its idea 3): against going round in circles, and the
contract's constraints the hub checks itself.

Going round in circles has three signals, read from the checks after the worker's turns since the goal last stopped
or closed a stage (an investigation's own turn does not count: it changes nothing):
    the same failure  - a check's fingerprint (its failures without their noise: times, durations, temporary paths,
                        addresses) twice in a row: the next turn investigates; three times in a row: the human;
    a pendulum        - a↔b over four checks: the next turn investigates; again after that: the human;
    thrashing         - one file changed in THRASH_TURNS turns while what fails got no fewer: the same. What a turn
                        changed is read from the projects' trees at its start and its end (21.1) - the hub's writes,
                        Claude Code's and the commands' alike.
An investigation is the plan's idea 3: for one turn writing and commands are refused; the worker reads, follows the
calls and says the root cause and the fix it will make. Its check runs nothing - nothing could change.

Constraints are the judge's words - except three forms the hub checks itself at the call, before anything is asked
(in «Доверенном» too): «не трогать: a/, *.lock» (no write there), «только: src/, tests/» (writes only there), «не
запускать: git push, npm publish» (no such command); in English «don't touch:», «only:», «never run:». A turn that
tries TRIES times stops the goal for the human. What a command does to files is not seen here - the judge reads the
diff.
"""

from __future__ import annotations

import collections
import contextlib
import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.i18n import tr

FINGERPRINT_CHARS = 16
TWICE, THRICE, SWING = 2, 3, 4  # checks in a row
THRASH_TURNS = 3  # turns that changed one file with no fewer failures
TRIES = 3  # refused tries at a constraint in one turn that stop the goal
STOPS = ("blocked", "budget", "paused", "rolled_back")  # the goal stopped or went back: after it, afresh
INVESTIGATING = ("not done: this is an investigation turn (the goal's loop guard) - writing and commands are refused "
                 "for this turn. Read the code, find the root cause; your tools come back next turn.")  # fmt: skip
# what changes between two runs of the same failure
NOISE = (
    (re.compile(r"[A-Za-z]:\\[^\s\"']*\\(Temp|tmp)\\[^\s\"']*|/tmp/[^\s\"']*", re.I), "<temp>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}([T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?Z?)?"), "<date>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b"), "<time>"),
    (re.compile(r"\b\d+(\.\d+)?\s?(ms|s|sec|secs|seconds|мс|с|сек)\b", re.I), "<duration>"),  # i18n: keep
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<address>"),
    (re.compile(r"\b(pid|PID)[ =:#]?\d+"), "<pid>"),
)
FORMS = (
    ("paths_no", re.compile(r"^\s*(не\s+трогать|не\s+менять|не\s+изменять"  # i18n: keep
                            r"|(don'?t|do\s+not|never)\s+(touch|change))\s*:\s*(?P<items>.+)$", re.I)),
    ("paths_only", re.compile(r"^\s*(только|only)\s*:\s*(?P<items>.+)$", re.I)),  # i18n: keep
    ("commands_no", re.compile(r"^\s*(не\s+запускать|не\s+выполнять|(don'?t|do\s+not|never)\s+run)"  # i18n: keep
                               r"\s*:\s*(?P<items>.+)$", re.I)),
)  # fmt: skip
SEGMENTS = re.compile(r"&&|\|\||[;|\n]")


def _quiet(text: str) -> str:
    for pattern, said in NOISE:
        text = pattern.sub(said, text)
    return " ".join(text.split())


def fingerprint(results: list[dict[str, Any]], extra: list[str]) -> str | None:
    """What failed in a check, without what differs between two runs of the same failure; None when nothing did.
    `extra`: what else the check found - files that do not parse, tests lost (not the judge's words: they differ)."""
    parts = [f"{r['n']}:{_quiet(r.get('tail') or r.get('why') or '')}"
             for r in results if r.get("kind") == "command" and r.get("met") is False]  # fmt: skip
    parts += [_quiet(x) for x in extra]
    if not parts:
        return None
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def failing(results: list[dict[str, Any]], extra: list[str]) -> int:
    """How much failed: the commands not met and what else the check found."""
    return sum(1 for r in results if r.get("kind") == "command" and r.get("met") is False) + len(extra)


def afresh(check: dict[str, Any]) -> bool:
    """After this check the signals count afresh: the goal stopped or went back - or a stage was accepted (19.1): a
    stage done is progress, though the criteria may fail the same way until the last one."""
    return check["route"] in STOPS or any(r.get("kind") == "stage" and r.get("accepted")
                                          for r in check.get("results") or [])  # fmt: skip


def _worker(history: list[dict[str, Any]]) -> list[int]:
    """The places in `history` of the checks after the worker's own turns."""
    return [i for i, c in enumerate(history) if c["route"] != "investigated"]


def _progress(history: list[dict[str, Any]], now: int) -> int | None:
    """The place in `history` after which what fails got no fewer (-1: never fewer); None - fewer now."""
    places = _worker(history)
    seq = [history[i]["failing"] for i in places] + [now]
    if len(seq) > 1 and seq[-1] < seq[-2]:
        return None
    last = max((i for i in range(1, len(seq)) if seq[i] < seq[i - 1]), default=0)
    return places[last] if last else -1


def turn_files(check: dict[str, Any]) -> list[str]:
    """The files the turn before this check changed (21.1), as the check keeps them."""
    return next((r.get("files") or [] for r in check.get("results") or [] if r.get("kind") == "turn"), [])


def thrashing(history: list[dict[str, Any]], now: int, files: list[str]) -> tuple[str, int]:
    """The file changed in the most turns since what fails last got fewer, and in how many - the third signal's
    count. `files`: what this turn changed; the earlier turns' are kept with their checks."""
    at = _progress(history, now)
    if at is None:
        return "", 0
    counts = collections.Counter(set(files))
    for check in history[at + 1 :]:
        counts.update(set(turn_files(check)))
    return counts.most_common(1)[0] if counts else ("", 0)


def signal(history: list[dict[str, Any]], fp: str | None, now: int, edits: int = 0,
           edited: str = "") -> tuple[str, str] | None:  # fmt: skip
    """After a check: ("investigate" | "blocked", why) when the goal goes round in circles, else None. `history`: the
    checks since the goal last stopped, the oldest first; `fp`, `now`: this check's fingerprint and failures; `edits`:
    the most turns that changed one file (`edited`) since what fails last got fewer (thrashing())."""
    places = _worker(history)
    fps = [history[i]["fingerprint"] for i in places] + [fp]
    starts = [*places, len(history)]

    def looked_after(at: int) -> bool:
        return any(c["route"] == "investigated" for c in history[max(at, 0) :])

    if fp and len(fps) >= THRICE and len(set(fps[-THRICE:])) == 1:
        return "blocked", tr("одна и та же ошибка {n} хода подряд", n=THRICE)
    if fp and len(fps) >= TWICE and fps[-1] == fps[-2] and not looked_after(starts[-TWICE]):
        return "investigate", tr("одна и та же ошибка два хода подряд")
    if fp and len(fps) >= SWING:
        a, b, c, d = fps[-SWING:]
        if a and b and a != b and (a, b) == (c, d):
            return ("blocked" if looked_after(starts[-SWING]) else "investigate"), tr(
                "ошибки ходят по кругу: одна сменяет другую и возвращается")  # fmt: skip
    if edits >= THRASH_TURNS and now > 0 and (at := _progress(history, now)) is not None:
        said = tr("файл {path} менялся в {n} ходах, а ошибок не меньше", path=edited or "?", n=edits)
        return ("blocked" if looked_after(at) else "investigate"), said
    return None


# ---------------------------------------------------------------- the constraints the hub checks


@dataclass(frozen=True)
class Rule:
    kind: str  # paths_no, paths_only, commands_no
    items: tuple[str, ...]
    text: str  # as the contract has it


@dataclass
class TurnGuard:
    """A goal's turn in the chat (hub/chat.py asks it at each tool call): the constraints to check, whether the turn
    investigates, and the tries refused."""

    rules: list[Rule]
    investigating: bool = False
    refused: list[str] = field(default_factory=list)  # the rules tried, as the contract says them


def _rule(text: str) -> Rule | None:
    for kind, form in FORMS:
        if found := form.match(text):
            items = tuple(i for i in (x.strip().strip("`'\"«»").strip() for x in found["items"].split(",")) if i)
            if items:
                return Rule(kind, items, text)
    return None


def rules(constraints: list[str]) -> list[Rule]:
    return [r for r in map(_rule, constraints) if r is not None]


def checkable(text: str) -> bool:
    return _rule(text) is not None


def _path(path: str) -> str:
    return path.replace("\\", "/").strip().removeprefix("./").strip("/").casefold()


def _matches(path: str, item: str) -> bool:
    item = item.replace("\\", "/").strip().removeprefix("./").casefold()
    if any(ch in item for ch in "*?["):
        return fnmatch.fnmatch(path, item) or ("/" not in item and fnmatch.fnmatch(path.rsplit("/", 1)[-1], item))
    item = item.strip("/")
    return path == item or path.startswith(item + "/")


def _runs(command: str, item: str) -> bool:
    wanted = item.casefold().split()
    return any(seg.casefold().split()[: len(wanted)] == wanted for seg in SEGMENTS.split(command) if seg.strip())


CLAUDE_WRITES = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
CLAUDE_SHELLS = ("Bash", "PowerShell")  # PowerShell: its shell on Windows


def claude_call(tool_name: str, data: dict[str, Any], root: Path | None) -> tuple[str, str] | None:
    """Claude Code's call as the guard sees it (11.7): ("write", the path - from the project's root when it is
    inside) or ("command", its text); None - neither (its reads, the web, its questions)."""
    if tool_name in CLAUDE_SHELLS:
        return "command", str(data.get("command") or "")
    key = CLAUDE_WRITES.get(tool_name)
    if key is None:
        return None
    path = str(data.get(key) or "")
    if root is not None and path:
        full = Path(path) if Path(path).is_absolute() else Path(root) / path
        with contextlib.suppress(ValueError, OSError):
            return "write", full.resolve().relative_to(Path(root).resolve()).as_posix()
    return "write", path


def breaking(found: list[Rule], target: str) -> Rule | None:
    """The rule a write to `target` (the path in the project) breaks: «don't touch» it, or «only» elsewhere."""
    path = _path(target)
    for r in found:
        if r.kind == "paths_no" and any(_matches(path, i) for i in r.items):
            return r
    only = [r for r in found if r.kind == "paths_only"]
    if only and not any(_matches(path, i) for r in only for i in r.items):
        return only[0]
    return None


def refusal(found: list[Rule], what: str, target: str) -> str | None:
    """Why a write (`target`: the path in the project) or a command breaks a constraint the hub checks; None - not."""
    if what == "write":
        path = _path(target)
        r = breaking(found, target)
        if r is None:
            return None
        if r.kind == "paths_no":
            return f"not done: the goal's constraint «{r.text}» forbids writing {path}"
        return f"not done: the goal's constraint «{r.text}» allows writing only there, not {path}"
    for r in found:
        if r.kind == "commands_no" and (item := next((i for i in r.items if _runs(target, i)), None)):
            return f"not done: the goal's constraint «{r.text}» forbids the command `{item}`"
    return None
