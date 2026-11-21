"""«Губы под голос» (stage 57): MuseTalk 1.5 makes the mouth of the face in a video say a voice. AFO's hub runs it
with the engine's own Python (hub/torch_engines.py) - Python 3.10, PyTorch, MuseTalk's libraries of 2024; the hub
never imports it. MuseTalk's own code (MIT, TMElyralab) is used from the folder its archive was unpacked into
(`--code`); its `scripts/inference.py` is not: it fetches by relative paths, runs ffmpeg itself, and drops the frames
where no face is found - AFO gives the frames and the voice and takes the frames back.

python -I lips.py --check --code <dir> --models <dir>
python -I lips.py --code <dir> --models <dir> --frames <dir> --voice <wav> --out <dir> --fps <n>

`--check`: PyTorch, MuseTalk's modules and the card - a line of JSON, exit 3 when the card is not seen. Else the
frames of `--frames` (000001.png...) come out into `--out` by the same names: where a face is found and the voice
goes on - the mouth made to say it; the rest - as they were. The last line: JSON {"frames", "faces", "made"}.

What the probe of stage 56 found it needs under PyTorch 2.8 and mmcv-lite, all here (`shimmed`):
- mmcv-lite has no compiled ops (`mmcv._ext`); mmdet takes them when the face's landmark model (DWPose) is built,
  but never calls them for it - a stand-in module, any op called fails in words;
- PyTorch >= 2.6 opens weights safely: DWPose's checkpoint keeps numpy arrays and mmengine's training log - only
  those types are allowed, never `weights_only=False` for it;
- the face parser's resnet18 (torchvision's own file, sha256 5c106cde...) is in the old .tar format, which is not
  opened safely at all: opened once the old way after its sum is checked, kept in today's format in the engine's
  folder (`SAFE_RESNET`);
- the face detector (S3FD) is given its file in AFO's models folder, not fetched at its first run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

NAME = "%06d.png"
RESNET = "face-parse-bisent/resnet18-5c106cde.pth"
RESNET_SHA256 = "5c106cde386e87d4033832f2996f5493238eda96ccf559d1d62760c4de0613f8"
SAFE_RESNET = "resnet18-5c106cde.safe.pth"  # in the folder it runs in (the engine's own)
S3FD = "s3fd-619a316812.pth"
DWPOSE_CONFIG = "musetalk/utils/dwpose/rtmpose-l_8xb32-270e_coco-ubody-wholebody-384x288.py"
EXTRA_MARGIN = 10  # MuseTalk 1.5's: the face's box taken lower by so many pixels - the chin
CHEEK = 90  # MuseTalk 1.5's cheek widths of the face parser
BATCH = 8
PADDING = 2  # the voice's features around a frame, before and after (MuseTalk's defaults)


def shimmed(code: Path) -> None:
    """What MuseTalk needs here before it is imported (the probe of 56): the stand-in of mmcv's compiled ops, the
    weights' numpy types allowed, its code found."""
    import importlib.machinery
    import types

    def missing(name: str):
        def op(*_args, **_kwargs):
            raise NotImplementedError(f"mmcv._ext.{name} is not here (mmcv-lite, no compiled ops)")

        return op

    def attribute(name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return missing(name)

    ext = types.ModuleType("mmcv._ext")
    ext.__spec__ = importlib.machinery.ModuleSpec("mmcv._ext", None)
    ext.__getattr__ = attribute  # type: ignore[attr-defined]
    sys.modules["mmcv._ext"] = ext

    import numpy as np
    import torch
    from mmengine.logging.history_buffer import HistoryBuffer

    kinds = {type(np.dtype(t)) for t in ("float32", "float64", "int64", "int32", "uint8", "bool")}
    torch.serialization.add_safe_globals([HistoryBuffer, np.core.multiarray._reconstruct, np.ndarray, np.dtype,
                                          np.core.multiarray.scalar, *kinds])  # fmt: skip
    sys.path.insert(0, str(code))
    import musetalk.utils  # noqa: F401 - puts its folder on the path: its face detector is imported as `face_detection`


def safe_resnet(models: Path) -> Path:
    """The face parser's resnet18 in today's format, beside the engine: made once from torchvision's own file."""
    made = Path.cwd() / SAFE_RESNET
    if made.is_file():
        return made
    import torch

    source = models / RESNET
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != RESNET_SHA256:
        raise SystemExit(f"{source.name}: not torchvision's file (sha256 {digest})")
    state = torch.load(source, map_location="cpu", weights_only=False)  # the old .tar format: only this way
    if not all(isinstance(v, torch.Tensor) for v in state.values()):
        raise SystemExit(f"{source.name}: not a plain state dict")
    part = made.with_suffix(".part")
    torch.save(state, part)
    torch.load(part, map_location="cpu", weights_only=True)  # opened safely now
    part.replace(made)
    return made


def check(code: Path, models: Path) -> int:
    shimmed(code)
    import torch
    from mmpose.apis import inference_topdown, init_model  # noqa: F401
    from musetalk.models.unet import UNet  # noqa: F401
    from musetalk.models.vae import VAE  # noqa: F401
    from musetalk.utils.audio_processor import AudioProcessor  # noqa: F401
    from musetalk.utils.blending import get_image  # noqa: F401
    from musetalk.utils.face_parsing import FaceParsing  # noqa: F401

    safe_resnet(models)
    seen = torch.cuda.is_available()
    print(json.dumps({"torch": torch.__version__, "cuda": seen, "card": torch.cuda.get_device_name(0) if seen else ""}))
    return 0 if seen else 3


def face_box(frame, pose, detector) -> tuple[int, int, int, int] | None:
    """The face's box in a frame as MuseTalk 1.5 takes it (`get_landmark_and_bbox`): from the face's landmarks -
    its width, from the nose down to the chin and as far up -, or the detector's box when those are not right; None -
    no face."""
    import numpy as np
    from mmpose.apis import inference_topdown
    from mmpose.structures import merge_data_samples

    found = detector.get_detections_for_batch(np.asarray([frame]))[0]
    if found is None:
        return None
    marks = merge_data_samples(inference_topdown(pose, frame)).pred_instances.keypoints[0][23:91].astype(np.int32)
    middle = marks[29]
    upper = max(0, int(middle[1] - (np.max(marks[:, 1]) - middle[1])))
    x1, y1, x2, y2 = int(np.min(marks[:, 0])), upper, int(np.max(marks[:, 0])), int(np.max(marks[:, 1]))
    if y2 - y1 <= 0 or x2 - x1 <= 0 or x1 < 0:
        return tuple(int(v) for v in found[:4])  # type: ignore[return-value]
    return x1, y1, x2, y2


def make(args: argparse.Namespace) -> int:
    shimmed(args.code)
    import cv2
    import face_detection.detection.sfd.sfd_detector as sfd
    import numpy as np
    import torch
    from face_detection import FaceAlignment, LandmarksType
    from mmpose.apis import init_model
    from musetalk.models.unet import PositionalEncoding, UNet
    from musetalk.models.vae import VAE
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.blending import get_image
    from musetalk.utils.face_parsing import FaceParsing
    from transformers import WhisperModel

    models, frames, out = args.models, args.frames, args.out
    out.mkdir(parents=True, exist_ok=True)
    resnet = safe_resnet(models)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    class Parsing(FaceParsing):
        def model_init(self, resnet_path=str(resnet), model_pth=str(models / "face-parse-bisent/79999_iter.pth")):
            return super().model_init(resnet_path, model_pth)

    sfd.SFDDetector.__init__.__defaults__ = (str(models / S3FD), False)  # its file, not fetched
    detector = FaceAlignment(LandmarksType._2D, flip_input=False, device=str(device))
    pose = init_model(str(args.code / DWPOSE_CONFIG), str(models / "dwpose/dw-ll_ucoco_384.pth"), device=str(device))
    vae = VAE(model_path=str(models / "sd-vae"))
    unet = UNet(unet_config=str(models / "musetalkV15/musetalk.json"), model_path=str(models / "musetalkV15/unet.pth"),
                device=device)  # fmt: skip
    pe = PositionalEncoding(d_model=384).half().to(device)
    vae.vae = vae.vae.half().to(device)
    unet.model = unet.model.half().to(device)
    dtype = unet.model.dtype
    audio = AudioProcessor(feature_extractor_path=str(models / "whisper"))
    whisper = WhisperModel.from_pretrained(str(models / "whisper")).to(device=device, dtype=dtype).eval()
    whisper.requires_grad_(False)
    parser = Parsing(left_cheek_width=CHEEK, right_cheek_width=CHEEK)
    steps = torch.tensor([0], device=device)

    names = sorted(p.name for p in frames.glob("*.png"))
    features, length = audio.get_audio_feature(str(args.voice))
    chunks = audio.get_whisper_chunk(features, device, dtype, whisper, length, fps=args.fps,
                                     audio_padding_length_left=PADDING, audio_padding_length_right=PADDING)  # fmt: skip
    boxes: dict[int, tuple[int, int, int, int]] = {}
    with torch.no_grad():
        latents = {}
        for k, name in enumerate(names):
            if k >= len(chunks):  # the voice has ended: the frames after it as they were
                break
            frame = cv2.imread(str(frames / name))
            box = face_box(frame, pose, detector)
            if box is None:
                continue
            x1, y1, x2, y2 = box
            y2 = min(y2 + EXTRA_MARGIN, frame.shape[0])
            boxes[k] = (x1, y1, x2, y2)
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents[k] = vae.get_latents_for_unet(crop)
        made = 0
        order = sorted(latents)
        for at in range(0, len(order), BATCH):
            batch = order[at : at + BATCH]
            sound = pe(torch.stack([chunks[k] for k in batch]).to(device))
            looks = torch.cat([latents[k] for k in batch], dim=0).to(dtype=dtype)
            drawn = vae.decode_latents(unet.model(looks, steps, encoder_hidden_states=sound).sample)
            for k, face in zip(batch, drawn, strict=True):
                x1, y1, x2, y2 = boxes[k]
                frame = cv2.imread(str(frames / names[k]))
                face = cv2.resize(face.astype(np.uint8), (x2 - x1, y2 - y1))
                cv2.imwrite(str(out / names[k]), get_image(frame, face, [x1, y1, x2, y2], mode="jaw", fp=parser))
                made += 1
    for name in names:  # no face, or the voice over: as it was
        if not (out / name).is_file():
            shutil.copyfile(frames / name, out / name)
    print(json.dumps({"frames": len(names), "faces": len(boxes), "made": made}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--frames", type=Path)
    parser.add_argument("--voice", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--fps", type=int, default=25)
    args = parser.parse_args()
    if args.check:
        return check(args.code, args.models)
    if not (args.frames and args.voice and args.out):
        parser.error("--frames, --voice and --out are needed")
    return make(args)


if __name__ == "__main__":
    sys.exit(main())
