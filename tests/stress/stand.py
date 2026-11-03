"""What the stress tests share: the table of numbers, a soft limit on each, a clock."""

from __future__ import annotations

import time

RESULTS: list[tuple[str, str, str]] = []  # (what was measured, the number, its limit)


def note(what: str, value: float, unit: str, limit: float) -> None:
    """One number for the table, held to its soft limit."""
    shown = f"{value:.0f} {unit}" if value >= 100 or unit == "шт" else f"{value:.2f} {unit}"
    RESULTS.append((what, shown.strip(), f"{limit:g} {unit}".strip()))
    assert value <= limit, f"{what}: {shown}, over the limit of {limit:g} {unit}"


class Clock:
    """`with Clock() as c: ...`, then c.s (wall seconds) and c.cpu (this process's CPU seconds, all its threads)."""

    def __enter__(self) -> Clock:
        self._wall, self._cpu = time.perf_counter(), time.process_time()
        return self

    def __exit__(self, *exc) -> None:
        self.s = time.perf_counter() - self._wall
        self.cpu = time.process_time() - self._cpu
