"""A stand-in of uv (stage 56; hub/torch_engines.py) - a real process, started as `python fake_uv.py <uv's arguments>`
in the folder the hub gives it, with the environment the hub gives it. It fetches nothing.

- python install <version> --no-bin --no-registry: <UV_PYTHON_INSTALL_DIR>/cpython-<version>-windows-x86_64-none
  with a python.exe (a file); without both flags - refused, as a uv that would write outside its folder.
- venv <path> --python <version> --no-project: a real environment of this Python (`venv --without-pip`); no Python
  installed before - refused.
- pip install --python <its python> ...: what it was asked, line by line, into <env>/afo-fake-uv.txt.

$FAKE_UV_LOG - each call a JSON line: its arguments, folder, and the UV_*, SAM2_*, MMCV_* (57), TEMP, TMP of its
environment.
Each call leaves a file in a temporary folder of its own, as a build does.
$FAKE_UV_SLOW=<seconds> - `pip install` waits so long, a child of its own waiting with it (its pid into
$FAKE_UV_CHILD); $FAKE_UV_FAIL=<words> - `pip install` says them, exit 1.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def appended(path, line):
    """A line to the end of a log several stand-ins write at once (58.2: two installs) - one at a time, by a lock
    file: Windows' append from two processes may write one over the other."""
    lock, deadline, held = f"{path}.lock", time.monotonic() + 30, None
    while held is None and time.monotonic() < deadline:
        try:
            held = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            time.sleep(0.01)
    try:
        with open(path, "a", encoding="utf-8") as out:
            out.write(line)
    finally:
        if held is not None:
            os.close(held)
            os.unlink(lock)


def main():
    args = sys.argv[1:]
    if log := os.environ.get("FAKE_UV_LOG"):
        seen = {k: v for k, v in os.environ.items() if k.startswith(("UV_", "SAM2_", "MMCV_")) or k in ("TEMP", "TMP")}
        appended(log, json.dumps({"args": args, "cwd": os.getcwd(), "env": seen}) + "\n")
    (Path(tempfile.mkdtemp()) / "build.txt").write_text("a build's file", encoding="utf-8")  # as a build leaves
    if args[:2] == ["python", "install"]:
        if "--no-bin" not in args or "--no-registry" not in args:
            print("error: would write outside: --no-bin and --no-registry wanted")
            sys.exit(2)
        found = Path(os.environ["UV_PYTHON_INSTALL_DIR"]) / f"cpython-{args[2]}-windows-x86_64-none"
        found.mkdir(parents=True, exist_ok=True)
        (found / "python.exe").write_bytes(b"MZ python")
        print(f"Installed Python {args[2]}")
    elif args[0] == "venv":
        pythons = Path(os.environ["UV_PYTHON_INSTALL_DIR"])
        if not any(pythons.glob(f"cpython-{value(args, '--python')}-*/python.exe")):
            print(f"error: no interpreter found for Python {value(args, '--python')} in managed installations")
            sys.exit(2)
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", args[1]], check=True)
    elif args[:2] == ["pip", "install"]:
        if slow := float(os.environ.get("FAKE_UV_SLOW") or 0):
            child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({slow})"])
            Path(os.environ["FAKE_UV_CHILD"]).write_text(str(child.pid))
            time.sleep(slow)
        if failing := os.environ.get("FAKE_UV_FAIL"):
            print(failing)
            sys.exit(1)
        env = Path(value(args, "--python")).parent.parent
        lines = []
        if wanted := value(args, "-r"):
            lines += Path(wanted).read_text(encoding="utf-8").splitlines()
        lines += [a for a in args[2:] if " @ " in a]
        with open(env / "afo-fake-uv.txt", "a", encoding="utf-8") as out:
            out.write("".join(line + "\n" for line in lines))
        print(f"Installed {len(lines)} packages")
    else:
        print(f"unknown: {args}")
        sys.exit(3)


main()
