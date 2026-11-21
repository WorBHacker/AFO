"""«Камера» (stage 59): ReCamMaster (KwaiVGI) films a video's 81 frames anew along one of its ten camera moves. AFO's
hub runs it with the engine's own Python (hub/torch_engines.py) - Python 3.10, PyTorch, diffusers 0.41.0, einops; the
hub never imports it.

python -I camera.py --check
python -I camera.py --camera <plan.json>

`--check`: PyTorch, diffusers, einops and the card - a line of JSON, exit 3 when the card is not seen.

`--camera`: {dit (ReCamMaster's wan_video_dit.py), flow (its flow_match.py), paths (its camera_extrinsics.json), ckpt
(step20000.ckpt), vace (the replace engine's model folder: its tokenizer and Wan VAE), encoder (umT5's GGUF), frames:
[81 paths], move (1-10), prompt, negative, steps, cfg, seed, out, progress}: the frames - each brought to 832 × 480,
covering it, its middle kept - filmed anew, into `out` numbered from 1; `progress` - a file: steps done / all.

Without the base Wan2.1-T2V-1.3B files (59.1): ReCamMaster's checkpoint is its whole DiT (their own inference loads it
strict over the base), built here in the base's shape with their two layers a block; the text is encoded by the
replace engine's umT5 (Q4_K_M GGUF, on the processor) and the frames by its Wan VAE - the same VAE, its latents
normalised as diffusers and DiffSynth both do. Their DiT and scheduler are their own files, read from where AFO put
them; the loop is their WanVideoReCamMasterPipeline.__call__ without TeaCache. Their DiT repeats the camera over a
30 × 52 grid: 832 × 480 only, 81 frames only.

What 59.1 measured (RTX 5080): 44 s a step (two passes: the words and the negative ones), 8.7 GiB of the card; the
text 58 s on the processor; 25 steps - 20 min. The VAE goes by tiles (59.5): whole, its decode of the 81 frames at the
end reserved 12.4 GiB - more than the steps, the card full at the last minute; tiled - 3.6 GiB, 8 s longer, the
frames the same (PSNR 55.8 dB to the whole way's).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import types
from pathlib import Path

NAME = "%06d.png"
FRAMES, HEIGHT, WIDTH = 81, 480, 832


def check() -> int:
    import diffusers
    import einops  # noqa: F401 - it is there
    import gguf  # noqa: F401
    import torch

    seen = torch.cuda.is_available()
    print(json.dumps({"torch": torch.__version__, "diffusers": diffusers.__version__, "cuda": seen,
                      "card": torch.cuda.get_device_name(0) if seen else ""}))  # fmt: skip
    return 0 if seen else 3


def told(path: str, done: int, total: int) -> None:
    with contextlib.suppress(OSError):  # the hub reading it now: the next step tells
        Path(path).write_text(f"{done} {total}", encoding="utf-8")


def module(name: str, path: str, drop: str = "") -> types.ModuleType:
    """One of ReCamMaster's files as a module of its own - not as a part of its package, which brings all of
    DiffSynth with it; `drop` - a line of it that is not needed here."""
    source = Path(path).read_text(encoding="utf-8")
    found = types.ModuleType(name)
    found.__file__ = path
    exec(compile(source.replace(drop, "") if drop else source, path, "exec"), found.__dict__)
    return found


def covered(picture, width: int, height: int):
    """The picture brought to width × height: larger or smaller to cover it, its middle kept."""
    from PIL import Image

    scale = max(width / picture.width, height / picture.height)
    size = (max(width, round(picture.width * scale)), max(height, round(picture.height * scale)))
    picture = picture.resize(size, Image.BICUBIC)
    x, y = (size[0] - width) // 2, (size[1] - height) // 2
    return picture.crop((x, y, x + width, y + height))


def poses(paths: str, move: int):
    """The camera of each fourth frame along the move, from the first one's place - their dataset's own reckoning."""
    import numpy as np
    import torch

    cams = json.loads(Path(paths).read_text(encoding="utf-8"))

    def matrix(text: str):  # "[a b c d] [e f g h] ..." - their file's rows
        rows = text.strip().split("] [")
        return np.array([list(map(float, r.replace("[", "").replace("]", "").split())) for r in rows])

    found = []
    for i in list(range(FRAMES))[::4]:
        c2w = matrix(cams[f"frame{i}"][f"cam{move:02d}"]).T[:, [1, 2, 0, 3]]
        c2w[:3, 1] *= -1.0
        c2w[:3, 3] /= 100
        found.append(c2w)
    first = np.linalg.inv(found[0])
    rows = [torch.as_tensor((first @ c).astype(np.float32))[:3, :] for c in found]
    return torch.stack(rows).reshape(len(rows), 12)[None].to(torch.bfloat16)  # (1, 21, 12)


def camera(plan: dict) -> int:
    import numpy as np
    import torch
    from diffusers import AutoencoderKLWan
    from diffusers.pipelines.wan.pipeline_wan import prompt_clean
    from PIL import Image
    from transformers import AutoTokenizer, UMT5EncoderModel

    out = Path(plan["out"])
    out.mkdir(parents=True, exist_ok=True)
    steps = int(plan["steps"])
    told(plan["progress"], 0, steps)
    frames = [covered(Image.open(p).convert("RGB"), WIDTH, HEIGHT) for p in plan["frames"]]
    if len(frames) != FRAMES:
        raise SystemExit(f"ReCamMaster films {FRAMES} frames, not {len(frames)}")
    dit_code = module("recam_dit", plan["dit"], "from .utils import hash_state_dict_keys")
    flow = module("recam_flow", plan["flow"])
    cam = poses(plan["paths"], int(plan["move"]))

    tokens = AutoTokenizer.from_pretrained(str(Path(plan["vace"]) / "tokenizer"))
    encoder = UMT5EncoderModel.from_pretrained(str(Path(plan["encoder"]).parent), gguf_file=Path(plan["encoder"]).name,
                                               torch_dtype=torch.bfloat16).eval()  # fmt: skip

    def words(text: str):  # as DiffSynth's WanPrompter: 512 tokens, zeros past the text
        ids = tokens([prompt_clean(text)], padding="max_length", max_length=512, truncation=True,
                     add_special_tokens=True, return_attention_mask=True, return_tensors="pt")  # fmt: skip
        with torch.inference_mode():
            found = encoder(ids.input_ids, ids.attention_mask).last_hidden_state.clone()
            found[:, int(ids.attention_mask.sum()) :] = 0
        return found

    positive, negative = words(plan["prompt"]), words(plan["negative"])
    encoder = None  # the processor's memory back: umT5 is not needed past the words

    video = torch.from_numpy(np.stack([np.asarray(f) for f in frames])).permute(3, 0, 1, 2)[None].float() / 127.5 - 1
    vae = AutoencoderKLWan.from_pretrained(plan["vace"], subfolder="vae", torch_dtype=torch.float32).to("cuda").eval()
    vae.enable_tiling()  # (59.5) by tiles: the decode reserved 12.4 GiB whole, 3.6 tiled - the frames the same
    mean = torch.tensor(vae.config.latents_mean).view(1, 16, 1, 1, 1).to("cuda")
    std = torch.tensor(vae.config.latents_std).view(1, 16, 1, 1, 1).to("cuda")
    with torch.inference_mode():
        source = ((vae.encode(video.to("cuda")).latent_dist.mode() - mean) / std).to(torch.bfloat16)
    vae.to("cpu")
    torch.cuda.empty_cache()

    dit = dit_code.WanModel(has_image_input=False, patch_size=[1, 2, 2], in_dim=16, dim=1536, ffn_dim=8960,
                            freq_dim=256, text_dim=4096, out_dim=16, num_heads=12, num_layers=30, eps=1e-6)  # fmt: skip
    for block in dit.blocks:
        block.cam_encoder = torch.nn.Linear(12, dit.dim)
        block.projector = torch.nn.Linear(dit.dim, dit.dim)
    dit.load_state_dict(torch.load(plan["ckpt"], map_location="cpu", weights_only=True), strict=True)
    dit.to("cuda", dtype=torch.bfloat16).eval()

    def model(x, timestep, context):  # their model_fn_wan_video
        t = dit.time_embedding(dit_code.sinusoidal_embedding_1d(dit.freq_dim, timestep))
        t_mod = dit.time_projection(t).unflatten(1, (6, dit.dim))
        context = dit.text_embedding(context)
        x, (f, h, w) = dit.patchify(x)
        freqs = torch.cat([dit.freqs[0][:f].view(f, 1, 1, -1).expand(f, h, w, -1),
                           dit.freqs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
                           dit.freqs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1)],
                          dim=-1).reshape(f * h * w, 1, -1).to(x.device)  # fmt: skip
        for block in dit.blocks:
            x = block(x, context, cam_on, t_mod, freqs)
        return dit.unpatchify(dit.head(x, t), (f, h, w))

    scheduler = flow.FlowMatchScheduler(shift=5, sigma_min=0.0, extra_one_step=True)
    scheduler.set_timesteps(steps, denoising_strength=1.0, shift=5.0)
    noise = torch.Generator("cpu").manual_seed(int(plan["seed"]))
    latents = torch.randn((1, 16, (FRAMES - 1) // 4 + 1, HEIGHT // 8, WIDTH // 8), generator=noise,
                          dtype=torch.float32).to("cuda", dtype=torch.bfloat16)  # fmt: skip
    cam_on = cam.to("cuda")
    positive, negative = positive.to("cuda", dtype=torch.bfloat16), negative.to("cuda", dtype=torch.bfloat16)
    made, guidance = latents.shape[2], float(plan["cfg"])
    with torch.inference_mode():
        for i, timestep in enumerate(scheduler.timesteps):
            at = timestep.unsqueeze(0).to(dtype=torch.bfloat16, device="cuda")
            both = torch.cat([latents, source], dim=2)
            found = model(both, at, positive)
            if guidance != 1.0:
                without = model(both, at, negative)
                found = without + guidance * (found - without)
            latents = scheduler.step(found[:, :, :made], scheduler.timesteps[i], both[:, :, :made])
            told(plan["progress"], i + 1, steps)
    dit.to("cpu")
    torch.cuda.empty_cache()
    vae.to("cuda")
    with torch.inference_mode():
        drawn = vae.decode(latents.float() * std + mean).sample[0]  # (3, F, H, W) in -1..1
    drawn = ((drawn.float() + 1) * 127.5).clip(0, 255).permute(1, 2, 3, 0).cpu().numpy().astype(np.uint8)
    for k in range(drawn.shape[0]):
        Image.fromarray(drawn[k]).save(out / (NAME % (k + 1)))
    peak = torch.cuda.max_memory_allocated() / 2**30
    print(json.dumps({"frames": int(drawn.shape[0]), "move": int(plan["move"]), "peak_gib": round(peak, 2)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--camera")
    args = parser.parse_args()
    if args.check:
        return check()
    return camera(json.loads(Path(args.camera).read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
