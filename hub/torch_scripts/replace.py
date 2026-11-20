"""«Замена и дорисовка» (stage 58): Wan2.1-VACE-1.3B through diffusers' WanVACEPipeline draws an object, the
background or a frame's edges anew. AFO's hub runs it with the engine's own Python (hub/torch_engines.py) - Python
3.10, PyTorch, diffusers 0.41.0; the hub never imports it. The hub plans (hub/enhance.py: which frames, which masks,
the windows); this draws and puts together.

python -I replace.py --check
python -I replace.py --draw <plan.json>
python -I replace.py --compose <plan.json>

`--check`: PyTorch, diffusers, gguf and the card - a line of JSON, exit 3 when the card is not seen.

`--draw`: {mode: object | background | edges, prompt, negative, steps, guidance, seed, size: [w, h] (VACE's, sides of
16), grow (pixels at VACE's size), model (the repository's folder), encoder (umT5's GGUF), moments: [{frame, masks:
[...]}] (a moment's picture; its masks - white the object - taken together), windows: [[first, end, keep]] (moments;
the first `keep` of a window are the ones the window before made - kept, so the new object does not change at the
seam: 58.1), place: [x, y, w, h] (edges: where the frame lies in VACE's), out, progress}. Each window's drawn frames
come out into `out` numbered by moment from 1 - those the window before made are not written again. A window that has
nothing to draw (the object not seen in it) is its frames as they are. `progress` - a file: steps done / all of them.

`--compose`: {mode, grow, feather (pixels at the frame's size), frames: [{source, drawn, mask}], canvas: [w, h, x, y]
(edges: the new frame's size, where the source lies in it), out}: each frame the source with only the drawn area from
`drawn` (the object; all but it; the edges), the edge of the area soft - numbered from 1.

What 58.1 measured (RTX 5080, 81 frames 832 × 480): 17.8-19.3 s a step, compute-bound; PyTorch 13.18 GiB of the
card; umT5 on the processor, then gone. The text is encoded once for all windows.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

GRAY = 128  # VACE's own fill of the area it draws
NAME = "%06d.png"


def check() -> int:
    import diffusers
    import gguf  # noqa: F401 - it is there
    import torch

    seen = torch.cuda.is_available()
    print(json.dumps({"torch": torch.__version__, "diffusers": diffusers.__version__, "cuda": seen,
                      "card": torch.cuda.get_device_name(0) if seen else ""}))  # fmt: skip
    return 0 if seen else 3


def grown(mask, px: int):
    from PIL import ImageFilter

    return mask.filter(ImageFilter.MaxFilter(px * 2 + 1)) if px > 0 else mask


def mask_of(paths: list[str], size: tuple[int, int]):
    """The masks of a moment taken together, at `size`, black and white."""
    from PIL import Image, ImageChops

    found = Image.new("L", size, 0)
    for path in paths:
        found = ImageChops.lighter(found, Image.open(path).convert("L").resize(size, Image.BILINEAR))
    return found.point(lambda v: 255 if v > 127 else 0)


def told(path: str, done: int, total: int) -> None:
    with contextlib.suppress(OSError):  # the hub reading it now: the next step tells
        Path(path).write_text(f"{done} {total}", encoding="utf-8")


def draw(plan: dict) -> int:
    import torch
    from diffusers import AutoencoderKLWan, WanVACEPipeline
    from PIL import Image, ImageChops
    from transformers import UMT5EncoderModel

    mode, (w, h), grow = plan["mode"], plan["size"], int(plan["grow"])
    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    moments, windows = plan["moments"], plan["windows"]

    def frame(k: int):
        return Image.open(moments[k]["frame"]).convert("RGB")

    def area(k: int):
        """What VACE draws of moment k, at its size: white - drawn, black - kept."""
        if mode == "edges":
            x, y, iw, ih = plan["place"]
            m = Image.new("L", (w, h), 255)
            m.paste(0, (x + 2, y + 2, x + iw - 2, y + ih - 2))
            return m
        m = mask_of(moments[k]["masks"], (w, h))
        return grown(m, grow) if mode == "object" else ImageChops.invert(grown(m, 2))

    def given(k: int):
        """Moment k as VACE is given it: the area it draws filled with gray (edges: the frame in a gray canvas)."""
        if mode == "edges":
            x, y, iw, ih = plan["place"]
            canvas = Image.new("RGB", (w, h), (GRAY,) * 3)
            canvas.paste(frame(k).resize((iw, ih), Image.LANCZOS), (x, y))
            return canvas
        return Image.composite(Image.new("RGB", (w, h), (GRAY,) * 3), frame(k).resize((w, h), Image.LANCZOS), area(k))

    busy = []  # the windows that draw: (window, its areas)
    for first, end, keep in windows:
        areas = {k: area(k) for k in range(first + keep, end)}
        if mode == "object" and not any(a.getbbox() for a in areas.values()):
            for k in areas:  # the object not seen here: nothing to draw
                frame(k).save(out / (NAME % (k + 1)))
            continue
        busy.append(((first, end, keep), areas))
    total = len(busy) * int(plan["steps"])
    told(plan["progress"], 0, total)
    if not busy:
        print(json.dumps({"windows": len(windows), "drawn": 0}))
        return 0

    encoder = UMT5EncoderModel.from_pretrained(str(Path(plan["encoder"]).parent), gguf_file=Path(plan["encoder"]).name,
                                               torch_dtype=torch.bfloat16)  # fmt: skip
    vae = AutoencoderKLWan.from_pretrained(plan["model"], subfolder="vae", torch_dtype=torch.float32)
    pipe = WanVACEPipeline.from_pretrained(plan["model"], vae=vae, text_encoder=encoder, torch_dtype=torch.bfloat16)
    with torch.inference_mode():  # on the processor: umT5 does not fit the card beside the model (58.1)
        positive, negative = pipe.encode_prompt(prompt=plan["prompt"], negative_prompt=plan["negative"],
                                                do_classifier_free_guidance=True, num_videos_per_prompt=1,
                                                max_sequence_length=512, device=torch.device("cpu"),
                                                dtype=torch.bfloat16)  # fmt: skip
    pipe.text_encoder = None
    del encoder
    pipe.to("cuda")
    positive, negative = positive.to("cuda"), negative.to("cuda")
    made: dict[int, object] = {}  # the moments drawn, as they go on in the next window (composited)
    done = 0

    def step(_pipe, _i, _t, kwargs):
        nonlocal done
        done += 1
        told(plan["progress"], done, total)
        return kwargs

    for (first, end, keep), areas in busy:
        videos, masks = [], []
        for k in range(first, end):
            if k < first + keep and k in made:
                videos.append(made[k])
                masks.append(Image.new("L", (w, h), 0))
            else:
                videos.append(given(k))
                masks.append(areas.get(k) or area(k))
        while (len(videos) - 1) % 4 or len(videos) < 5:  # VACE takes 4k + 1 frames
            videos.append(videos[-1])
            masks.append(masks[-1])
        generator = torch.Generator("cuda").manual_seed(int(plan["seed"]))
        with torch.inference_mode():
            result = pipe(video=videos, mask=masks, prompt_embeds=positive, negative_prompt_embeds=negative,
                          height=h, width=w, num_frames=len(videos), num_inference_steps=int(plan["steps"]),
                          guidance_scale=float(plan["guidance"]), generator=generator, output_type="pil",
                          callback_on_step_end=step).frames[0]  # fmt: skip
        for k in range(first, end):
            drawn = result[k - first].convert("RGB").resize((w, h))
            if k >= first + keep:
                drawn.save(out / (NAME % (k + 1)))
            kept = drawn if mode == "edges" else Image.composite(drawn, frame(k).resize((w, h), Image.LANCZOS),
                                                                 masks[k - first])  # fmt: skip
            made[k] = kept
        for k in [k for k in made if k < end - keep]:
            del made[k]
    peak = torch.cuda.max_memory_allocated() / 2**30
    print(json.dumps({"windows": len(windows), "drawn": len(busy), "peak_gib": round(peak, 2)}))
    return 0


def compose(plan: dict) -> int:
    from PIL import Image, ImageChops, ImageFilter

    mode, grow, feather = plan["mode"], int(plan["grow"]), int(plan["feather"])
    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    for n, each in enumerate(plan["frames"], start=1):
        source = Image.open(each["source"]).convert("RGB")
        drawn = Image.open(each["drawn"]).convert("RGB")
        if mode == "edges":
            cw, ch, x, y = plan["canvas"]
            made = drawn.resize((cw, ch), Image.LANCZOS)
            inner = Image.new("L", source.size, 0)
            inner.paste(255, (feather, feather, source.width - feather, source.height - feather))
            made.paste(source, (x, y), inner.filter(ImageFilter.GaussianBlur(feather / 2)) if feather else None)
        else:
            m = Image.open(each["mask"]).convert("L").resize(source.size).point(lambda v: 255 if v > 127 else 0)
            m = grown(m, grow) if mode == "object" else ImageChops.invert(grown(m, 2))
            soft = m.filter(ImageFilter.GaussianBlur(feather)) if feather else m
            made = Image.composite(drawn.resize(source.size, Image.LANCZOS), source, soft)
        made.save(out / (NAME % n))
    print(json.dumps({"frames": len(plan["frames"])}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--draw")
    parser.add_argument("--compose")
    args = parser.parse_args()
    if args.check:
        return check()
    which, path = (draw, args.draw) if args.draw else (compose, args.compose)
    return which(json.loads(Path(path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
