"""A JSON file AFO keeps in its data folder so as not to find the same things out at every start (stage 35, D58):
`computer.json` - which computer this is, `hf_answers.json` - what Hugging Face said. An update leaves the data folder
alone. Only a memory: a file that is not there or cannot be read is an empty one, never a reason to fail, and deleting
it is safe.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read(path: Path) -> dict[str, Any]:
    """The file's object; {} when it is not there or not one."""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def write(path: Path, data: dict[str, Any]) -> None:
    """The file, whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
    os.replace(partial, path)
