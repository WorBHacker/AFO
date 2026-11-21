"""«Глубина кадра» (stage 59): Depth Anything V2 Small through transformers - each frame's depth, the picture
Wan2.1-VACE draws a whole video anew by («Перерисовать всё»). AFO's hub runs it with the engine's own Python
(hub/torch_engines.py) - Python 3.10, PyTorch, transformers 5.20.2; the hub never imports it.

python -I depth.py --check
python -I depth.py --depth <plan.json>

`--check`: PyTorch, transformers and the card - a line of JSON, exit 3 when the card is not seen.

`--depth`: {model (the repository's folder), frames: [a picture's path, ...], out, progress}: each picture's depth
into `out` numbered from 1, at the picture's size - gray, near bright, each frame from its own nearest to its own
farthest (as VACE's own depth annotator gives it); `progress` - a file: frames done / all of them.

What 59.1 measured (RTX 5080, 832 × 480): 0.07 s a frame on the card, 0.3 GiB of it; 0.3-0.4 s on the processor.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

NAME = "%06d.png"


def check() -> int:
    import torch
    import transformers

    seen = torch.cuda.is_available()
    print(json.dumps({"torch": torch.__version__, "transformers": transformers.__version__, "cuda": seen,
                      "card": torch.cuda.get_device_name(0) if seen else ""}))  # fmt: skip
    return 0 if seen else 3


def told(path: str, done: int, total: int) -> None:
    with contextlib.suppress(OSError):  # the hub reading it now: the next frame tells
        Path(path).write_text(f"{done} {total}", encoding="utf-8")


def depth(plan: dict) -> int:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    frames = plan["frames"]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(plan["model"])
    model = AutoModelForDepthEstimation.from_pretrained(plan["model"]).to(device).eval()
    told(plan["progress"], 0, len(frames))
    for n, path in enumerate(frames, start=1):
        picture = Image.open(path).convert("RGB")
        with torch.inference_mode():
            found = model(**processor(images=picture, return_tensors="pt").to(device)).predicted_depth
            found = torch.nn.functional.interpolate(found[:, None], size=(picture.height, picture.width),
                                                    mode="bicubic", align_corners=False)[0, 0]  # fmt: skip
        values = found.float().cpu().numpy()
        values = (values - values.min()) / max(float(values.max() - values.min()), 1e-6) * 255.0
        gray = values.round().clip(0, 255).astype(np.uint8)
        Image.fromarray(np.repeat(gray[..., None], 3, axis=2)).save(out / (NAME % n))
        told(plan["progress"], n, len(frames))
    peak = torch.cuda.max_memory_allocated() / 2**30 if device == "cuda" else 0.0
    print(json.dumps({"frames": len(frames), "device": device, "peak_gib": round(peak, 2)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--depth")
    args = parser.parse_args()
    if args.check:
        return check()
    return depth(json.loads(Path(args.depth).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
