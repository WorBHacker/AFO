"""A stand-in of «Улучшить ролик»'s programs (stage 54; hub/enhance.py) - a real process, started as
`python fake_enhance.py <the program's name> <its arguments>`, in the program's folder as the hub starts it.

- rife-ncnn-vulkan.exe -i <in> -o <out> -n <frames> -m <model> -f <pattern>: `frames` frames into <out>, frame k a
  copy of the input frame at or before its time k × inputs / frames - where the real one puts its inputs (58.1: input
  j is output j × frames / inputs exactly); the model's folder must lie beside it.
- realesrgan-ncnn-vulkan.exe -i <in> -o <out> -n <model> -s 4 -f png: each input frame four times larger, by its
  name; the model's files must lie in models/.
- sd-cli.exe -M vid_upscale ... --input-frames <in> --upscale-factor 2 -o <out>/%06d.png: each input frame twice as
  large, numbered from 1. (A file that is no picture is copied as it is.)

What it was asked is a line of $FAKE_ENHANCE_LOG (JSON). $FAKE_ENHANCE_SLOW - seconds it waits first (to be stopped);
$FAKE_ENHANCE_FAIL - «oom»: sd-cli's words for a card too small, exit 1; anything else: those words, exit 1.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def larger(frame, target, times):
    """A frame `times` larger each way, as an enlarging program gives it; a file that is no picture - copied."""
    from PySide6.QtGui import QImage

    image = QImage(str(frame))
    if image.isNull():
        shutil.copyfile(frame, target)
        return
    image.scaled(image.width() * times, image.height() * times).save(str(target), "PNG")


def main():
    name, args = sys.argv[1], sys.argv[2:]
    if log := os.environ.get("FAKE_ENHANCE_LOG"):
        with open(log, "a", encoding="utf-8") as out:
            out.write(json.dumps({"program": name, "args": args, "cwd": os.getcwd()}) + "\n")
    time.sleep(float(os.environ.get("FAKE_ENHANCE_SLOW") or 0))
    if failing := os.environ.get("FAKE_ENHANCE_FAIL"):
        if failing == "oom":
            print("[W] execution engine cannot make enough memory available on CUDA0: need 4304.68 MB device")
            print("[E] RelayVSR Wan segment 31/31 (ggml_runner.final) failed during weight preparation")
        else:
            print(failing)
        sys.exit(1)
    if name == "rife-ncnn-vulkan.exe":
        source, target, count = Path(value(args, "-i")), Path(value(args, "-o")), int(value(args, "-n"))
        if not Path(value(args, "-m")).is_dir():
            print("rife model not found")
            sys.exit(2)
        frames = sorted(source.iterdir())
        for k in range(count):
            at = frames[min(k * len(frames) // count, len(frames) - 1)]
            shutil.copyfile(at, target / (value(args, "-f") % (k + 1)))
    elif name == "realesrgan-ncnn-vulkan.exe":
        source, target = Path(value(args, "-i")), Path(value(args, "-o"))
        if not Path("models", value(args, "-n") + ".param").is_file():
            print("model not found")
            sys.exit(2)
        for frame in sorted(source.iterdir()):
            larger(frame, target / frame.name, int(value(args, "-s")))
    elif name == "sd-cli.exe":
        source, pattern = Path(value(args, "--input-frames")), value(args, "-o")
        for k, frame in enumerate(sorted(source.iterdir())):
            larger(frame, Path(pattern % (k + 1)), int(value(args, "--upscale-factor")))
        print(f"[I] {len(list(source.iterdir()))}/{len(list(source.iterdir()))} images saved")
    else:
        print(f"unknown program {name}")
        sys.exit(3)


main()
