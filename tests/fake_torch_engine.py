"""A stand-in of an engine's script on PyTorch (stage 56; hub/torch_scripts/select.py) - a real process, started by
the hub with the engine's own Python (`python -I fake_torch_engine.py <arguments>`), in the engine's folder.

- --check: a line of JSON, exit 0 ($FAKE_ENGINE_NO_CUDA - the card not seen: exit 3).
- --frames <dir> --out <dir> --at <n> --x --y ...: each frame copied into <out> by its name - as SAM 2.1 gives the
  frames back, the object kept.

$FAKE_ENGINE_LOG - each run a JSON line: its arguments, folder, its Python, whether it runs isolated (-I), and the
environment it was given for its caches and temporary files. A run at frames leaves a file in a temporary folder of
its own, as SAM's frames are. $FAKE_ENGINE_SLOW=<seconds> - it waits so long first, a child of its own
waiting with it (its pid into $FAKE_ENGINE_CHILD); $FAKE_ENGINE_FAIL - «oom»: PyTorch's words for a card too small,
exit 1; anything else: those words, exit 1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GIVEN = ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "MPLCONFIGDIR", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
         "PYTHONNOUSERSITE", "VIRTUAL_ENV", "PYTHONPATH", "TEMP", "TMP")  # fmt: skip


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def main():
    args = sys.argv[1:]
    if log := os.environ.get("FAKE_ENGINE_LOG"):
        with open(log, "a", encoding="utf-8") as out:
            out.write(json.dumps({"args": args, "cwd": os.getcwd(), "python": sys.executable,
                                  "isolated": bool(sys.flags.isolated),
                                  "env": {k: os.environ.get(k) for k in GIVEN}}) + "\n")  # fmt: skip
    if "--frames" in args:  # as SAM's frames for a piece: JPEGs in a temporary folder - a run killed leaves them
        (Path(tempfile.mkdtemp()) / "0.jpg").write_bytes(b"\xff\xd8\xff a frame")
    if slow := float(os.environ.get("FAKE_ENGINE_SLOW") or 0):
        child = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({slow})"])
        Path(os.environ["FAKE_ENGINE_CHILD"]).write_text(str(child.pid))
        time.sleep(slow)
    if failing := os.environ.get("FAKE_ENGINE_FAIL"):
        if failing == "oom":
            print("torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.25 GiB. GPU 0 has a total "
                  "capacity of 15.92 GiB of which 1.10 GiB is free.")  # fmt: skip
        else:
            print(failing)
        sys.exit(1)
    if "--check" in args:
        print(json.dumps({"torch": "2.8.0+cu128", "cuda": not os.environ.get("FAKE_ENGINE_NO_CUDA")}))
        sys.exit(3 if os.environ.get("FAKE_ENGINE_NO_CUDA") else 0)
    frames, out = Path(value(args, "--frames")), Path(value(args, "--out"))
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for frame in sorted(frames.glob("*.png")):
        shutil.copyfile(frame, out / frame.name)
        count += 1
    print(json.dumps({"frames": count, "shown": count}))


main()
