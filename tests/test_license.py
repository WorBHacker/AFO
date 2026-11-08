"""AFO's license: the GNU GPL, version 3 or later (the user's word 2026-11-04) - the same in the repository's LICENSE,
the package's metadata, the VS Code extension and the installed program's folder."""

import json
import tomllib
from pathlib import Path

from installer import build

ROOT = Path(__file__).resolve().parent.parent


def test_the_license_is_the_gpl_3_or_later_everywhere():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.split()[:4] == ["GNU", "GENERAL", "PUBLIC", "LICENSE"] and "Version 3, 29 June 2007" in text
    assert "\r" not in text and len(text.splitlines()) == 674  # the FSF's text, whole
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "GPL-3.0-or-later" and project["license-files"] == ["LICENSE"]
    extension = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    assert extension["license"] == "GPL-3.0-or-later"
    assert "LICENSE" in build.FILES  # the installed program carries it beside its code
    assert "GPL" in (ROOT / "TechDoc.md").read_text(encoding="utf-8")
