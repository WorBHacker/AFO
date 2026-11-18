"""Pictures and videos (D55, stage 29): what the hub asks of a picture model and how it checks the answer.

- The trial picture (29.3): a model just installed draws one small picture through llama-swap's OpenAI address
  (llama-swap starts its sd-server by the request's `model`, 29.1); it counts only when it is a picture.
- A blank picture: stable-diffusion.cpp on Vulkan can answer «success» with a flat grey one when the memory ran out
  (#1220), and a NaN gives a black one. Such a PNG compresses to almost nothing - a real one keeps half its size and
  more (29.1) - so only then are its pixels decoded and their spread looked at.
- The pictures people ask for (29.4): one job at a time, a synchronous `POST /sdapi/v1/txt2img` through llama-swap
  (it holds the model while the picture is drawn - the asynchronous API lets go of it, and a chat's request could
  swap sd-server out in the middle, 29.1); meanwhile `/upstream/<model>/sdapi/v1/progress` gives the step and a
  preview, sent to the window as events; «Стоп» unloads the model - the picture ends within a fraction of a second.
  The file goes to data/media/<year-month>/<job>.png, kept within D30's term and size (hub/retention.py).
- The ladder (29.4б): a picture that does not come out - the engine refused or fell, the answer is blank, no answer
  in time - is tried again by itself, a rung lower: a quarter of the pixels, then a lighter model (its own size in
  the same shape, then smaller). The rung it came out on is remembered for that model on this computer (a settings
  row, by the build, the card and its memory - data moved to another computer does not take it along): the next
  picture begins there. The weights' place in memory and the VAE in tiles after running out are the engine's own
  rungs (29.1). The last rung (29.4в): where the engine is the card's build, each model has a second block on the
  processor's (`<model>-cpu`, hub/model_install.py) - when nothing came out on the card, the lightest model there
  draws it, no more than CPU_PIXELS; the cloud's card comes after it, with 29.7.
- A chat's model draws too (29.6): the tool generate_image - offered to a chat begun once a picture model is here;
  the picture is drawn as any other (this computer's model, the ladder), waited for, and its PNG kept with the chat
  in data/blobs (as long as the chat, D30) - the window shows it under the call; the model hears what was drawn in
  words, never the picture. Its answer stopped - the picture is stopped.
- «По картинке» (29.6б): a source - one drawn here, one a chat holds, a PNG sent - goes to the engine's
  `/sdapi/v1/img2img` with how much to change it (denoising strength); the engine crops and resizes it to the size
  asked (29.6б live), so the size follows its shape. Only a model of several steps follows a source - SDXS's one step
  draws anew whatever the strength (live) - and so the ladder skips the others. The source is kept beside the job
  (data/media/<month>/<job>-source.png): each rung sends it again.
- A video (29.8а): Wan2.2-TI2V-5B on the same engine; sd-server draws it only in the background - `vid_gen`, then
  its job looked at (`/sdcpp/v1/jobs/<id>`), through llama-swap's `/upstream/<model>/` (it loads the model) - and
  so llama-swap holds nothing meanwhile: a local chat model's request would swap the video's engine out under it
  (29.8 live). The hub holds the card itself (hub/gpu_card.py): while a video is drawn its llama-swap provider waits
  before each answer (`GpuCard.gate`; a «loading» line says why); an install's trial holding it, a job waits;
  tuning holding it is stopped first (48.9б1: the Studio is the person's, before tuning). The
  result is an animated WebP (the engine's own, 29.8 live); its MP4 (29.8в) is made once by ffmpeg when asked for -
  ffmpeg fetched the first time (hub/ffmpeg.py) - and kept beside it.
- What a picture was drawn from - the prompt, the seed, the steps - goes into its PNG under «parameters», as
  stable-diffusion.cpp and A1111 keep it; the engine's own text is kept, but written as PNG has it: its UTF-8 in a
  tEXt (Latin-1 by the rules) becomes an iTXt, so a Russian prompt reads back.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import functools
import hashlib
import json
import logging
import random
import shutil
import struct
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from common.i18n import N_, tr
from common.picture_sizes import (
    CPU_PIXELS,
    SECONDS,
    SHAPES,
    SIDES,
    clip_of,
    fit,
    frames_for,
    half,
    moved,
    shape_of,
    shaped,
    size_for,
    trial_size,
    video_size,
)

from . import ffmpeg, hf
from .db import utc_now
from .engines import EngineError
from .gpu_card import ASKER, STUDIO, GpuCard, rank

log = logging.getLogger("hub.visual")

TRIAL_PROMPT = "a red fox sitting in fresh snow at sunrise, detailed photo"  # i18n: keep (the model reads it)
TRIAL_SIZE = 512
TRIAL_TIMEOUT_S = 900.0  # a big model on a slow processor takes minutes (29.1: FLUX.2-klein 512² - 92 s on 24 cores)
FLAT_RATIO = 0.02  # a PNG smaller than this share of its pixels' bytes is looked at closer
FLAT_SPREAD = 4.0  # brightness spread below this: one colour
SAMPLES = 64  # a grid of this many points a side is enough to see a spread
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # PNG colour types: grey, RGB, grey and alpha, RGBA
SIGNATURE = b"\x89PNG\r\n\x1a\n"
PARAMETERS = b"parameters"  # the key stable-diffusion.cpp and A1111 keep a picture's prompt, seed and steps under
TEXTS = (b"tEXt", b"iTXt", b"zTXt")


class PictureError(ValueError):
    """Not a PNG this module reads (8 bits a channel, not interlaced, no palette)."""


def _png(data: bytes) -> tuple[int, int, int, bytes]:
    if not data.startswith(SIGNATURE):
        raise PictureError("not a PNG")
    pos, header, idat = 8, None, []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None or not idat:
        raise PictureError("no header or no pixels")
    width, height, depth, colour, _compression, _filter, interlace = header
    if depth != 8 or interlace or colour not in CHANNELS:
        raise PictureError(f"depth {depth}, colour type {colour}, interlace {interlace}")
    return width, height, CHANNELS[colour], b"".join(idat)


def pixels(data: bytes) -> tuple[int, int, int, bytearray]:
    """A PNG's pixels: width, height, channels, the rows one after another (each filter undone)."""
    width, height, channels, compressed = _png(data)
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise PictureError(str(exc)) from exc
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise PictureError("rows missing")
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        at = y * (stride + 1)
        kind, line = raw[at], bytearray(raw[at + 1 : at + 1 + stride])
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif kind == 2:
            line = bytearray((a + b) & 255 for a, b in zip(line, prev, strict=True))
        elif kind == 3:
            for i in range(stride):
                line[i] = (line[i] + ((line[i - channels] if i >= channels else 0) + prev[i]) // 2) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b, c = prev[i], prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif kind != 0:
            raise PictureError(f"filter {kind}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return width, height, channels, out


def blank(data: bytes) -> bool:
    """Whether a picture is one flat colour - or no picture at all."""
    try:
        width, height, channels, compressed = _png(data)
        if len(compressed) > FLAT_RATIO * width * height * channels:
            return False  # a real picture: it does not compress to nothing
        width, height, channels, rows = pixels(data)
    except PictureError:
        return True
    colour = min(channels, 3)  # alpha is not the picture
    seen = []
    for y in range(0, height, max(1, height // SAMPLES)):
        for x in range(0, width, max(1, width // SAMPLES)):
            at = (y * width + x) * channels
            seen.append(sum(rows[at : at + colour]) / colour)
    mean = sum(seen) / len(seen)
    return (sum((v - mean) ** 2 for v in seen) / len(seen)) ** 0.5 < FLAT_SPREAD


def _chunks(data: bytes):
    """A PNG's chunks: (kind, body, where it begins, where it ends)."""
    pos = len(SIGNATURE)
    while pos + 12 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        yield kind, data[pos + 8 : pos + 8 + length], pos, pos + 12 + length
        pos += 12 + length


def _text(kind: bytes, body: bytes) -> str | None:
    """A text chunk's value when its key is «parameters», else None."""
    key, _, rest = body.partition(b"\0")
    if key != PARAMETERS or kind not in TEXTS:
        return None
    with contextlib.suppress(zlib.error, UnicodeDecodeError, IndexError):
        if kind == b"tEXt":
            try:
                return rest.decode()  # stable-diffusion.cpp writes its UTF-8 there
            except UnicodeDecodeError:
                return rest.decode("latin-1")
        if kind == b"zTXt":
            return zlib.decompress(rest[1:]).decode("latin-1")
        text = rest[2:].split(b"\0", 2)[2]  # iTXt: compressed?, method, language, translated key, the text
        return (zlib.decompress(text) if rest[0] else text).decode()
    return None


def parameters(data: bytes) -> str:
    """What a PNG says it was drawn from (its «parameters»: the prompt, the seed, the steps); "" when nothing."""
    if not data.startswith(SIGNATURE):
        return ""
    return next((t for kind, body, _a, _b in _chunks(data) if (t := _text(kind, body)) is not None), "")


def with_parameters(data: bytes, text: str) -> bytes:
    """The PNG with `text` as its «parameters», right after the header (an old one goes): a tEXt when Latin-1 holds
    it, else an iTXt in UTF-8 - Pillow and A1111 read either."""
    if not data.startswith(SIGNATURE):
        return data
    try:
        kind, body = b"tEXt", PARAMETERS + b"\0" + text.encode("latin-1")
    except UnicodeEncodeError:
        kind, body = b"iTXt", PARAMETERS + b"\0\0\0\0\0" + text.encode()  # not compressed, no language
    chunk = struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    out = bytearray(SIGNATURE)
    for found, found_body, begin, end in _chunks(data):
        if _text(found, found_body) is not None:
            continue
        out += data[begin:end]
        if found == b"IHDR":
            out += chunk
    return bytes(out)


def _refused(response: httpx.Response) -> str:
    said = " ".join(response.text.split())[:200]
    return tr("движок ответил {status}: {said}", status=response.status_code, said=said)


def _no_answer(exc: Exception) -> str:
    return tr("llama-swap не ответил ({error})", error=exc.__class__.__name__)


def _blank_words() -> str:
    return tr("картинка вышла пустой, одного цвета: движку здесь не хватило памяти или он не работает с этой "
              "видеокартой")  # fmt: skip


async def trial(base_url: str, model_id: str, *, size: int = TRIAL_SIZE,
                transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """The trial picture: {ok, seconds, size} or {ok: False, error} - in words for the window."""
    body = {"model": model_id, "prompt": TRIAL_PROMPT, "size": f"{size}x{size}", "n": 1, "response_format": "b64_json"}
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TRIAL_TIMEOUT_S, transport=transport) as http:
            response = await http.post(f"{base_url}/v1/images/generations", json=body)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _no_answer(exc)}
    seconds = round(time.monotonic() - start, 1)
    if response.status_code != 200:
        return {"ok": False, "seconds": seconds, "error": _refused(response)}
    try:
        picture = base64.b64decode(response.json()["data"][0]["b64_json"])
    except ValueError, KeyError, IndexError, TypeError:
        return {"ok": False, "seconds": seconds, "error": tr("ответ без картинки")}
    if blank(picture):
        return {"ok": False, "seconds": seconds, "error": _blank_words()}
    return {"ok": True, "seconds": seconds, "size": size}


# --- the pictures people ask for (29.4) ---

STEP_S = 0.5  # how often the progress is asked while a picture is drawn
WATCH_END_S = 15.0  # a progress watch told to stop ends within its request's timeout
PICTURE_TIMEOUT_S = 1800.0  # half an hour at most: the processor and a big model (29.1)
LIVE = ("queued", "running")
SIZES = SIDES  # a side, in pixels; a multiple of 16 (the models' latent grid)
TIERS = ("main", "light", "quality")  # the picture a person gets unless they choose: the fast main one first
RANK = {"light": 0, "main": 1, "quality": 2}  # how heavy a tier's models are: a rung lower is a lighter one
MAX_TRIES = 5  # a picture's tries at most, the first among them
STRENGTH = 0.6  # «По картинке»: how much a source is changed unless said («Заметно»)
STRENGTHS = (0.1, 0.95)  # past it nothing of the source is left
SOURCE_BYTES = 20 * 1024**2
VIDEO_STALL_S = 15 * 60.0  # (48's review) a video whose step, pass, status, place in the engine's queue stay this long
# is stopped - an hour was no limit for LTX and MiniMax on 16 GB; 48.8 measures the longest gap (a half loaded, the VAE)
VIDEO_TIMEOUT_S = 3 * 3600.0  # a model that gives no step at all (HunyuanVideo 1.5: no projection) - its whole time
CANCEL_WAIT_S = 60.0  # a cancelled job stops at its step's end (sd.cpp's sampler looks at the flag each step)
VIDEO_STEP_S = 1.0  # how often a video's job is looked at
VIDEO_CLOCK_S = 5.0  # a video whose model gives no step (48's review): the time it has taken, to the window this often
VIDEO_MODES = ("text", "picture", "frames")  # (48.5) by words, by a picture (its first frame), by the first and last
# what a video model does not do (48.5), in words - its catalog's `modes`; one installed before films by words only
MODE_WORDS = {"text": N_("{name} снимает только по картинке — выберите первый кадр"),
              "picture": N_("{name} не снимает по картинке — только по описанию"),
              "frames": N_("{name} не снимает по двум кадрам — только по первому")}  # fmt: skip
RUNGS = "visual.rungs"  # settings: {computer: {model: {"pixels": n} | {"instead": model}}} - what came out where


def trial_frame(width: int, height: int) -> bytes:
    """A first frame for the trial of a model that films only by a picture (48.5): a sky over snow, in a PNG."""
    rows = b"".join(b"\0" + bytes(c for x in range(width) for c in (
        (90 + y * 120 // height, 140 + y * 80 // height, 220) if y < height * 2 // 3 else (235, 240, 245)))
        for y in range(height))  # fmt: skip

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(
        b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")  # fmt: skip


async def trial_video(base_url: str, model_id: str, *, frames: int = 9, clip: dict[str, Any] | None = None,
                      picture: bool = False) -> dict[str, Any]:  # fmt: skip
    """A video model's trial (29.8а): a short video through vid_gen - {ok, seconds, frames} or {ok: False, error}; its
    size on the family's grid, its frames a second the family's (48.3; 9 frames - 4k+1 and 8k+1 alike); a model that
    films only by a picture - from AFO's own first frame (48.5)."""
    start = time.monotonic()
    upstream = f"{base_url}/upstream/{model_id}/sdcpp/v1"
    clip = clip_of(clip)
    width, height = trial_size(clip)
    body = {"prompt": TRIAL_PROMPT, "width": width, "height": height, "video_frames": frames, "fps": clip["fps"],
            "seed": 1, "output_format": "webp"}  # fmt: skip
    if picture:
        body["init_image"] = base64.b64encode(trial_frame(width, height)).decode()
    try:
        async with httpx.AsyncClient(timeout=TRIAL_TIMEOUT_S) as http:
            said = await http.post(f"{upstream}/vid_gen", json=body)
            if said.status_code >= 300:
                return {"ok": False, "error": _refused(said)}
            video_job = said.json().get("id", "")
            try:
                while time.monotonic() - start < TRIAL_TIMEOUT_S:
                    looked = await http.get(f"{upstream}/jobs/{video_job}")
                    state = looked.json() if looked.status_code < 300 else {"status": "failed", "error": looked.text}
                    if state.get("status") == "completed":
                        data = base64.b64decode((state.get("result") or {}).get("b64_json") or "")
                        if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                            return {"ok": False, "error": tr("ответ без видео")}
                        if not moving(data):  # (48.8 live) every frame the same: LTX's white ones passed as a trial
                            return {"ok": False, "error": tr(STILL_WORDS)}
                        return {"ok": True, "seconds": round(time.monotonic() - start, 1), "frames": frames}
                    if state.get("status") in ("failed", "cancelled"):
                        return {"ok": False, "error": _engine_error(state)}
                    await asyncio.sleep(VIDEO_STEP_S)
            except asyncio.CancelledError:  # a senior needs the card (48.9в): the engine stops too, as by ✕
                await halt(http, base_url, model_id, video_job)
                raise
            await halt(http, base_url, model_id, video_job)  # past its time: the engine stops too (48's review)
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": _no_answer(exc)}
    return {"ok": False, "error": tr("видео не готово и за {minutes} мин", minutes=round(TRIAL_TIMEOUT_S / 60))}


STILL_WORDS = N_("вместо видео — одна неподвижная картинка: все кадры вышли одинаковыми, модель снимает неверно")


SOUND_ROLE = "audio-vae"  # the file a model films sound with (52.3): LTX's audio VAE, sd-server's flag of that name
WEBM = b"\x1a\x45\xdf\xa3"  # how the engine's clip with sound begins: Matroska's header
SOUND_SUFFIX, POSTER_SUFFIX = ".webm", ".jpg"
SOUND_WORDS = 600  # the longest «what is heard»


def engine_prompt(found: dict[str, Any]) -> str:
    """What the engine is told to film (52.3): the job's description - and, filmed with sound, what is heard, a
    sentence of its own after it: LTX takes the sound from the description."""
    words = (found.get("sound_words") or "").strip() if found.get("sound") else ""
    return f"{found['prompt'].rstrip()} Sound: {words}" if words else found["prompt"]


def clip_type(path: Path) -> str | None:
    """A job's file's type by its first bytes: a picture's, a silent video's (WebP) - or a video's with sound, the
    engine's WebM (52.3); None - neither (or not there)."""
    try:
        with path.open("rb") as f:
            head = f.read(4)
    except OSError:
        return None
    return "video/webm" if head == WEBM else picture_type(path)


def moving(data: bytes) -> bool:
    """A WebP of frames, more than one (48.8 live): a model that films each frame the same - LTX's white ones, its gemma
    under flash attention - comes back as one still picture, libwebp folding the same frames into one."""
    return frames_in(data) > 1


def frames_in(data: bytes) -> int:
    """How many frames an animated WebP has - what ffmpeg numbers from 0 (51.2: the engine's file may hold fewer
    than were asked for, the same ones folded into one); 0 - a still picture, or no WebP."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return 0
    at, frames = 12, 0
    while at + 8 <= len(data):
        size = int.from_bytes(data[at + 4 : at + 8], "little")
        frames += data[at : at + 4] == b"ANMF"
        at += 8 + size + (size & 1)
    return frames


def sampling(model: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """A video's `sample_params` for sd-server: the sampler and the flow shift the model was installed with; its
    steps and guidance - the job's own when the request said them (50.4), else the install's."""
    return {"sample_method": model["sampler"], "sample_steps": int(found.get("steps") or model["steps"]),
            "guidance": {"txt_cfg": float(found.get("cfg") or model["cfg"])},
            **({"flow_shift": model["flow_shift"]} if model["flow_shift"] else {})}  # fmt: skip


def _engine_error(state: dict[str, Any]) -> str:
    """sd-server's job's error in words: its message - the job says {code, message} (async_jobs.cpp 236), and the
    object itself went into the words (48's review)."""
    error = state.get("error")
    if isinstance(error, dict):
        error = error.get("message") or error.get("code")
    return tr("движок не снял видео: {error}", error=error or state.get("status") or "")


async def halt(http: httpx.AsyncClient, base_url: str, model_id: str, video_job: str) -> None:
    """A video past its limit, stopped in the engine (48's review, finding 6): sd-server films on otherwise - the card
    let go to a chat, the next video in the engine's own queue behind it. Its job cancelled (POST …/jobs/<id>/cancel,
    routes_sdcpp.cpp 822: it stops at a step's end) - asked only of a model llama-swap runs: through /upstream one not
    running would be loaded for it; still going after CANCEL_WAIT_S, or not answering - the model unloaded."""
    upstream = f"{base_url}/upstream/{model_id}/sdcpp/v1"
    with contextlib.suppress(httpx.HTTPError, ValueError):
        running = (await http.get(f"{base_url}/running")).json().get("running") or []
        if not any(r.get("model") == model_id for r in running):
            return
        await http.post(f"{upstream}/jobs/{video_job}/cancel")
        deadline = time.monotonic() + CANCEL_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(VIDEO_STEP_S)
            looked = await http.get(f"{upstream}/jobs/{video_job}")
            if looked.status_code >= 300 or looked.json().get("status") in ("cancelled", "failed", "completed"):
                return
    with contextlib.suppress(httpx.HTTPError):
        await http.post(f"{base_url}/api/models/unload/{model_id}")


GENERATE_IMAGE = {
    "name": "generate_image",
    "description": "Draw a picture from a description with the user's local picture model; it is shown to the user in "
    "the chat. Seconds to minutes. Describe subject, setting, light, style.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to draw, in detail (English draws best)"},
            "shape": {"type": "string", "enum": list(SHAPES), "description": "square (default), wide or tall"},
            "negative": {"type": "string", "description": "What should not be in it"},
        },
        "required": ["prompt"],
    },
}
GENERATE_VIDEO = {
    "name": "generate_video",
    "description": "Film a short video from a description with the user's local video model; it is shown to the user "
    "in the chat. A minute or more. Describe the subject, its motion, the camera, light and style.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What happens, in detail (English works best)"},
            "shape": {"type": "string", "enum": list(SHAPES), "description": "wide (default), tall or square"},
            "seconds": {"type": "number", "description": "1 to 5; 2 unless said"},
        },
        "required": ["prompt"],
    },
}
INSTALL_HERE = "Установка моделей → Рисовать"  # the window's words, for a model to tell the user  # i18n: keep
PICTURE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"))


def picture_type(path: Path) -> str | None:
    """A file's picture type by its first bytes; None - not a picture (or not there)."""
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return next((kind for magic, kind in PICTURE_TYPES if head.startswith(magic)), None)


class VisualError(Exception):
    """A picture that cannot be asked for as it was; the message is for the window."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height from its header; None - not a PNG."""
    if not data.startswith(SIGNATURE) or data[12:16] != b"IHDR" or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


def follows(model: dict[str, Any]) -> bool:
    """Whether a model draws from a picture: several steps - one step draws anew (29.6б live)."""
    return int(model.get("steps") or 0) >= 2


def _row(row) -> dict[str, Any]:
    out = dict(row)
    out["tries"] = json.loads(out["tries"] or "[]")
    return out


def jobs(conn, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM visual_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def job(conn, job_id: str) -> dict[str, Any] | None:
    found = conn.execute("SELECT * FROM visual_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row(found) if found else None


def _update(conn, job_id: str, **fields: Any) -> None:
    if "tries" in fields:
        fields["tries"] = json.dumps(fields["tries"], ensure_ascii=False)
    fields["updated_at"] = utc_now()
    names = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE visual_jobs SET {names} WHERE id = ?", (*fields.values(), job_id))


def _flag(flags: list[str], name: str, default: float) -> float:
    with contextlib.suppress(ValueError, IndexError):
        return float(flags[flags.index(name) + 1])
    return default


def _text_flag(flags: list[str], name: str, default: str) -> str:
    return flags[flags.index(name) + 1] if name in flags and flags.index(name) + 1 < len(flags) else default


def installed(conn, kind: str = "image") -> list[dict[str, Any]]:
    """The picture models AFO installed (29.3) - or its video models (`kind` video, 29.8): {model_id, name, tier,
    size, steps, cfg, cpu_id - its block on the processor's build, or "", sampler, high_steps - a pair's high half's
    (--high-noise-steps, 0 - sd.cpp splits `steps` at --moe-boundary), flow_shift, clip - a video family's own (48.3;
    one installed before - TI2V-5B's), modes - how it films (48.5; one installed before - by words), sound - whether
    it films with sound (52.3): its audio VAE is among its files}."""
    out = []
    for row in conn.execute("SELECT model_id, name, plan, files FROM model_installs WHERE status = 'done' "
                            "ORDER BY name"):  # fmt: skip
        plan = json.loads(row["plan"] or "{}")
        if plan.get("engine") != "sd" or plan.get("kind", "image") != kind:
            continue
        flags = [str(f) for f in plan.get("flags") or []]
        out.append({"model_id": row["model_id"], "name": row["name"], "tier": plan.get("tier", "main"),
                    "size": int(plan.get("size") or 512), "steps": int(_flag(flags, "--steps", 4)),
                    "cfg": _flag(flags, "--cfg-scale", 1.0), "cpu_id": plan.get("cpu") or "",
                    "sampler": _text_flag(flags, "--sampling-method", "default"),
                    "high_steps": int(_flag(flags, "--high-noise-steps", 0)),  # a pair's high half's (48's review)
                    "flow_shift": _flag(flags, "--flow-shift", 0.0), "clip": clip_of(plan.get("clip")),
                    "modes": [m for m in plan.get("modes") or ["text"] if m in VIDEO_MODES],
                    "sound": any(f.get("role") == SOUND_ROLE for f in json.loads(row["files"] or "[]"))})  # fmt: skip
    return out


def on_cpu(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The models' blocks on the processor's build (29.4в), each as a model of its own: `of` - whose it is."""
    return [{**m, "model_id": m["cpu_id"], "cpu_id": "", "of": m["model_id"]} for m in models if m.get("cpu_id")]


def _rank(model: dict[str, Any]) -> int:
    return RANK.get(model["tier"], RANK["main"])


def rungs(model: dict[str, Any], width: int, height: int, models: list[dict[str, Any]],
          remembered: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """A picture's tries until one comes out (29.4б): as asked, then a quarter of the pixels, then each lighter model -
    its own size in the same shape, then smaller. Where it came out here before (`remembered`, by model) is where it
    begins: the model that did instead of this one, no more pixels than came out. The last rung is the processor's
    (29.4в): the lightest model with a block there."""
    cards, cpus = [m for m in models if not m.get("of")], on_cpu(models)
    if model.get("of"):  # its block on the processor, asked for by name
        return [_cpu_rung(model, model, width, height, remembered, "asked")]
    instead = next((m for m in [*cards, *cpus] if m["model_id"] == (remembered.get(model["model_id"]) or {}).get(
        "instead")), None)  # fmt: skip
    if instead is not None and instead.get("of") and _rank(instead) <= _rank(model):
        return [_cpu_rung(model, instead, width, height, remembered, "asked")]
    first = instead if instead is not None and _rank(instead) < _rank(model) else model
    chain = [first, *sorted((m for m in cards if _rank(m) < _rank(first)), key=_rank, reverse=True)]
    out = []
    for each in chain:
        w, h = moved(width, height, model["size"], each["size"])
        w, h = fit(w, h, int((remembered.get(each["model_id"]) or {}).get("pixels") or 0))
        out.append({"model_id": each["model_id"], "width": w, "height": h, "rung": "lighter" if out else "asked"})
        if smaller := half(w, h):
            out.append({"model_id": each["model_id"], "width": smaller[0], "height": smaller[1], "rung": "smaller"})
    out = out[:MAX_TRIES]
    last = min((m for m in cpus if _rank(m) <= _rank(first)), key=_rank, default=None)
    if last is not None:
        out.append(_cpu_rung(model, last, width, height, remembered, "cpu"))
    return out


def _cpu_rung(model: dict[str, Any], cpu: dict[str, Any], width: int, height: int,
              remembered: dict[str, dict[str, Any]], rung: str) -> dict[str, Any]:  # fmt: skip
    w, h = fit(*moved(width, height, model["size"], cpu["size"]), CPU_PIXELS)
    w, h = fit(w, h, int((remembered.get(cpu["model_id"]) or {}).get("pixels") or 0))
    return {"model_id": cpu["model_id"], "width": w, "height": h, "rung": rung}


def learned(tries: list[dict[str, Any]], asked: str) -> dict[str, dict[str, Any]]:
    """What a picture's tries teach the next one here: the model that came out, instead of those that did not and of
    the one asked for; the pixels, when it came out smaller."""
    ok = next((t for t in tries if t["ok"]), None)
    if ok is None or ok is tries[0]:
        return {}
    out = {t["model_id"]: {"instead": ok["model_id"]} for t in tries if t["model_id"] != ok["model_id"]}
    if asked != ok["model_id"]:
        out[asked] = {"instead": ok["model_id"]}
    if ok["rung"] == "smaller":
        out[ok["model_id"]] = {"pixels": ok["width"] * ok["height"]}
    return out


def computer(machine: dict[str, Any]) -> str:
    """This computer, as far as pictures go: the engine's build, the card and its memory."""
    return "|".join(str(machine.get(k) or "") for k in ("build", "card", "memory_gib"))


def remembered(conn, here: str) -> dict[str, dict[str, Any]]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RUNGS,)).fetchone()
    return (json.loads(row["value"]) if row else {}).get(here, {})


def remember(conn, here: str, rules: dict[str, dict[str, Any]]) -> None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RUNGS,)).fetchone()
    saved = json.loads(row["value"]) if row else {}
    saved[here] = {**saved.get(here, {}), **rules}
    conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET "
                 "value = excluded.value, updated_at = excluded.updated_at",
                 (RUNGS, json.dumps(saved), utc_now()))  # fmt: skip


def described(prompt: str, negative: str, seed: int, model: dict[str, Any], width: int, height: int,
              strength: float | None = None) -> str:  # fmt: skip
    """A picture's «parameters» as A1111 writes them, when the engine wrote none."""
    lines = [prompt, *([f"Negative prompt: {negative}"] if negative else [])]
    lines.append(f"Steps: {model['steps']}, CFG scale: {model['cfg']}, Seed: {seed}, Size: {width}x{height}, "
                 f"Model: {model['model_id']}" + (f", Denoising strength: {strength}" if strength else ""))  # fmt: skip
    return "\n".join(lines)


def default_model(models: list[dict[str, Any]], machine: dict[str, Any]) -> dict[str, Any] | None:
    """The model a picture is drawn with unless one is chosen: the main one where this computer draws it fast, else
    the light one, else whatever there is."""
    order = TIERS if machine.get("pictures") == "main" else ("light", "main", "quality")
    return min(models, key=lambda m: order.index(m["tier"]) if m["tier"] in order else len(order), default=None)


class Visual:
    """The hub's pictures (29.4): asked for, drawn one at a time through llama-swap, shown as they go, kept."""

    def __init__(self, db, bus, *, llama_swap_url: str, media: Path, machine: Callable[[], dict[str, Any]],
                 blobs: Path | None = None, card: GpuCard | None = None):  # fmt: skip
        self._db = db
        self._bus = bus
        self._url = llama_swap_url.rstrip("/")
        self.media = media
        self.blobs = blobs  # data/blobs: a chat's pictures, sources for «По картинке» (29.6б)
        self._machine = machine
        self._task: asyncio.Task | None = None
        self._running: str | None = None  # the job being drawn
        self._watching: asyncio.Task | None = None  # its progress
        self._watch_stop: asyncio.Event | None = None  # tells the watch to end
        self._stopping: set[str] = set()  # jobs «Стоп» was pressed for
        self.tools = ffmpeg.home()  # where ffmpeg is fetched to, for a video's MP4 (29.8в)
        self._ffmpeg = asyncio.Lock()  # one fetch, one conversion at a time
        self.card = card or GpuCard()  # the Studio's for each job, an install's trial's meanwhile (48's review)
        # a model's file not on the disk, by its name, or "": the installer's, set by the server (48's review)
        self.missing: Callable[[str], Awaitable[str]] | None = None
        self._more = False  # a job added since the worker last looked at the queue
        # «Выгрузить» (48.9г): the queue waits for the person - their next job here, or «Продолжить»
        self._held = asyncio.Event()
        self._held.set()
        self._stops: set[asyncio.Task] = set()  # a job being stopped as by ✕ (48.9г)
        self._closing = False  # the hub stops (48.10г): the worker begins no job
        # (50.7) a film is being shot: the card stays the Studio's between its scenes - `keep`; how the film is
        # stopped when the card is taken from it meanwhile («Выгрузить», its model removed)
        self._kept: Callable[[], None] | None = None

    async def start(self) -> int:
        """Pictures and videos a stopped hub left going or waiting are failed: llama-swap let them go with it - each in
        its own words (48.10г: a video «не снято», not «картинка не нарисована»)."""

        def fail(conn) -> int:
            failed = 0
            for kind, words in (("image", N_("хаб перезапускался — картинка не нарисована")),
                                ("video", N_("хаб перезапускался — видео не снято"))):  # fmt: skip
                failed += conn.execute("UPDATE visual_jobs SET status = 'failed', error = ?, updated_at = ? WHERE "
                                       "status IN ('queued', 'running') AND kind = ?",
                                       (tr(words), utc_now(), kind)).rowcount  # fmt: skip
            return failed

        return await self._db.acall(fail)

    async def stop(self) -> None:
        """The hub stops (48.10г, the review's finding 11): the queue is closed first - the worker took the next job
        meanwhile and loaded its model while the hub went away -, the job going on stops as by ✕; what waits is failed
        when the hub starts again (`start`)."""
        self._closing = True
        if self._running:
            with contextlib.suppress(Exception):
                await self.cancel(self._running)
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 10.0)

    async def models(self) -> dict[str, Any]:
        """The picture models, each with where it came out here (29.4б): `pixels` - no more than these, `instead` -
        the model that came out in its place."""
        found = await self._db.acall(installed)
        machine = await asyncio.to_thread(self._machine)
        here = await self._db.acall(lambda conn: remembered(conn, computer(machine)))
        for model in found:
            rule = here.get(model["model_id"]) or {}
            model["pixels"], model["instead"] = int(rule.get("pixels") or 0), rule.get("instead") or ""
        best = default_model(found, machine)
        videos = await self._db.acall(lambda conn: installed(conn, "video"))
        return {"models": found, "default": best["model_id"] if best else "", "videos": videos}

    @property
    def holding(self) -> bool:
        """Whether the card is held from local chats now - by a video (29.8а)."""
        return not self.card.free.is_set()

    @property
    def busy(self) -> bool:
        """Whether pictures or videos are drawn now: their worker goes through the queue (47.1)."""
        return self._task is not None and not self._task.done()

    async def video(self, spec: dict[str, Any], client: str) -> dict[str, Any]:
        """A video asked for (29.8а): {prompt, negative, model, shape, seconds, hd, seed, conversation_id}; (50.4)
        steps, cfg - its own, instead of the ones the model was installed with; (48.5) by a
        picture - from_job, from_blob or from_png: its first frame, the video's shape its - and the last frame too
        (last_job, last_blob, last_png). A family films as its `modes` say: by words, by a picture, by two frames.
        (52.3) sound - with sound, when the model films it (none said: with), sound_words - what is heard."""
        prompt = (spec.get("prompt") or "").strip()
        if not prompt:
            raise VisualError(tr("нужно описание видео"), 422)
        first = await self.source(spec)
        last = await self.source({"from_job": spec.get("last_job"), "from_blob": spec.get("last_blob"),
                                   "from_png": spec.get("last_png")})  # fmt: skip
        if last is not None and first is None:
            raise VisualError(tr("последний кадр — вместе с первым: сначала выберите первый"), 422)
        mode = "frames" if last is not None else "picture" if first is not None else "text"
        found = await self._db.acall(lambda conn: installed(conn, "video"))
        model = next((m for m in found if m["model_id"] == spec.get("model")), None) if spec.get("model") else next(
            (m for m in found if mode in m["modes"]), found[0] if found else None)  # fmt: skip
        if model is None:
            raise VisualError(tr("модели видео нет — поставьте её: «Установка моделей» → «Рисовать»"), 404)
        if mode not in model["modes"]:
            said = "text" if mode == "text" else "picture" if "picture" not in model["modes"] else "frames"
            raise VisualError(tr(MODE_WORDS[said], name=model["name"]), 422)
        clip = model["clip"]  # the family's own (48.3)
        # the first frame's shape - but a film's scenes all have the film's (50.7: `shape_fixed`)
        own = first is None or bool(spec.get("shape_fixed"))
        shape = str(spec.get("shape") or "wide") if own else shape_of(*png_size(first))
        width, height = video_size(shape, bool(spec.get("hd")), clip)
        frames = frames_for(float(spec.get("seconds") or 2.0), clip["fps"], clip["step"])
        seed = spec.get("seed")
        seed = random.randrange(2**31) if seed is None or int(seed) < 0 else int(seed)
        sound = bool(model["sound"]) and spec.get("sound") is not False
        heard = " ".join(str(spec.get("sound_words") or "").split())[:SOUND_WORDS] if sound else ""
        job_id = uuid.uuid4().hex[:12]
        kept: dict[str, str] = {}
        for key, data in (("source", first), ("last", last)):  # beside the job: its frames
            if data is not None:
                kept[key] = f"{datetime.now(UTC):%Y-%m}/{job_id}-{key}.png"
                await asyncio.to_thread(save, self.media / kept[key], data)

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO visual_jobs (id, kind, prompt, negative, model_id, width, height, seed, status, client, "
                "conversation_id, frames, fps, source, last_frame, steps, cfg, sound, sound_words) VALUES (?, "
                "'video', ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, prompt, (spec.get("negative") or "").strip(), model["model_id"], width, height, seed, client,
                 spec.get("conversation_id"), frames, clip["fps"], kept.get("source", ""), kept.get("last", ""),
                 spec.get("steps"), spec.get("cfg"), int(sound), heard),
            )  # fmt: skip

        await self._db.acall(add)
        self._more = True
        self._persons()
        await self._publish(job_id)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
        return await self._db.acall(lambda conn: job(conn, job_id))

    async def source(self, spec: dict[str, Any]) -> bytes | None:
        """«По картинке» (29.6б): the source's PNG - a job's picture, a chat's (by its sha256), one sent - or None.
        (50.3) A job that is a video gives its last frame: the next scene goes on from where that one ended."""
        if spec.get("from_job"):
            found = await self._db.acall(lambda conn: job(conn, str(spec["from_job"])))
            if found is not None and found.get("kind") == "video" and found.get("status") == "done":
                path = await self.end_frame(found)
            else:
                path = self.file(found) if found is not None else None
        elif spec.get("from_blob"):
            name = str(spec["from_blob"])
            ok = self.blobs is not None and len(name) == 64 and all(c in "0123456789abcdef" for c in name)
            path = self.blobs / name if ok else None
        elif spec.get("from_png"):
            try:
                data = base64.b64decode(str(spec["from_png"]), validate=True)
            except ValueError as exc:
                raise VisualError(tr("картинка-образец не читается"), 422) from exc
            if png_size(data) is None or len(data) > SOURCE_BYTES:
                raise VisualError(tr("образец — картинка PNG до {mb} МБ", mb=SOURCE_BYTES // 1024**2), 422)
            return data
        else:
            return None
        data = await asyncio.to_thread(path.read_bytes) if path is not None and path.is_file() else None
        if data is None:
            raise VisualError(tr("нет такой картинки"), 404)
        if png_size(data) is None:
            raise VisualError(tr("образец — картинка PNG до {mb} МБ", mb=SOURCE_BYTES // 1024**2), 422)
        return data

    async def draw(self, spec: dict[str, Any], client: str) -> dict[str, Any]:
        """A picture asked for: {prompt, negative, model, width, height, seed, conversation_id}; «По картинке»
        (29.6б): from_job, from_blob or from_png (base64), and strength."""
        prompt = (spec.get("prompt") or "").strip()
        if not prompt:
            raise VisualError(tr("нужно описание картинки"), 422)
        source = await self.source(spec)
        strength = float(spec.get("strength") or STRENGTH) if source is not None else None
        if strength is not None and not STRENGTHS[0] <= strength <= STRENGTHS[1]:
            raise VisualError(tr("насколько менять образец — от {low} до {high}", low=STRENGTHS[0],
                                 high=STRENGTHS[1]), 422)  # fmt: skip
        found = await self._db.acall(installed)
        if spec.get("model"):  # a model, or its block on the processor (29.4в)
            model = next((m for m in [*found, *on_cpu(found)] if m["model_id"] == spec["model"]), None)
            if model is None:
                raise VisualError(tr("нет модели картинок {model}", model=spec["model"]), 404)
        else:
            choice = [m for m in found if follows(m)] if source is not None else found
            model = default_model(choice, await asyncio.to_thread(self._machine))
            if model is None:
                raise VisualError(tr("модели картинок ещё нет — поставьте её: «Установка моделей» → «Рисовать»"), 404)
        if source is not None and not follows(model):
            raise VisualError(tr("по картинке рисуют модели в несколько шагов — {model} рисует за один, заново",
                                 model=model["name"]), 422)  # fmt: skip
        side = shaped(*png_size(source), model["size"]) if source is not None else (model["size"], model["size"])
        width, height = int(spec.get("width") or side[0]), int(spec.get("height") or side[1])
        for side in (width, height):
            if not SIZES[0] <= side <= SIZES[1] or side % 16:
                raise VisualError(tr("сторона картинки — от {low} до {high} точек, кратно 16", low=SIZES[0],
                                     high=SIZES[1]), 422)  # fmt: skip
        seed = spec.get("seed")
        seed = random.randrange(2**31) if seed is None or int(seed) < 0 else int(seed)
        job_id = uuid.uuid4().hex[:12]
        kept = ""
        if source is not None:  # beside the job: each rung sends it again
            kept = f"{datetime.now(UTC):%Y-%m}/{job_id}-source.png"
            await asyncio.to_thread(save, self.media / kept, source)

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO visual_jobs (id, prompt, negative, model_id, width, height, seed, status, client, "
                "conversation_id, source, strength) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (job_id, prompt, (spec.get("negative") or "").strip(), model["model_id"], width, height, seed, client,
                 spec.get("conversation_id"), kept, strength),
            )  # fmt: skip

        await self._db.acall(add)
        self._more = True
        self._persons()
        await self._publish(job_id)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
        return await self._db.acall(lambda conn: job(conn, job_id))

    def keep(self, stop: Callable[[], None], words: str) -> None:
        """A film is being shot (50.7): from now the card stays the Studio's between its scenes - given after one,
        a waiting chat's model would load, and the video model again for the next scene. `stop` - how the film is
        stopped when the card is taken from it between two scenes; `words` - what a waiting chat is told."""
        self._kept = stop
        self.card.note = words

    def unkeep(self) -> None:
        """The film's shooting is over (50.7): the card is given - by the worker when its job ends, here when none
        goes on."""
        self._kept = None
        if self._running is None:
            self.card.give(STUDIO)
        self.card.note = ""

    def _give(self) -> None:
        """A job of the Studio's is over: the card goes - unless a film keeps it for its next scene (50.7); the film
        gives it when its shooting ends (`unkeep`)."""
        if self._kept is None:
            self.card.give(STUDIO)

    def hold(self) -> None:
        """«Выгрузить» (48.9г): the queue does not take the card back by itself - the person's next job here does,
        or «Продолжить»."""
        self._held.clear()
        self.card.changed()

    def release(self) -> None:
        if not self._held.is_set():
            self._held.set()
            self.card.changed()

    @property
    def held(self) -> bool:
        """The queue waits for the person (48.9г) - the window says so at its jobs (48.9д)."""
        return not self._held.is_set()

    def _stop_job(self) -> None:
        """«Выгрузить», or its model removed (48.9г): the job going on stops as by ✕ - a video cancelled in the engine,
        a picture without a rung lower (it is «отменено», not failed). (50.7) A film that keeps the card is stopped
        too - between two scenes there is no job, and the card would stay the Studio's."""
        if self._kept is not None:
            self._kept()
        if self._running is None:
            return
        self._stopping.add(self._running)
        task = asyncio.ensure_future(self.cancel(self._running))
        self._stops.add(task)
        task.add_done_callback(self._stops.discard)

    def _persons(self) -> None:
        """A job the person asked for themselves (48.9г: the Studio, their chat) - the card given away is theirs again,
        and the queue goes on."""
        if rank(ASKER.get()) == 1:
            self.release()
            self.card.take_back()

    async def cancel(self, job_id: str) -> dict[str, Any]:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is None:
            raise VisualError(tr("нет такой картинки"), 404)
        if found["status"] == "queued" and job_id != self._running:
            self._stopping.add(job_id)  # (48.10а) the worker may take it before «отменено» is written: it looks here
            await self._set(job_id, status="cancelled", error="")
        elif found["status"] in ("queued", "running"):  # going on - or (48.10а, stage 48's review, finding 5) one the
            # worker has just taken, not yet «running»: «отменено» was written over by «running», and it went on. The
            # worker looks at `_stopping` right before the model is asked for; one asked for already is unloaded
            self._stopping.add(job_id)
            if self._watching is not None:  # its next look at the progress would load the model again
                self._watch_stop.set()
                self._watching.cancel()
            with contextlib.suppress(httpx.HTTPError):
                async with httpx.AsyncClient(timeout=30.0) as http:
                    await http.post(f"{self._url}/api/models/unload/{found['model_id']}")
        return await self._db.acall(lambda conn: job(conn, job_id))

    async def can_draw(self) -> bool:
        """Whether a picture model is here: a new chat is offered generate_image (29.6)."""
        return bool(await self._db.acall(installed))

    async def can_film(self) -> bool:
        """Whether a video model that films by words is here: a new chat is offered generate_video (29.8б), which films
        by words only - one that films only by a picture is not enough (48.10в, the review's finding 10)."""
        return any("text" in m["modes"] for m in await self._db.acall(lambda conn: installed(conn, "video")))

    async def until_done(self, job_id: str, cancel: asyncio.Event) -> dict[str, Any] | None:
        """A job waited for - by a chat's tool, by a film's shooting (50.7): the answer or the film stopped - the job
        is stopped."""
        stopping = False
        while (done := await self._db.acall(lambda conn: job(conn, job_id))) is not None and done["status"] in LIVE:
            if cancel.is_set() and not stopping:
                stopping = True
                await self.cancel(job_id)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(cancel.wait(), STEP_S)
        return done

    async def handle_video(self, conversation_id: str, call: dict[str, Any],
                           cancel: asyncio.Event) -> tuple[str, bool, bytes | None]:  # fmt: skip
        """generate_video (29.8б): the video filmed and waited for - (what the model hears, whether it is an error,
        the WebP)."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "nothing filmed: `prompt` is empty - say what happens", True, None
        if not await self.can_film():
            if await self._db.acall(lambda conn: installed(conn, "video")):  # (48.10в) by a picture only
                return ("nothing filmed: the video models here film only from a picture, not from words alone - the "
                        "user can film one from a picture in the window's Studio - say so"), True, None  # fmt: skip
            return (f"no video model is installed here: the user can install one in the window ({INSTALL_HERE}) - "
                    "say so"), True, None  # fmt: skip
        try:
            seconds = min(SECONDS[1], max(1.0, float(args.get("seconds") or 2.0)))
        except TypeError, ValueError:
            seconds = 2.0
        # silent (52.3): a chat keeps its video as a WebP - a message's picture -, and a WebM is none
        spec = {"prompt": prompt[:4000], "shape": str(args.get("shape") or "wide"), "seconds": seconds,
                "conversation_id": conversation_id, "sound": False}  # fmt: skip
        try:
            job_id = (await self.video(spec, "chat"))["id"]
        except VisualError as exc:
            return f"nothing filmed: {exc}", True, None
        done = await self.until_done(job_id, cancel)
        if done is None or done["status"] == "cancelled":
            return "not filmed: the user stopped it", True, None
        path = self.file(done) if done["status"] == "done" else None
        if path is None:
            return f"the video did not come out: {done['error'] or done['status']}", True, None
        data = await asyncio.to_thread(path.read_bytes)
        await self._chat_mp4(path, data)
        length = round(done["frames"] / done["fps"], 1)
        return (f"Filmed and shown to the user in the chat: {done['width']}×{done['height']}, {length:g} s "
                f"({done['frames']} frames at {done['fps']}/s), seed {done['seed']}, {done['seconds']} s to make. You "
                "cannot see it - do not describe it as seen; ask whether it is what they wanted."), False, (
                    data)  # fmt: skip

    async def handle(self, conversation_id: str, call: dict[str, Any],
                     cancel: asyncio.Event) -> tuple[str, bool, bytes | None]:  # fmt: skip
        """generate_image (29.6): the picture drawn with this computer's model and waited for - (what the model
        hears, whether it is an error, the PNG). The answer stopped - the picture is stopped."""
        args = call["input"] if isinstance(call["input"], dict) else {}
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return "nothing drawn: `prompt` is empty - say what to draw", True, None
        found = await self._db.acall(installed)
        model = default_model(found, await asyncio.to_thread(self._machine))
        if model is None:
            return (f"no picture model is installed here: the user can install one in the window ({INSTALL_HERE}) - "
                    "say so"), True, None  # fmt: skip
        width, height = size_for(str(args.get("shape") or "square"), model["size"])
        spec = {"prompt": prompt[:4000], "negative": str(args.get("negative") or "")[:2000], "width": width,
                "height": height, "conversation_id": conversation_id}  # fmt: skip
        try:
            job_id = (await self.draw(spec, "chat"))["id"]
        except VisualError as exc:
            return f"nothing drawn: {exc}", True, None
        done = await self.until_done(job_id, cancel)
        if done is None or done["status"] == "cancelled":
            return "not drawn: the user stopped it", True, None
        path = self.file(done) if done["status"] == "done" else None
        if path is None:
            return f"the picture did not come out: {done['error'] or done['status']}", True, None
        data = await asyncio.to_thread(path.read_bytes)
        name = next((m["name"] for m in found if m["model_id"] in (done["model_id"], done["model_id"][:-4])),
                    done["model_id"])  # fmt: skip
        return (f"Drawn and shown to the user in the chat: {done['width']}×{done['height']}, {name}, seed "
                f"{done['seed']}, {done['seconds']} s. You cannot see the picture - do not describe it as seen; ask "
                "whether it is what they wanted."), False, data  # fmt: skip

    async def mp4(self, found: dict[str, Any]) -> Path:
        """A video's MP4 (29.8в): made once with ffmpeg - fetched the first time - and kept beside its WebP; since
        48.12.8 right after the video is filmed, here only an older one's. The window plays it too (48.12): Qt's
        FFmpeg reads the WebP but cannot go to a frame of it."""
        if found.get("kind") != "video":
            raise VisualError(tr("это не видео — MP4 бывает только у видео"), 409)
        source = self.file(found)
        if source is None:
            raise VisualError(tr("видео ещё нет"), 404)
        return await self._mp4_of(source, source.with_suffix(".mp4"))

    async def blob_mp4(self, blobs: Path, name: str) -> Path:
        """The MP4 of a video a chat holds (48.12: its card plays it as the Studio does): made once, kept beside the
        chat's copy (data/blobs/<sha256>.mp4 - no message names it: it goes with the old files and is made again)."""
        source = blobs / name
        kind = await asyncio.to_thread(picture_type, source)
        if kind is None:
            raise VisualError(tr("нет такой картинки"), 404)
        if kind != "image/webp":
            raise VisualError(tr("это не видео — MP4 бывает только у видео"), 409)
        return await self._mp4_of(source, blobs / f"{name}.mp4")

    async def _mp4_now(self, source: Path) -> Path | None:
        """The MP4 of a video just filmed (48.12.8), with an ffmpeg already here: the first one is fetched (~86 MB)
        when a video is first shown - the window asks -, not before a chat's answer goes on. A clip with sound
        (52.3) has it fetched now: the window shows nothing of a WebM without its MP4 and poster."""
        if source.suffix != SOUND_SUFFIX and await asyncio.to_thread(ffmpeg.find, self.tools) is None:
            return None
        try:
            return await self._mp4_of(source, source.with_suffix(".mp4"))
        except VisualError as exc:
            log.warning("video %s: no MP4 made: %s", source.stem, exc)
            return None

    async def _chat_mp4(self, path: Path, data: bytes) -> None:
        """A chat's video's MP4 before its card is shown (48.12.8): the job's - the worker makes it once the video is
        done - copied beside the chat's copy, named as `attachments.keep` names that (its sha256). None made: the card
        asks for one, as an older video's does."""
        if self.blobs is None or (made := await self._mp4_now(path)) is None:
            return
        target = self.blobs / f"{hashlib.sha256(data).hexdigest()}.mp4"

        def copy() -> None:
            part = target.with_name(target.name + ".part")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(made, part)
            part.replace(target)

        try:
            await asyncio.to_thread(copy)
        except OSError as exc:
            log.warning("a chat's video: its MP4 not copied: %s", exc)

    async def _mp4_of(self, source: Path, target: Path) -> Path:
        """A clip's MP4 - and, of a clip with sound (52.3), its poster beside: the engine's WebM is no picture to
        the window, its first frame as a JPEG is what the lists show."""
        if source.suffix == SOUND_SUFFIX:
            poster = source.with_suffix(POSTER_SUFFIX)
            await self.with_ffmpeg(poster, lambda program: ffmpeg.poster(program, source, poster))
        return await self.with_ffmpeg(target, lambda program: ffmpeg.to_mp4(program, source, target))

    async def with_ffmpeg(self, target: Path, make: Callable[[Path], None]) -> Path:
        """`target`, made by ffmpeg once (`make`, blocking, given the program) and kept: one fetch of ffmpeg - the
        first time, ~86 MB - and one piece of its work at a time."""
        async with self._ffmpeg:
            if not target.is_file():
                await self._ffmpeg_does(make)
        return target

    async def _ffmpeg_does(self, make: Callable[[Path], Any]) -> Any:
        """ffmpeg's piece of work (`make`, blocking, given the program) - the program fetched the first time; the
        caller holds `_ffmpeg`."""
        program = await asyncio.to_thread(ffmpeg.find, self.tools)
        if program is None:
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                    build = await ffmpeg.latest(http)
                program = await asyncio.to_thread(ffmpeg.install, build, self.tools, threading.Event())
            except (EngineError, hf.HubError) as exc:
                raise VisualError(tr("ffmpeg не скачался: {error}", error=exc), 502) from exc
        try:
            return await asyncio.to_thread(make, program)
        except EngineError as exc:
            raise VisualError(str(exc), 502) from exc

    async def look(self, found: dict[str, Any], count: int, side: int) -> list[bytes]:
        """A video looked at (51.2): `count` of its frames - the first, those between evenly, the last - as JPEGs,
        the longer side `side` points at most: what a model is shown of a film's scene. A clip of fewer frames gives
        all it has; one frame alone is no video to look at."""
        source = self.file(found)
        if source is None:
            raise VisualError(tr("видео ещё нет"), 404)
        # a WebM keeps each frame asked for (52.3); a WebP may hold fewer - the same ones folded into one
        total = int(found.get("frames") or 0) if source.suffix == SOUND_SUFFIX else frames_in(
            await asyncio.to_thread(source.read_bytes))  # fmt: skip
        if total < 2:
            raise VisualError(tr("в видео меньше кадров, чем нужно для проверки"), 422)
        count = min(count, total)
        numbers = [round(k * (total - 1) / (count - 1)) for k in range(count)]

        def take(program: Path) -> list[bytes]:
            with tempfile.TemporaryDirectory() as folder:
                return [f.read_bytes() for f in ffmpeg.frames_at(program, source, Path(folder), numbers, small=side)]

        async with self._ffmpeg:
            return await self._ffmpeg_does(take)

    async def end_frame(self, found: dict[str, Any]) -> Path:
        """A video's last frame as a PNG (50.3): the director's next scene goes on from it; taken once, kept beside
        the clip (<clip>.end.png - it goes with the clip when the old files are cleaned)."""
        source = self.file(found)
        if source is None:
            raise VisualError(tr("видео ещё нет"), 404)
        target = source.with_suffix(".end.png")
        return await self.with_ffmpeg(target, lambda program: ffmpeg.last_frame(program, source, target))

    def file(self, found: dict[str, Any]) -> Path | None:
        path = self.media / found["file"] if found.get("file") else None
        return path if path is not None and path.is_file() else None

    async def _publish(self, job_id: str) -> None:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is not None:
            await self._bus.publish("visual.changed", {"job": found})

    async def _set(self, job_id: str, **fields: Any) -> None:
        await self._db.acall(lambda conn: _update(conn, job_id, **fields))
        await self._publish(job_id)

    async def _work(self) -> None:
        while True:

            def oldest(conn):
                found = conn.execute("SELECT * FROM visual_jobs WHERE status = 'queued' ORDER BY created_at "
                                     "LIMIT 1").fetchone()  # fmt: skip
                return _row(found) if found else None

            if self._closing:  # the hub stops (48.10г): no next job - it waits, and is failed when the hub starts
                return
            self._more = False
            found = await self._db.acall(oldest)
            if found is None:
                if self._more:  # one asked for while the queue was looked at: draw() saw this still going
                    continue
                self._stopping.clear()  # ✕ said in the queue (48.10а): those jobs are «отменено», nothing goes on
                return
            job_id = found["id"]
            # the card is the Studio's for the job (stage 48's review, finding 2): an install's trial holding it, the
            # job waits «в очереди»; a video holds it from local chats too - before it says «running» (48's full run).
            # After «Выгрузить» the queue waits for the person (48.9г) - not taken back by a chat's message either
            # (50.7) a film's next scene: the card kept since the scene before is the Studio's still
            while self._kept is None or self.card.holder != STUDIO:
                await self._held.wait()
                await self.card.take(STUDIO, chats=found.get("kind") == "video", stop=self._stop_job,
                                     model=found["model_id"])  # fmt: skip
                if self._held.is_set():
                    break
                self.card.give(STUDIO)
            if self._closing:  # the hub began to stop while it waited for the card (48.10г): it does not begin
                self.card.give(STUDIO)
                return
            try:
                self._running = job_id  # the worker's from now (48.10а): ✕ and «Выгрузить» stop it as one going on
                found = await self._db.acall(functools.partial(job, job_id=job_id))  # «✕» while it waited for the card
                if found is None or found["status"] != "queued":
                    continue
                await (self._run_video(found) if found.get("kind") == "video" else self._run(found))
            except Exception:  # one picture's trouble must not stop the rest
                log.exception("picture %s", job_id)
                await self._set(job_id, status="failed", error=tr("внутренняя ошибка хаба — подробности в журнале"))
            finally:
                self._running = None
                self._give()
                self._stopping.discard(job_id)

    async def _run(self, found: dict[str, Any]) -> None:
        job_id = found["id"]
        models = await self._db.acall(installed)
        by_id = {m["model_id"]: m for m in [*models, *on_cpu(models)]}
        model = by_id.get(found["model_id"])
        if model is None:
            await self._set(job_id, status="failed", error=tr("модель картинок {model} удалена",
                                                               model=found["model_id"]))  # fmt: skip
            return
        here = computer(await asyncio.to_thread(self._machine))
        source = None
        if found.get("source"):  # «По картинке»: only models that follow a source are rungs (29.6б)
            path = self.media / found["source"]
            source = await asyncio.to_thread(path.read_bytes) if path.is_file() else None
            if source is None:
                await self._set(job_id, status="failed", error=tr("образец этой картинки уже удалён"))
                return
            models = [m for m in models if follows(m)]
        ladder = rungs(model, found["width"], found["height"], models, await self._db.acall(
            lambda conn: remembered(conn, here)))  # fmt: skip
        tries: list[dict[str, Any]] = []
        picture, start = None, time.monotonic()
        for rung in ladder:
            if job_id in self._stopping:
                break
            await self._set(job_id, status="running", error="", model_id=rung["model_id"], width=rung["width"],
                            height=rung["height"], tries=tries)  # fmt: skip
            if job_id in self._stopping:  # ✕ or «Выгрузить» while «running» was written (48.10а): nothing asked for
                break
            picture, error, gone, seconds = await self._try(job_id, found, rung, by_id[rung["model_id"]], source)
            if job_id in self._stopping:
                break
            tries.append({**rung, "ok": picture is not None, "error": error, "seconds": seconds})
            if picture is not None or gone:  # llama-swap is not there: no rung lower helps
                break
        seconds = round(time.monotonic() - start, 1)
        if job_id in self._stopping:
            await self._set(job_id, status="cancelled", error="", seconds=seconds, tries=tries)
            return
        if picture is None:
            await self._set(job_id, status="failed", error=tries[-1]["error"] if tries else "", seconds=seconds,
                            tries=tries)  # fmt: skip
            return
        if rules := learned(tries, model["model_id"]):
            await self._db.acall(lambda conn: remember(conn, here, rules))
        drawn = tries[-1]
        picture = with_parameters(picture, parameters(picture) or described(
            found["prompt"], found["negative"], found["seed"], by_id[drawn["model_id"]], drawn["width"],
            drawn["height"], found.get("strength") if source is not None else None))  # fmt: skip
        name = f"{datetime.now(UTC):%Y-%m}/{job_id}.png"
        await asyncio.to_thread(save, self.media / name, picture)
        await self._set(job_id, status="done", error="", file=name, seconds=seconds, tries=tries)

    async def _try(self, job_id: str, found: dict[str, Any], rung: dict[str, Any], model: dict[str, Any],
                   source: bytes | None = None) -> tuple[bytes | None, str, bool, float]:  # fmt: skip
        """One rung: (the picture or None, why not, whether llama-swap is not there at all, the seconds it took)."""
        body = {"model": rung["model_id"], "prompt": found["prompt"], "negative_prompt": found["negative"],
                "width": rung["width"], "height": rung["height"], "seed": found["seed"], "steps": model["steps"],
                "cfg_scale": model["cfg"], "batch_size": 1}  # fmt: skip
        if source is not None:  # «По картинке» (29.6б): the engine crops and resizes it to the size asked
            body |= {"init_images": [base64.b64encode(source).decode()], "denoising_strength": found["strength"]}
        start = time.monotonic()
        self._watch_stop = asyncio.Event()
        self._watching = asyncio.create_task(self._watch(job_id, rung["model_id"], self._watch_stop))
        response, error, gone = None, "", False
        try:
            async with httpx.AsyncClient(timeout=PICTURE_TIMEOUT_S) as http:
                response = await http.post(f"{self._url}/sdapi/v1/{'img2img' if source else 'txt2img'}", json=body)
        except httpx.HTTPError as exc:
            error, gone = _no_answer(exc), isinstance(exc, httpx.ConnectError)
        finally:
            self._watch_stop.set()
            self._watching.cancel()
            await asyncio.wait({self._watching}, timeout=WATCH_END_S)  # not `await`: it may outlive its cancel
            self._watching = self._watch_stop = None
        seconds = round(time.monotonic() - start, 1)
        picture = None
        if response is not None and response.status_code != 200:
            error = _refused(response)
        elif response is not None:
            try:
                picture = base64.b64decode(response.json()["images"][0])
            except ValueError, KeyError, IndexError, TypeError:
                error = tr("ответ без картинки")
        if picture is not None and blank(picture):
            picture, error = None, _blank_words()
        return picture, error, gone, seconds

    async def _run_video(self, found: dict[str, Any]) -> None:
        """A video (29.8а): vid_gen through llama-swap's /upstream (it loads the model), then its job looked at until
        it ends; the card held meanwhile. «Стоп» unloads the model: its job is gone."""
        job_id, model_id = found["id"], found["model_id"]
        model = next((m for m in await self._db.acall(lambda conn: installed(conn, "video"))
                      if m["model_id"] == model_id), None)  # fmt: skip
        if model is None:
            await self._set(job_id, status="failed", error=tr("модель видео {model} удалена", model=model_id))
            return
        # (48's review, finding 8) a file deleted by hand: sd.cpp only warns that a pair's high half did not load
        if gone := (await self.missing(model_id) if self.missing is not None else ""):
            await self._set(job_id, status="failed", error=tr(
                "нет файла {name}: он удалён с диска — удалите модель в «Установке моделей» и поставьте её снова",
                name=gone))  # fmt: skip
            return
        # (48.10б, the review's finding 9) a frame deleted from the disk while it waited: not filmed without it - one by
        # a picture by its words alone -, but said, as a picture's sample is
        for kept, words in ((found.get("source"), N_("первый кадр этого видео уже удалён")),
                            (found.get("last_frame"), N_("последний кадр этого видео уже удалён"))):  # fmt: skip
            if kept and not await asyncio.to_thread((self.media / kept).is_file):
                await self._set(job_id, status="failed", error=tr(words))
                return
        upstream = f"{self._url}/upstream/{model_id}/sdcpp/v1"
        params = sampling(model, found)
        # `preview`: sd-server says a job's step only in its preview (48's review; async_jobs.cpp 249) - `proj`, the
        # latents put into colour, no VAE
        loud = bool(found.get("sound"))  # (52.3) with sound the engine's clip is a WebM: a WebP has none
        body = {"prompt": engine_prompt(found), "negative_prompt": found["negative"], "width": found["width"],
                "height": found["height"], "video_frames": found["frames"], "fps": found["fps"], "seed": found["seed"],
                "output_format": "webm" if loud else "webp", "sample_params": params, "preview": "proj"}  # fmt: skip
        start, error, result = time.monotonic(), "", None
        steps, clock = _Steps(params["sample_steps"] + max(model["high_steps"], 0)), start
        # the card is the video's, held from local chats, since before the job says «running» (`_work`; stage 48's full
        # run: one seeing it running found the card free - the status's write awaits the database)
        try:
            if job_id not in self._stopping:  # ✕ or «Выгрузить» since the worker took it (48.10а): not «running»
                await self._set(job_id, status="running", error="")
            for key, kept in (("init_image", found.get("source")), ("end_image", found.get("last_frame"))):  # 48.5
                if kept:  # the engine crops and resizes it to the video's size; one gone since - not read, said
                    body[key] = base64.b64encode(await asyncio.to_thread((self.media / kept).read_bytes)).decode()
            async with httpx.AsyncClient(timeout=60.0) as http:
                # looked at right before its model is asked for, no `await` between (48.10а): ✕ or «Выгрузить» meanwhile
                said = None if job_id in self._stopping else await http.post(  # the model loads first
                    f"{upstream}/vid_gen", json=body, timeout=600.0)  # fmt: skip
                if said is not None and said.status_code >= 300:
                    error = _refused(said)
                elif said is not None:
                    video_job = said.json().get("id", "")
                    moved, where = start, None  # when it last moved - a step, a pass, its status, its place in line
                    while job_id not in self._stopping:
                        await asyncio.sleep(VIDEO_STEP_S)
                        if job_id in self._stopping:
                            break
                        looked = await http.get(f"{upstream}/jobs/{video_job}")
                        if looked.status_code >= 300:
                            error = _refused(looked)
                            break
                        state = looked.json()
                        if state.get("status") == "completed":
                            result = state.get("result") or {}
                            break
                        if state.get("status") in ("failed", "cancelled"):
                            error = _engine_error(state)
                            break
                        now = time.monotonic()
                        if (step := steps.seen(state.get("preview"))) is not None:  # each step once, as a picture's
                            await self._said(job_id, *step, now - start)
                        elif steps.shown is None and now - clock >= VIDEO_CLOCK_S:  # no step yet, or none at all
                            clock = now
                            await self._said(job_id, 0, 0, now - start)
                        if (state.get("status"), state.get("queue_position"), steps.shown) != where:
                            moved, where = now, (state.get("status"), state.get("queue_position"), steps.shown)
                        # (48's review, finding 6) one that moved and stopped moving; one that gives no step - its time
                        if steps.shown is not None and now - moved >= VIDEO_STALL_S:
                            error = tr("видео стоит без хода {minutes} мин — остановлено",
                                       minutes=round(VIDEO_STALL_S / 60))  # fmt: skip
                        elif steps.shown is None and now - start >= VIDEO_TIMEOUT_S:
                            error = tr("видео не готово и за {hours} ч — остановлено",
                                       hours=round(VIDEO_TIMEOUT_S / 3600))  # fmt: skip
                        if error:
                            await halt(http, self._url, model_id, video_job)  # the engine stops, then the card goes
                            break
        except (httpx.HTTPError, ValueError) as exc:
            error = _no_answer(exc)
        except OSError as exc:  # a frame kept beside the job, not read
            error = tr("кадр задания не прочитан: {error}", error=exc.strerror or exc)
        finally:
            self._give()  # the engine has stopped: the card is free before the video is kept
        seconds = round(time.monotonic() - start, 1)
        if job_id in self._stopping:
            await self._set(job_id, status="cancelled", error="", seconds=seconds)
            return
        data = None
        if result is not None:
            try:
                data = base64.b64decode(result.get("b64_json") or "")
            except ValueError:
                data = None
            if not data or (data[:4] != WEBM if loud else data[:4] != b"RIFF" or data[8:12] != b"WEBP"):
                data, error = None, tr("ответ без видео")
            elif not loud and not moving(data):  # (48.8 live) LTX's white frames: «готово», a player of one picture
                data, error = None, tr(STILL_WORDS)
        name = f"{datetime.now(UTC):%Y-%m}/{job_id}{SOUND_SUFFIX if loud else '.webp'}"
        if data is not None:
            await asyncio.to_thread(save, self.media / name, data)
            if loud and await self._still(self.media / name, int(found["frames"])):  # the same, of a WebM
                await asyncio.to_thread((self.media / name).unlink, True)
                data, error = None, tr(STILL_WORDS)
        tries = [{"model_id": model_id, "width": found["width"], "height": found["height"], "rung": "asked",
                  "ok": data is not None, "error": error, "seconds": seconds}]  # fmt: skip
        if data is None:
            await self._set(job_id, status="failed", error=error, seconds=seconds, tries=tries)
            return
        if loud:  # (52.3) before «готово»: the window shows nothing of a WebM itself - its poster, its MP4
            await self._mp4_now(self.media / name)
        await self._set(job_id, status="done", error="", file=name, seconds=seconds, tries=tries)
        # its MP4 at once (48.12.8), not when it is first shown: the window played the WebP until then, then the MP4
        # from its start - the clip began twice. The window, asking meanwhile, waits for this one (`_ffmpeg`)
        if not loud:
            await self._mp4_now(self.media / name)

    async def _still(self, clip: Path, frames: int) -> bool:
        """Whether a clip with sound shows one picture all along (52.3; a WebP says it by itself, `moving`): three
        of its frames compared by ffmpeg - fetched the first time. Not to be told - no ffmpeg, a clip it does not
        read - is not «still»: the clip is kept, the window says what is wrong with it."""
        try:
            async with self._ffmpeg:
                return bool(await self._ffmpeg_does(lambda program: ffmpeg.still(program, clip, frames)))
        except VisualError as exc:
            log.warning("video %s: not told whether it moves: %s", clip.stem, exc)
            return False

    async def _said(self, job_id: str, step: int, steps: int, seconds: float) -> None:
        """A video's step to the window - or, with none (`steps` 0), the time it has taken (48's review)."""
        await self._bus.publish("visual.progress", {"id": job_id, "step": step, "steps": steps, "preview": "",
                                                    "seconds": round(seconds, 1)}, persist=False)  # fmt: skip

    async def _watch(self, job_id: str, model_id: str, stop: asyncio.Event) -> None:
        """The picture's step and preview to the window while it is drawn - until `stop`: a cancel alone is not
        enough, httpx (anyio under it) can turn one that comes mid-request into an HTTPError, which this lets pass;
        the watch then went on for ever, and the picture waited for it (2026-11-07, one test run in five)."""
        async with httpx.AsyncClient(timeout=10.0) as http:
            while not stop.is_set():
                await asyncio.sleep(STEP_S)
                if stop.is_set():
                    return
                with contextlib.suppress(httpx.HTTPError, ValueError):
                    said = (await http.get(f"{self._url}/upstream/{model_id}/sdapi/v1/progress")).json()
                    state = said.get("state") or {}
                    if state.get("sampling_steps"):
                        await self._bus.publish("visual.progress", {
                            "id": job_id, "step": state.get("sampling_step") or 0, "steps": state["sampling_steps"],
                            "preview": said.get("current_image") or ""}, persist=False)  # fmt: skip


class _Steps:
    """A video's step through its passes (48's review): sd-server's job says each pass's own (`preview`: pass, step,
    total_steps) - a pair's high half first, then the low one; one count across them, out of `whole` (--steps and
    --high-noise-steps: sd.cpp's total_steps)."""

    def __init__(self, whole: int) -> None:
        self.whole = whole
        self.passes: dict[int, int] = {}  # each pass seen -> its steps
        self.shown: tuple[int, int] | None = None  # the step said last, out of how many

    def seen(self, preview: Any) -> tuple[int, int] | None:
        """The step now and out of how many - when it is not the one said last."""
        if not isinstance(preview, dict) or not preview.get("total_steps"):
            return None
        now = int(preview.get("pass") or 1)
        self.passes[now] = int(preview["total_steps"])
        before = sum(total for each, total in self.passes.items() if each < now)
        step = (before + int(preview.get("step") or 0), max(self.whole, before + self.passes[now]))
        if step == self.shown:
            return None
        self.shown = step
        return step


def save(path: Path, data: bytes) -> None:
    """A file of the Studio's written whole: beside under another name, then put in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)
