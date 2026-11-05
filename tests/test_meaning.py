"""Search by meaning without vectors (D52, step 18.3): each definition with the words around it - its name split into
words, the comment above it, its docstring, its decorators - in a full-text index (SQLite FTS5, English stems);
find_symbol with several words searches it, with one word it finds a name as before."""

import asyncio
import os

from hub import meaning
from hub.projects import Project
from hub.symbols import SymbolIndex, parse
from hub.tools import ToolContext, run_tool


def project(root):
    return Project(id="p1", name="proj", path=str(root), description="", cloud_ok=True, created_at="", updated_at="")


def test_a_name_is_words():
    assert meaning.words("requirePermission") == "require permission"
    assert meaning.words("HTTPServer") == "http server"
    assert meaning.words("_merge_one") == "merge one"
    assert meaning.words("ApprovalQueue.ask") == "approval queue ask"
    assert meaning.words("hub/api/sandbox.py") == "hub api sandbox py"


def test_what_is_said_around_a_definition():
    source = (b"# Rights come first.\n@cached\ndef require_permission(user):\n    \"\"\"Checks that the user has the\n"
              b"    permission to write.\"\"\"\n    return user.admin\n\n\ndef plain():\n    return 1\n")  # fmt: skip
    said = meaning.notes(source, parse(source, "python"), "python")
    assert said == ["Rights come first. @cached Checks that the user has the permission to write.", ""]
    js = b"/**\n * Sends the order to the shop.\n */\nexport function submit(order) {}\n"
    assert meaning.notes(js, parse(js, "javascript"), "javascript") == ["Sends the order to the shop."]
    c = b"#define LIMIT 5\nint run(void) { return 0; }\n"
    run = [s for s in parse(c, "c") if s.name == "run"]
    assert meaning.notes(c, run, "c") == [""]  # in C a #define is no comment
    long = b'def f():\n    """' + b"word " * 200 + b'"""\n'
    values = b'LIMIT = 5\nTIMEOUT = 3\n\n\ndef later():\n    """Not the constants\' words."""\n'
    assert meaning.notes(values, parse(values, "python"), "python") == ["", "", "Not the constants' words."]
    py = b'#!/usr/bin/env python\n"""Watches the window\'s thread for stalls.\n\nMore."""\n\nimport os\n'
    assert meaning.about(py, "python") == "Watches the window's thread for stalls. More."
    ts = b"// Sends orders to the shop,\n// one at a time.\n\nexport const x = 1;\n"
    assert meaning.about(ts, "typescript") == "Sends orders to the shop, one at a time."
    assert meaning.about(b"import os\n", "python") == ""
    assert len(meaning.notes(long, parse(long, "python"), "python")[0]) <= meaning.NOTE_CHARS


def code_of(repo):
    (repo / "auth.py").write_text(
        'def require_role(user, role):\n    """Checks that the user has the permission for this."""\n'
        "    return role in user.roles\n\n\ndef permission_names():\n    return []\n\n\n"
        'def send_mail(to):\n    """Sends a letter."""\n',
        encoding="utf-8",
    )
    (repo / "guard.ts").write_text("// Refuses requests without a valid token.\nexport function checkAccess(req) {}\n",
                                   encoding="utf-8")  # fmt: skip
    (repo / "stalls.py").write_text('"""Watches the window thread for stalls."""\n\nimport os\n', encoding="utf-8")


def test_several_words_find_definitions_by_what_they_say(repo):
    code_of(repo)
    p, index = project(repo.resolve()), SymbolIndex(fresh_s=0)
    found = index.find(p, "check user permission")
    names = [s["name"] for s in found["symbols"]]
    assert found["by"] == "meaning" and set(names) == {"require_role", "permission_names", "checkAccess"}
    assert names[0] == "require_role"  # all three words, in what it says of itself
    assert found["symbols"][0]["why"] == "[Checks] that the [user] has the [permission] for this."
    assert [s["name"] for s in index.find(p, "permission")["symbols"]] == ["permission_names"]  # one word: a name
    assert index.find(p, "user permission", kind="function")["symbols"][0]["name"] == "require_role"
    assert index.find(p, "the of and")["symbols"] == []  # nothing but small words
    (whole,) = index.find(p, "window stalls")["symbols"]  # a file says what it is for in its own first words
    assert (whole["path"], whole["kind"], whole["why"]) == ("stalls.py", "file", "Watches the [window] thread for "
                                                                                 "[stalls].")  # fmt: skip

    auth = repo / "auth.py"
    auth.write_text('def require_role(user, role):\n    """Grants nothing."""\n', encoding="utf-8")
    os.utime(auth, ns=(auth.stat().st_atime_ns, auth.stat().st_mtime_ns + 10_000_000))
    assert "require_role" not in [s["name"] for s in index.find(p, "user permission")["symbols"]]
    (repo / "guard.ts").unlink()
    assert index.find(p, "refuses token")["symbols"] == []  # a file gone takes its words with it
    index.forget(p.id)
    assert index.meaning.count(p.id) == 0


def test_find_symbol_with_several_words(repo):
    code_of(repo)
    ctx = ToolContext([project(repo.resolve())])
    text, error = asyncio.run(run_tool("find_symbol", {"project": "proj", "name": "check user permission"}, ctx))
    lines = text.splitlines()
    assert not error and lines[0] == ("3 definitions about 'check user permission' in proj - by their names, "
                                      "comments and docstrings, best first:")  # fmt: skip
    assert lines[1] == ("auth.py:1-3 function require_role: def require_role(user, role): — [Checks] that the [user] "
                        "has the [permission] for this.")  # fmt: skip
    whole, error = asyncio.run(run_tool("find_symbol", {"project": "proj", "name": "window stalls"}, ctx))
    assert whole.splitlines()[1] == "stalls.py (the file) — Watches the [window] thread for [stalls]."
    nothing, error = asyncio.run(run_tool("find_symbol", {"project": "proj", "name": "fly to the moon"}, ctx))
    assert not error and nothing.startswith("no definitions about 'fly to the moon' in proj - try")
