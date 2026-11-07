"""Pictures and videos (D55, stage 29): a picture model's trial picture through llama-swap, and a blank picture told
from a real one - stable-diffusion.cpp on Vulkan can answer «success» with a flat grey one (#1220)."""

import asyncio
import base64
import json
import random
import struct
import zlib

import httpx
import pytest

from hub import visual


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _filtered(row: bytes, prev: bytes, kind: int, bpp: int) -> bytes:
    """A row as PNG's filter `kind` writes it (the decoder must undo each)."""
    out = bytearray(len(row))
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if kind == 0:
            pred = 0
        elif kind == 1:
            pred = a
        elif kind == 2:
            pred = b
        elif kind == 3:
            pred = (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
        out[i] = (x - pred) & 255
    return bytes([kind]) + bytes(out)


def png(width: int, height: int, pixel, *, kinds=(0,)) -> bytes:
    """An 8-bit RGB PNG; row y written with filter kinds[y % len(kinds)]."""
    rows = [bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height)]
    prev = bytes(width * 3)
    raw = b""
    for y, row in enumerate(rows):
        raw += _filtered(row, prev, kinds[y % len(kinds)], 3)
        prev = row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def gradient(x, y):
    return (x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256)


def test_a_pngs_pixels_through_each_filter():
    for kinds in ((0,), (1,), (2,), (3,), (4,), (0, 1, 2, 3, 4)):
        width, height, channels, pixels = visual.pixels(png(24, 10, gradient, kinds=kinds))
        assert (width, height, channels) == (24, 10, 3)
        assert bytes(pixels) == b"".join(bytes(c for x in range(24) for c in gradient(x, y)) for y in range(10)), kinds


def test_a_blank_picture_is_told_from_a_real_one():
    rnd = random.Random(42)
    noise = png(64, 64, lambda x, y: (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    assert not visual.blank(noise)  # a real picture keeps much of its size: not even decoded
    assert visual.blank(png(64, 64, lambda x, y: (128, 128, 128), kinds=(4,)))  # the grey «success»
    assert visual.blank(png(64, 64, lambda x, y: (0, 0, 0)))  # NaN: black
    assert not visual.blank(png(64, 64, gradient, kinds=(1, 2)))  # compresses well, but it has things in it
    assert visual.blank(b"not a png")


def _swap(answer):
    def handler(request):
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert body["model"] == "pictures" and body["size"] == "512x512" and body["prompt"] == visual.TRIAL_PROMPT
        return answer(body)

    return httpx.MockTransport(handler)


def test_the_trial_picture():
    rnd = random.Random(1)
    real = png(32, 32, lambda x, y: (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    grey = png(32, 32, lambda x, y: (128, 128, 128))

    def run(answer):
        return asyncio.run(visual.trial("http://swap", "pictures", transport=_swap(answer)))

    good = run(lambda b: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(real).decode()}]}))
    assert good["ok"] and good["size"] == 512 and good["seconds"] >= 0
    flat = run(lambda b: httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(grey).decode()}]}))
    assert not flat["ok"] and "пустой" in flat["error"]
    crashed = run(lambda b: httpx.Response(502, text="upstream command exited prematurely"))
    assert not crashed["ok"] and "exited prematurely" in crashed["error"]
    empty = run(lambda b: httpx.Response(200, json={"data": []}))
    assert not empty["ok"] and "без картинки" in empty["error"]


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
def test_the_trial_picture_without_an_answer(error):
    def handler(request):
        raise error

    result = asyncio.run(visual.trial("http://swap", "pictures", transport=httpx.MockTransport(handler)))
    assert not result["ok"] and type(error).__name__ in result["error"]
