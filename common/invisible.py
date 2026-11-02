"""Characters a person does not see (the audit, D46; «Trojan Source»): direction marks that reorder what is shown and
zero-width ones that hide inside a word. Code that holds them can read differently from what it does - a person
approving a model's edit is warned, and each one is shown as ⟦U+202E⟧. A byte order mark at the very start of a text
is the file's, not hidden.
"""

from __future__ import annotations

import re

HIDDEN = re.compile("[؜​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def _hidden(text: str) -> list[re.Match[str]]:
    return [m for m in HIDDEN.finditer(text) if not (m.start() == 0 and m.group() == "﻿")]


def code(char: str) -> str:
    return f"U+{ord(char):04X}"


def lines_with(text: str) -> list[int]:
    """The numbers (from 1) of the lines that hold one."""
    return sorted({text.count("\n", 0, m.start()) + 1 for m in _hidden(text)})


def reveal(text: str) -> str:
    """Each one made visible: ⟦U+202E⟧."""
    found = {m.start() for m in _hidden(text)}
    return "".join(f"⟦{code(c)}⟧" if i in found else c for i, c in enumerate(text)) if found else text


def warning(text: str) -> str:
    """What a person reads above an approval, or "" when there is none."""
    found = _hidden(text)
    if not found:
        return ""
    counts: dict[str, int] = {}
    for m in found:
        counts[code(m.group())] = counts.get(code(m.group()), 0) + 1
    listed = ", ".join(f"{c} ×{n}" if n > 1 else c for c, n in counts.items())
    return (f"Невидимые символы: {listed} — показаны как ⟦U+…⟧. Они могут заставить код выглядеть не так, как он "
            "работает: проверьте эти места.")  # fmt: skip


def summary(text: str) -> str:
    """A few words for a file's header: «невидимые символы: 3 (строки 4, 9)», or ""."""
    found = _hidden(text)
    if not found:
        return ""
    lines = lines_with(text)
    where = ", ".join(map(str, lines[:5])) + ("…" if len(lines) > 5 else "")
    return f"⚠ невидимые символы: {len(found)} ({'строка' if len(lines) == 1 else 'строки'} {where})"
