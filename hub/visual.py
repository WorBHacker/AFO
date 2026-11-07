"""Pictures and videos (D55, stage 29): what the hub asks of a picture model and how it checks the answer.

- The trial picture (29.3): a model just installed draws one small picture through llama-swap's OpenAI address
  (llama-swap starts its sd-server by the request's `model`, 29.1); it counts only when it is a picture.
- A blank picture: stable-diffusion.cpp on Vulkan can answer «success» with a flat grey one when the memory ran out
  (#1220), and a NaN gives a black one. Such a PNG compresses to almost nothing - a real one keeps half its size and
  more (29.1) - so only then are its pixels decoded and their spread looked at.
"""

from __future__ import annotations

import base64
import struct
import time
import zlib
from typing import Any

import httpx

from common.i18n import tr

TRIAL_PROMPT = "a red fox sitting in fresh snow at sunrise, detailed photo"  # i18n: keep (the model reads it)
TRIAL_SIZE = 512
TRIAL_TIMEOUT_S = 900.0  # a big model on a slow processor takes minutes (29.1: FLUX.2-klein 512² - 92 s on 24 cores)
FLAT_RATIO = 0.02  # a PNG smaller than this share of its pixels' bytes is looked at closer
FLAT_SPREAD = 4.0  # brightness spread below this: one colour
SAMPLES = 64  # a grid of this many points a side is enough to see a spread
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}  # PNG colour types: grey, RGB, grey and alpha, RGBA


class PictureError(ValueError):
    """Not a PNG this module reads (8 bits a channel, not interlaced, no palette)."""


def _png(data: bytes) -> tuple[int, int, int, bytes]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
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


async def trial(base_url: str, model_id: str, *, size: int = TRIAL_SIZE,
                transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:  # fmt: skip
    """The trial picture: {ok, seconds, size} or {ok: False, error} - in words for the window."""
    body = {"model": model_id, "prompt": TRIAL_PROMPT, "size": f"{size}x{size}", "n": 1, "response_format": "b64_json"}
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TRIAL_TIMEOUT_S, transport=transport) as http:
            response = await http.post(f"{base_url}/v1/images/generations", json=body)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": tr("llama-swap не ответил ({error})", error=exc.__class__.__name__)}
    seconds = round(time.monotonic() - start, 1)
    if response.status_code != 200:
        said = " ".join(response.text.split())[:200]
        return {"ok": False, "seconds": seconds, "error": tr("движок ответил {status}: {said}",
                                                             status=response.status_code, said=said)}  # fmt: skip
    try:
        picture = base64.b64decode(response.json()["data"][0]["b64_json"])
    except ValueError, KeyError, IndexError, TypeError:
        return {"ok": False, "seconds": seconds, "error": tr("ответ без картинки")}
    if blank(picture):
        why = tr("картинка вышла пустой, одного цвета: движку здесь не хватило памяти или он не работает с этой "
                 "видеокартой")  # fmt: skip
        return {"ok": False, "seconds": seconds, "error": why}
    return {"ok": True, "seconds": seconds, "size": size}
