"""A stand-in of an engine's script on PyTorch (stage 56; hub/torch_scripts/select.py) - a real process, started by
the hub with the engine's own Python (`python -I fake_torch_engine.py <arguments>`), in the engine's folder.

- --check: a line of JSON, exit 0 ($FAKE_ENGINE_NO_CUDA - the card not seen: exit 3).
- --frames <dir> --out <dir> --at <n> --x --y ... [--masks]: each frame copied into <out> by its name - as SAM 2.1
  gives the frames back, the object kept (or its masks: a file a frame).
- (58, hub/torch_scripts/replace.py) --draw <plan.json>: each window's moments not kept from the one before - the
  moment's picture copied into the plan's `out` as the moment drawn, its steps written into `progress` as it goes
  ($FAKE_ENGINE_STEP_S a step); the plan copied beside $FAKE_ENGINE_LOG as plan-<n>.json. --compose <plan.json>:
  each frame's `drawn` copied - edges: scaled to the canvas - numbered from 1.
- (59, hub/torch_scripts/depth.py) --depth <plan.json>: each of the plan's `frames` copied into its `out` as its
  depth, numbered from 1; the plan copied beside $FAKE_ENGINE_LOG as depth-<n>.json.
- (57, hub/torch_scripts/lips.py) --frames <dir> --voice <wav> --out <dir> --fps <n>: each frame copied, as MuseTalk
  gives back a frame without a face; the voice copied beside $FAKE_ENGINE_LOG as voice-<n>.wav; the last line - JSON
  {"frames", "faces", "made"} ($FAKE_ENGINE_NO_FACE - no face found).

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


def gray_png(width, height):
    """A gray PNG of this size, by hand: zlib and struct are all this Python has."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))  # fmt: skip


def main():
    args = sys.argv[1:]
    if log := os.environ.get("FAKE_ENGINE_LOG"):
        lock, deadline, held = f"{log}.lock", time.monotonic() + 30, None  # two engines' looks at once (58.2)
        while held is None and time.monotonic() < deadline:
            try:
                held = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                time.sleep(0.01)
        try:
            with open(log, "a", encoding="utf-8") as out:
                out.write(json.dumps({"args": args, "cwd": os.getcwd(), "python": sys.executable,
                                      "isolated": bool(sys.flags.isolated),
                                      "env": {k: os.environ.get(k) for k in GIVEN}}) + "\n")  # fmt: skip
        finally:
            if held is not None:
                os.close(held)
                os.unlink(lock)
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
    if "--depth" in args:  # (59) each picture copied as its depth, numbered from 1; the plan beside the log
        plan = json.loads(Path(value(args, "--depth")).read_text(encoding="utf-8"))
        if log := os.environ.get("FAKE_ENGINE_LOG"):
            kept = Path(log).with_name(f"depth-{len(list(Path(log).parent.glob('depth-*.json')))}.json")
            kept.write_text(json.dumps(plan), encoding="utf-8")
        out = Path(plan["out"])
        out.mkdir(parents=True, exist_ok=True)
        for n, frame in enumerate(plan["frames"], start=1):
            shutil.copyfile(frame, out / f"{n:06d}.png")
            Path(plan["progress"]).write_text(f"{n} {len(plan['frames'])}", encoding="utf-8")
        print(json.dumps({"frames": len(plan["frames"])}))
        return
    if "--draw" in args or "--compose" in args:
        plan = json.loads(Path(value(args, "--draw") or value(args, "--compose")).read_text(encoding="utf-8"))
        if log := os.environ.get("FAKE_ENGINE_LOG"):
            kept = Path(log).with_name(f"plan-{len(list(Path(log).parent.glob('plan-*.json')))}.json")
            kept.write_text(json.dumps(plan), encoding="utf-8")
        out = Path(plan["out"])
        out.mkdir(parents=True, exist_ok=True)
        if "--compose" in args:
            for n, frame in enumerate(plan["frames"], start=1):
                if plan["mode"] == "edges":  # a gray frame of the canvas' size - the engine's Python has no Qt
                    (out / f"{n:06d}.png").write_bytes(gray_png(*plan["canvas"][:2]))
                else:
                    shutil.copyfile(frame["drawn"], out / f"{n:06d}.png")
            print(json.dumps({"frames": len(plan["frames"])}))
            return
        steps, done = int(plan["steps"]), 0
        total = len(plan["windows"]) * steps
        for first, end, keep in plan["windows"]:
            for _ in range(steps):
                time.sleep(float(os.environ.get("FAKE_ENGINE_STEP_S") or 0))
                done += 1
                Path(plan["progress"]).write_text(f"{done} {total}", encoding="utf-8")
            for k in range(first + keep, end):
                shutil.copyfile(plan["moments"][k]["frame"], out / f"{k + 1:06d}.png")
        print(json.dumps({"windows": len(plan["windows"])}))
        return
    frames, out = Path(value(args, "--frames")), Path(value(args, "--out"))
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for frame in sorted(frames.glob("*.png")):
        shutil.copyfile(frame, out / frame.name)
        count += 1
    if voice := value(args, "--voice"):  # (57) the lips
        if log := os.environ.get("FAKE_ENGINE_LOG"):
            shutil.copyfile(voice, Path(log).with_name(f"voice-{len(list(Path(log).parent.glob('voice-*.wav')))}.wav"))
        faces = 0 if os.environ.get("FAKE_ENGINE_NO_FACE") else count
        print(json.dumps({"frames": count, "faces": faces, "made": faces}))
        return
    print(json.dumps({"frames": count, "shown": count}))


main()
