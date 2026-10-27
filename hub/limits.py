"""How much context a model gets (D22) - the layers' budgets and the tools' portions - and a model's own (stage 8.6,
D39: «для опытных»). The defaults are the same for every model, the cloud ones too: each step costs money (D22).

The model's own limits hold for the run that answers with it: the chat sets them (`use`) in the run's task, and the
context it stores and the tools it runs read them (`limit`) - a context variable, so a run never sees another's, and
the worker threads the database calls go to see the run's (asyncio.to_thread copies the context).
"""

from __future__ import annotations

from contextvars import ContextVar

# name -> (default, lowest, highest)
LIMITS: dict[str, tuple[int, int, int]] = {
    "map": (2000, 200, 200_000),  # L0: the map of every project, tokens
    "project": (3000, 300, 200_000),  # L1: an attached project's card, tokens
    "linked": (1000, 100, 50_000),  # L1: each linked project, briefly, tokens
    "live": (2000, 200, 200_000),  # L2: what is happening now, tokens
    "notes": (1000, 100, 50_000),  # L4: a project's notes for models, tokens
    "file_lines": (300, 20, 5000),  # read_file: lines when the model does not say how many
    "search_matches": (50, 5, 1000),  # search_code: matches
    "terminal_lines": (100, 10, 5000),  # a terminal's last lines
}
DEFAULTS = {name: spec[0] for name, spec in LIMITS.items()}
_current: ContextVar[dict[str, int]] = ContextVar("limits", default=DEFAULTS)


def limit(name: str) -> int:
    return _current.get().get(name, DEFAULTS[name])


def use(own: dict[str, int] | None) -> None:
    """The model's own limits for the rest of this task (none: the defaults)."""
    _current.set({**DEFAULTS, **(own or {})})
