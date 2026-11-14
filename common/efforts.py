"""The reasoning depths (D39): the hub's names, weakest first, and the nearest one a model takes (0.1.13). The hub asks
a model that depth; the window offers a model the depths it takes."""

from __future__ import annotations

from collections.abc import Iterable

EFFORTS = ("low", "medium", "high", "xhigh", "max")
_RANK = {name: n for n, name in enumerate(("minimal", *EFFORTS))}  # OpenRouter names «minimal» below «low»


def own_efforts(named: Iterable[str]) -> list[str]:
    """Of the depths a model names, the hub's, weakest first."""
    named = set(named)
    return [e for e in EFFORTS if e in named]


def nearest_effort(wanted: str, supported: Iterable[str] | None) -> str:
    """The hub's effort if the model takes it, else the strongest it takes below it (or its weakest); a model that
    names none - as asked."""
    supported = list(supported or [])
    if not supported or wanted in supported:
        return wanted
    below = [e for e in supported if e in _RANK and _RANK[e] <= _RANK.get(wanted, _RANK["medium"])]
    if below:
        return max(below, key=_RANK.__getitem__)
    return min((e for e in supported if e in _RANK), key=_RANK.__getitem__, default=wanted)
