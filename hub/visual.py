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
- What a picture was drawn from - the prompt, the seed, the steps - goes into its PNG under «parameters», as
  stable-diffusion.cpp and A1111 keep it; the engine's own text is kept, but written as PNG has it: its UTF-8 in a
  tEXt (Latin-1 by the rules) becomes an iTXt, so a Russian prompt reads back.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import random
import struct
import time
import uuid
import zlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from common.i18n import tr
from common.picture_sizes import CPU_PIXELS, SHAPES, SIDES, fit, half, moved, size_for

from .db import utc_now

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
RUNGS = "visual.rungs"  # settings: {computer: {model: {"pixels": n} | {"instead": model}}} - what came out where


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


def installed(conn) -> list[dict[str, Any]]:
    """The picture models AFO installed (29.3): {model_id, name, tier, size, steps, cfg, cpu_id - its block on the
    processor's build, or ""}."""
    out = []
    for row in conn.execute("SELECT model_id, name, plan FROM model_installs WHERE status = 'done' ORDER BY name"):
        plan = json.loads(row["plan"] or "{}")
        if plan.get("engine") != "sd":
            continue
        flags = [str(f) for f in plan.get("flags") or []]
        out.append({"model_id": row["model_id"], "name": row["name"], "tier": plan.get("tier", "main"),
                    "size": int(plan.get("size") or 512), "steps": int(_flag(flags, "--steps", 4)),
                    "cfg": _flag(flags, "--cfg-scale", 1.0), "cpu_id": plan.get("cpu") or ""})  # fmt: skip
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


def described(prompt: str, negative: str, seed: int, model: dict[str, Any], width: int, height: int) -> str:
    """A picture's «parameters» as A1111 writes them, when the engine wrote none."""
    lines = [prompt, *([f"Negative prompt: {negative}"] if negative else [])]
    lines.append(f"Steps: {model['steps']}, CFG scale: {model['cfg']}, Seed: {seed}, Size: {width}x{height}, "
                 f"Model: {model['model_id']}")  # fmt: skip
    return "\n".join(lines)


def default_model(models: list[dict[str, Any]], machine: dict[str, Any]) -> dict[str, Any] | None:
    """The model a picture is drawn with unless one is chosen: the main one where this computer draws it fast, else
    the light one, else whatever there is."""
    order = TIERS if machine.get("pictures") == "main" else ("light", "main", "quality")
    return min(models, key=lambda m: order.index(m["tier"]) if m["tier"] in order else len(order), default=None)


class Visual:
    """The hub's pictures (29.4): asked for, drawn one at a time through llama-swap, shown as they go, kept."""

    def __init__(self, db, bus, *, llama_swap_url: str, media: Path, machine: Callable[[], dict[str, Any]]):
        self._db = db
        self._bus = bus
        self._url = llama_swap_url.rstrip("/")
        self.media = media
        self._machine = machine
        self._task: asyncio.Task | None = None
        self._running: str | None = None  # the job being drawn
        self._watching: asyncio.Task | None = None  # its progress
        self._watch_stop: asyncio.Event | None = None  # tells the watch to end
        self._stopping: set[str] = set()  # jobs «Стоп» was pressed for
        self._more = False  # a job added since the worker last looked at the queue

    async def start(self) -> int:
        """Pictures a stopped hub left going are failed: llama-swap let them go with it."""

        def fail(conn) -> int:
            return conn.execute("UPDATE visual_jobs SET status = 'failed', error = ?, updated_at = ? WHERE status IN "
                                "('queued', 'running')", (tr("хаб перезапускался — картинка не нарисована"),
                                                          utc_now())).rowcount  # fmt: skip

        return await self._db.acall(fail)

    async def stop(self) -> None:
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
        return {"models": found, "default": best["model_id"] if best else ""}

    async def draw(self, spec: dict[str, Any], client: str) -> dict[str, Any]:
        """A picture asked for: {prompt, negative, model, width, height, seed, conversation_id}."""
        prompt = (spec.get("prompt") or "").strip()
        if not prompt:
            raise VisualError(tr("нужно описание картинки"), 422)
        found = await self._db.acall(installed)
        if spec.get("model"):  # a model, or its block on the processor (29.4в)
            model = next((m for m in [*found, *on_cpu(found)] if m["model_id"] == spec["model"]), None)
            if model is None:
                raise VisualError(tr("нет модели картинок {model}", model=spec["model"]), 404)
        else:
            model = default_model(found, await asyncio.to_thread(self._machine))
            if model is None:
                raise VisualError(tr("модели картинок ещё нет — поставьте её: «Установка моделей» → «Рисовать»"), 404)
        width, height = int(spec.get("width") or model["size"]), int(spec.get("height") or model["size"])
        for side in (width, height):
            if not SIZES[0] <= side <= SIZES[1] or side % 16:
                raise VisualError(tr("сторона картинки — от {low} до {high} точек, кратно 16", low=SIZES[0],
                                     high=SIZES[1]), 422)  # fmt: skip
        seed = spec.get("seed")
        seed = random.randrange(2**31) if seed is None or int(seed) < 0 else int(seed)
        job_id = uuid.uuid4().hex[:12]

        def add(conn) -> None:
            conn.execute(
                "INSERT INTO visual_jobs (id, prompt, negative, model_id, width, height, seed, status, client, "
                "conversation_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                (job_id, prompt, (spec.get("negative") or "").strip(), model["model_id"], width, height, seed, client,
                 spec.get("conversation_id")),
            )  # fmt: skip

        await self._db.acall(add)
        self._more = True
        await self._publish(job_id)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._work())
        return await self._db.acall(lambda conn: job(conn, job_id))

    async def cancel(self, job_id: str) -> dict[str, Any]:
        found = await self._db.acall(lambda conn: job(conn, job_id))
        if found is None:
            raise VisualError(tr("нет такой картинки"), 404)
        if found["status"] == "queued":
            await self._set(job_id, status="cancelled", error="")
        elif found["status"] == "running":
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
        stopping = False
        while (done := await self._db.acall(lambda conn: job(conn, job_id))) is not None and done["status"] in LIVE:
            if cancel.is_set() and not stopping:
                stopping = True
                await self.cancel(job_id)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(cancel.wait(), STEP_S)
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

            self._more = False
            found = await self._db.acall(oldest)
            if found is None:
                if self._more:  # one asked for while the queue was looked at: draw() saw this still going
                    continue
                return
            self._running = found["id"]
            try:
                await self._run(found)
            except Exception:  # one picture's trouble must not stop the rest
                log.exception("picture %s", found["id"])
                await self._set(found["id"], status="failed", error=tr("внутренняя ошибка хаба — подробности в "
                                                                        "журнале"))  # fmt: skip
            finally:
                self._running = None
                self._stopping.discard(found["id"])

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
        ladder = rungs(model, found["width"], found["height"], models, await self._db.acall(
            lambda conn: remembered(conn, here)))  # fmt: skip
        tries: list[dict[str, Any]] = []
        picture, start = None, time.monotonic()
        for rung in ladder:
            if job_id in self._stopping:
                break
            await self._set(job_id, status="running", error="", model_id=rung["model_id"], width=rung["width"],
                            height=rung["height"], tries=tries)  # fmt: skip
            picture, error, gone, seconds = await self._try(job_id, found, rung, by_id[rung["model_id"]])
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
            drawn["height"]))  # fmt: skip
        name = f"{datetime.now(UTC):%Y-%m}/{job_id}.png"
        await asyncio.to_thread(_save, self.media / name, picture)
        await self._set(job_id, status="done", error="", file=name, seconds=seconds, tries=tries)

    async def _try(self, job_id: str, found: dict[str, Any], rung: dict[str, Any],
                   model: dict[str, Any]) -> tuple[bytes | None, str, bool, float]:  # fmt: skip
        """One rung: (the picture or None, why not, whether llama-swap is not there at all, the seconds it took)."""
        body = {"model": rung["model_id"], "prompt": found["prompt"], "negative_prompt": found["negative"],
                "width": rung["width"], "height": rung["height"], "seed": found["seed"], "steps": model["steps"],
                "cfg_scale": model["cfg"], "batch_size": 1}  # fmt: skip
        start = time.monotonic()
        self._watch_stop = asyncio.Event()
        self._watching = asyncio.create_task(self._watch(job_id, rung["model_id"], self._watch_stop))
        response, error, gone = None, "", False
        try:
            async with httpx.AsyncClient(timeout=PICTURE_TIMEOUT_S) as http:
                response = await http.post(f"{self._url}/sdapi/v1/txt2img", json=body)
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


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    part.replace(path)
