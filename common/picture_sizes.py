"""A picture's size on the ladder (D55, 29.4б) - the hub draws by it, the window shows by it: a smaller size, another
model's size, as many pixels as came out here; the shape kept, each side on the models' grid."""

from __future__ import annotations

import math

SIDES = (256, 2048)  # a side, in pixels
GRID = 16  # FLUX's latent grid (8 × 2); SD's 8 divides it


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
