"""The window's and the hub's native crashes (D46, 23.2): data/logs/app-crash.log and hub-crash.log.

faulthandler writes the Python stacks of a native crash there - but on Windows it writes an exception that was caught
too (0x800706be, a COM call that failed: 29.10 the window went on after it and closed normally), so a record alone does
not say the program died. Each session marks its start in the file and, when the process exits normally, its end; a
record after which the session did not end is a crash, and the next start writes it down as one. «Проверить систему»
shows those of the last days (hub/diagnostics.py).
"""

from __future__ import annotations

import atexit
import contextlib
import faulthandler
import os
from datetime import datetime
from pathlib import Path

MARK = "=== "
CRASHED = "=== crashed at "
CAUGHT = "the record above is an exception Windows caught, not a crash"
TOO_OLD = "the record above is from before the sessions were marked: whether it was a crash is not known"
KEEP_BYTES = 1024 * 1024  # a longer file keeps its last half at the next start


def watch(path: Path) -> None:
    """At the process's start: the last session's crash written down, this session's start marked, faulthandler on;
    a normal exit marks the end."""
    _trim(path)
    verdict = _verdict(path)
    crash_file = open(path, "a", encoding="utf-8")  # noqa: SIM115 - open until exit
    if verdict:
        _write(crash_file, verdict)
    _write(crash_file, f"{MARK}{_now()} pid {os.getpid()} started")
    size = path.stat().st_size
    faulthandler.enable(crash_file, all_threads=True)

    def ended() -> None:
        with contextlib.suppress(OSError, ValueError):
            caught = path.stat().st_size > size  # faulthandler wrote, and the process lived on
            _write(crash_file, f"{MARK}{_now()} pid {os.getpid()} ended normally" + (f": {CAUGHT}" if caught else ""))

    atexit.register(ended)


def found(path: Path, since: datetime) -> list[tuple[datetime, str]]:
    """The crashes written down since then: when the record was written, and its first line."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if line.startswith(CRASHED):
            stamp, _, what = line.removeprefix(CRASHED).partition(": ")
            with contextlib.suppress(ValueError):
                when = datetime.fromisoformat(stamp)
                if when >= since:
                    out.append((when, what))
    return out


def _verdict(path: Path) -> str:
    """The line for what came after the last mark: a record there is the last session's crash."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        written = datetime.fromtimestamp(path.stat().st_mtime)  # the record was the file's last write
    except OSError:
        return ""
    lines = text.splitlines()
    marks = [i for i, line in enumerate(lines) if line.startswith(MARK)]
    record = [line.strip() for line in lines[marks[-1] + 1 if marks else 0 :] if line.strip()]
    if not record:
        return ""
    return f"{CRASHED}{written:%Y-%m-%d %H:%M:%S}: {record[0]}" if marks else MARK + TOO_OLD


def _trim(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.stat().st_size > KEEP_BYTES:
            tail = path.read_bytes()[-KEEP_BYTES // 2 :]
            path.write_bytes(tail.split(b"\n", 1)[-1])


def _write(crash_file, line: str) -> None:
    crash_file.write(line + "\n")
    crash_file.flush()


def _now() -> str:
    return f"{datetime.now():%Y-%m-%d %H:%M:%S}"
