"""A picture's size on the ladder (D55, 29.4б) - the hub draws by it, the window shows by it: a smaller size, another
model's size, as many pixels as came out here; the shape kept, each side on the models' grid."""

from __future__ import annotations

import math

SIDES = (256, 2048)  # a side, in pixels
GRID = 16  # FLUX's latent grid (8 × 2); SD's 8 divides it
CPU_PIXELS = 512 * 512  # on the processor no more: past it a picture takes minutes (29.1: FLUX.2-klein 512² - 92 s)
# a side of the model's own size (512 or 1024) and two shapes around it; each a multiple of 64
SHAPES = {"square": (1.0, 1.0), "wide": (1.3125, 0.75), "tall": (0.75, 1.3125)}


def size_for(shape: str, side: int) -> tuple[int, int]:
    """The picture's width and height: the model's side in this shape, each a multiple of 64."""
    w, h = SHAPES.get(shape, SHAPES["square"])
    return max(64, round(side * w / 64) * 64), max(64, round(side * h / 64) * 64)


def snap(side: float) -> int:
    return min(SIDES[1], max(SIDES[0], round(side / GRID) * GRID))


def half(width: int, height: int) -> tuple[int, int] | None:
    """A quarter of the pixels; None when a side would fall under SIDES[0]."""
    if min(width, height) / 2 < SIDES[0]:
        return None
    return snap(width / 2), snap(height / 2)


def moved(width: int, height: int, size: int, to: int) -> tuple[int, int]:
    """The size in a model of side `to` that a model of side `size` had as width × height."""
    if size == to:
        return width, height
    return snap(width * to / size), snap(height * to / size)


def fit(width: int, height: int, pixels: int) -> tuple[int, int]:
    """No more than `pixels`: the size that came out here."""
    if not pixels or width * height <= pixels:
        return width, height
    scale = math.sqrt(pixels / (width * height))
    return snap(width * scale), snap(height * scale)
