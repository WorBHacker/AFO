"""«Выделение предмета» (stage 56): SAM 2.1 follows an object through a video from one point. AFO's hub runs it with
the engine's own Python (hub/torch_engines.py) - Python 3.10, PyTorch, sam2; the hub never imports it.

python -I select.py --check
python -I select.py --weights <sam2.1_hiera_large.pt> --frames <dir> --out <dir> --at <frame> --x <0..1> --y <0..1>

`--check`: PyTorch, sam2 and the card - a line of JSON, exit 3 when the card is not seen. Else the frames of
`--frames` (000001.png...) come out into `--out` by the same names: the object as it is, the rest darkened (DIM).
`--at` - the frame clicked, from 0; `--x`, `--y` - where, in shares of its width and height.

SAM holds a video's frames on the card (56.1: 244 frames of 832×480 - 4.8 GiB), so a long one is followed piece by
piece (PIECE frames): forward from the frame clicked - each piece from the mask the one before ended with -, then back
from it the same way. A piece's frames go to SAM as JPEGs named 0.jpg, 1.jpg... - the folder it reads.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

CONFIG = "configs/sam2.1/sam2.1_hiera_l.yaml"
PIECE = 120
DIM = 0.2
NAME = "%06d.png"


def check() -> int:
    import sam2  # noqa: F401 - it is there
    import torch

    seen = torch.cuda.is_available()
    print(json.dumps({"torch": torch.__version__, "cuda": seen, "card": torch.cuda.get_device_name(0) if seen else ""}))
    return 0 if seen else 3


def follow(predictor, frames: Path, first: int, end: int, start: int, prompt, reverse: bool) -> dict:
    """Frames first..end-1 of the video (from 0) followed from `start` (within the piece): `prompt` - a point
    (x, y) in pixels, or a mask. {frame: its mask, packed}."""
    import numpy as np
    from PIL import Image

    out = {}
    with tempfile.TemporaryDirectory() as folder:
        for k, n in enumerate(range(first, end)):
            Image.open(frames / (NAME % (n + 1))).convert("RGB").save(Path(folder) / f"{k}.jpg", quality=95)
        state = predictor.init_state(video_path=folder)
        if isinstance(prompt, tuple):
            predictor.add_new_points_or_box(state, frame_idx=start, obj_id=1,
                                            points=np.array([prompt], dtype=np.float32),
                                            labels=np.array([1], dtype=np.int32))  # fmt: skip
        else:
            predictor.add_new_mask(state, frame_idx=start, obj_id=1, mask=prompt)
        for k, _ids, logits in predictor.propagate_in_video(state, start_frame_idx=start, reverse=reverse):
            out[first + k] = (logits[0, 0] > 0.0).cpu().numpy()
        predictor.reset_state(state)
    return out


def select(args: argparse.Namespace) -> int:
    import numpy as np
    import torch
    from PIL import Image
    from sam2.build_sam import build_sam2_video_predictor

    frames, out = Path(args.frames), Path(args.out)
    count = len(list(frames.glob("*.png")))
    if count == 0 or not 0 <= args.at < count:
        print(f"no frame {args.at} of {count}")
        return 2
    width, height = Image.open(frames / (NAME % 1)).size
    point = (args.x * width, args.y * height)
    predictor = build_sam2_video_predictor(CONFIG, args.weights, device="cuda")
    masks: dict = {}
    with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
        first, prompt = args.at, point
        while True:  # forward from the frame clicked
            end = min(first + PIECE, count)
            masks.update(follow(predictor, frames, first, end, 0, prompt, reverse=False))
            if end == count or not masks[end - 1].any():
                break
            first, prompt = end - 1, masks[end - 1]
        end, prompt = args.at + 1, masks[args.at]
        while end > 1 and prompt.any():  # and back from it
            first = max(0, end - PIECE)
            masks.update(follow(predictor, frames, first, end, end - 1 - first, prompt, reverse=True))
            if first == 0:
                break
            end, prompt = first + 1, masks[first]
    out.mkdir(parents=True, exist_ok=True)
    shown = 0
    for n in range(count):
        frame = np.asarray(Image.open(frames / (NAME % (n + 1))).convert("RGB")).astype(np.float32)
        mask = masks.get(n)
        if mask is None or not mask.any():
            made = frame * DIM
        else:
            made = np.where(mask[..., None], frame, frame * DIM)
            shown += 1
        Image.fromarray(made.astype(np.uint8)).save(out / (NAME % (n + 1)))
    print(json.dumps({"frames": count, "shown": shown}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--weights")
    parser.add_argument("--frames")
    parser.add_argument("--out")
    parser.add_argument("--at", type=int, default=0)
    parser.add_argument("--x", type=float, default=0.5)
    parser.add_argument("--y", type=float, default=0.5)
    args = parser.parse_args()
    return check() if args.check else select(args)


if __name__ == "__main__":
    sys.exit(main())
