"""The autonomous agent's memory (stage 9.3, D40, Q24): what a run notes for itself, what the hub keeps for good, and
the ways that worked before.

    working   - a run's notes (`note`): what it found and decided, read back when it needs them; they live with the run.
    long-term - facts with their source and date (`remember`): kept locally, every one visible in the window, where the
                user corrects or deletes it. Personal data - contacts, addresses, phones, documents - is kept only when
                the user approves it, and is never given to a cloud model; any other fact a cloud model gets only when
                it is marked as allowed (by default: what a cloud model told itself, it may read again).
    skills    - a finished task's plan, saved when the user agrees: the next similar goal gets it as a hint.

Search is SQLite's full text (FTS5, no embeddings - D26): the words of a query, cut to their stems and matched by their
beginnings, so «Ташкенте» finds «Ташкент».
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .db import utc_now

FACT_CHARS = 1000
NOTE_CHARS = 2000
FOUND = 8  # at most this many matches go back to a model
# what looks like personal data: an e-mail, a phone number, a card or document number, a street address
PERSONAL = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"
    r"|(?:\+\d[\d\s()-]{8,}\d)|\b\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"
    r"|\b(?:\d{4}[\s-]?){3}\d{4}\b"
    r"|\b(?:ул(?:ица)?|улиц[аы]|просп(?:ект)?|пер(?:еулок)?|дом|кв(?:артира)?|street|st\.|avenue|apt)\.?\s+\S+",
    re.IGNORECASE,
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "note",
        "description": "Write down something for yourself in this task - a finding, a decision, a value you will "
        "need later. Your notes come back with recall.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "remember",
        "description": "Keep a fact for good - for later tasks too: a preference of the user, how something in their "
        "setup works. One fact, short, true. Personal data (contacts, addresses, phones) is kept only if the user "
        "approves it. Do not keep secrets.",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "The fact, complete in itself."},
                "about": {"type": "string", "description": "What it is about, in a word or two."},
                "personal": {"type": "boolean", "description": "Whether it is personal data."},
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall",
        "description": "Search your notes of this task and the facts kept for good.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


class MemoryError_(ValueError):
    pass


def match_query(text: str) -> str:
    """Words to an FTS5 query: each cut to its stem and matched by its beginning, any of them."""
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3][:12]
    stems = {w if len(w) <= 5 else w[: max(5, len(w) - 2)] for w in words}
    return " OR ".join(f'"{s}"*' for s in sorted(stems))


def looks_personal(text: str) -> bool:
    return bool(PERSONAL.search(text))


# ---------------------------------------------------------------- working memory


def add_note(conn: sqlite3.Connection, run_id: str, text: str) -> int:
    text = " ".join(text.split())[:NOTE_CHARS]
    if not text:
        raise MemoryError_("a note needs text")
    cursor = conn.execute("INSERT INTO run_notes (conversation_id, text) VALUES (?, ?)", (run_id, text))
    return int(cursor.lastrowid)


def notes(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, text, created_at FROM run_notes WHERE conversation_id = ? ORDER BY id",
                        (run_id,)).fetchall()  # fmt: skip
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- long-term facts


def _fact(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["personal"], out["cloud_ok"] = bool(row["personal"]), bool(row["cloud_ok"])
    return out


def add_fact(conn: sqlite3.Connection, fact: str, *, about: str = "", source: str, personal: bool,
             cloud_ok: bool) -> dict[str, Any]:  # fmt: skip
    text = " ".join(fact.split())[:FACT_CHARS]
    if not text:
        raise MemoryError_("a fact needs text")
    same = conn.execute("SELECT * FROM memory_facts WHERE text = ? AND deleted = 0", (text,)).fetchone()
    if same is not None:  # known already: not twice
        return _fact(same)
    cursor = conn.execute(
        "INSERT INTO memory_facts (text, about, source, personal, cloud_ok) VALUES (?, ?, ?, ?, ?)",
        (text, " ".join(about.split())[:80], source, int(personal), int(cloud_ok and not personal)),
    )
    return _fact(conn.execute("SELECT * FROM memory_facts WHERE id = ?", (cursor.lastrowid,)).fetchone())


def facts(conn: sqlite3.Connection, query: str = "", *, cloud: bool = False, limit: int = 500) -> list[dict]:
    """The kept facts - all of them, newest first, or those a query finds, best first. `cloud`: only those a cloud
    model may read (never personal ones)."""
    allowed = " AND f.cloud_ok = 1 AND f.personal = 0" if cloud else ""
    if query.strip():
        match = match_query(query)
        if not match:
            return []
        rows = conn.execute(
            "SELECT f.* FROM memory_facts_fts JOIN memory_facts f ON f.id = memory_facts_fts.rowid"
            f" WHERE memory_facts_fts MATCH ? AND f.deleted = 0{allowed} ORDER BY bm25(memory_facts_fts) LIMIT ?",
            (match, limit),
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT f.* FROM memory_facts f WHERE f.deleted = 0{allowed} ORDER BY f.id DESC LIMIT ?",
                            (limit,)).fetchall()  # fmt: skip
    return [_fact(r) for r in rows]


def change_fact(conn: sqlite3.Connection, fact_id: int, **changes: Any) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM memory_facts WHERE id = ? AND deleted = 0", (fact_id,)).fetchone()
    if row is None:
        return None
    text = " ".join(str(changes.get("text", row["text"])).split())[:FACT_CHARS]
    if not text:
        raise MemoryError_("a fact needs text")
    personal = bool(changes.get("personal", row["personal"]))
    cloud_ok = bool(changes.get("cloud_ok", row["cloud_ok"])) and not personal
    conn.execute("UPDATE memory_facts SET text = ?, about = ?, personal = ?, cloud_ok = ?, updated_at = ? WHERE id = ?",
                 (text, str(changes.get("about", row["about"]))[:80], int(personal), int(cloud_ok), utc_now(),
                  fact_id))  # fmt: skip
    return _fact(conn.execute("SELECT * FROM memory_facts WHERE id = ?", (fact_id,)).fetchone())


def delete_fact(conn: sqlite3.Connection, fact_id: int) -> bool:
    """Gone from every search and list; the row stays marked (the journal is never rewritten, D14)."""
    return bool(conn.execute("UPDATE memory_facts SET deleted = 1, updated_at = ? WHERE id = ? AND deleted = 0",
                             (utc_now(), fact_id)).rowcount)  # fmt: skip


def recall(conn: sqlite3.Connection, run_id: str, query: str, *, cloud: bool) -> str:
    """What a model gets back from recall: its notes of this run that share a word with the query, and the kept
    facts the search finds."""
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3}
    stems = {w[: max(5, len(w) - 2)] if len(w) > 5 else w for w in words}
    mine = [n for n in notes(conn, run_id) if any(s in n["text"].lower() for s in stems)] or notes(conn, run_id)[-3:]
    found = facts(conn, query, cloud=cloud, limit=FOUND)
    parts = []
    if mine:
        parts.append("Your notes in this task:\n" + "\n".join(f"- {n['text']}" for n in mine[-FOUND:]))
    if found:
        parts.append("Kept facts:\n" + "\n".join(f"- {f['text']} ({f['about'] or 'fact'}, {f['created_at'][:10]})"
                                                  for f in found))  # fmt: skip
    return "\n\n".join(parts) or "nothing found in your notes or the kept facts"


# ---------------------------------------------------------------- skills


def add_skill(conn: sqlite3.Connection, run_id: str, name: str) -> dict[str, Any]:
    """A finished task's plan as a skill (the user agreed): its goal, its steps, the tools it used."""
    run = conn.execute("SELECT goal, report FROM agent_runs WHERE conversation_id = ? AND kind = 'autonomous'",
                       (run_id,)).fetchone()  # fmt: skip
    if run is None:
        raise MemoryError_("no such autonomous task")
    plan = conn.execute("SELECT MAX(version) FROM run_plans WHERE conversation_id = ?", (run_id,)).fetchone()[0]
    if not plan:
        raise MemoryError_("the task has no plan to keep")
    steps = [{"title": r["title"], "check": r["check_how"]} for r in conn.execute(
        "SELECT title, check_how FROM run_steps WHERE conversation_id = ? AND version = ? AND status != 'skipped'"
        " ORDER BY n", (run_id, plan)).fetchall()]  # fmt: skip
    used = sorted({b["name"] for m in conn.execute(
        "SELECT content FROM messages WHERE conversation_id = ? AND role = 'assistant'", (run_id,)).fetchall()
        for b in json.loads(m["content"]) if b.get("type") == "tool_call"})  # fmt: skip
    name = " ".join(name.split())[:120] or run["goal"][:120]
    cursor = conn.execute(
        "INSERT INTO skills (name, goal, steps, tools, source_run) VALUES (?, ?, ?, ?, ?)",
        (name, run["goal"], json.dumps(steps, ensure_ascii=False), json.dumps(used), run_id),
    )
    return skill(conn, int(cursor.lastrowid))


def skill(conn: sqlite3.Connection, skill_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if row is None:
        raise MemoryError_("no such skill")
    return {**dict(row), "steps": json.loads(row["steps"]), "tools": json.loads(row["tools"])}


def skills(conn: sqlite3.Connection, query: str = "", *, limit: int = 200) -> list[dict[str, Any]]:
    if query.strip():
        match = match_query(query)
        rows = conn.execute(
            "SELECT s.id FROM skills_fts JOIN skills s ON s.id = skills_fts.rowid WHERE skills_fts MATCH ?"
            " ORDER BY bm25(skills_fts) LIMIT ?", (match, limit)).fetchall() if match else []  # fmt: skip
    else:
        rows = conn.execute("SELECT id FROM skills ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [skill(conn, r["id"]) for r in rows]


def delete_skill(conn: sqlite3.Connection, skill_id: int) -> bool:
    return bool(conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,)).rowcount)


def skill_hint(conn: sqlite3.Connection, goal: str) -> str:
    """A skill for a goal like this one, as the agent's role reads it - or ''."""
    found = skills(conn, goal, limit=1)
    if not found:
        return ""
    best = found[0]
    conn.execute("UPDATE skills SET uses = uses + 1 WHERE id = ?", (best["id"],))
    steps = "\n".join(f"{n}. {s['title']}" + (f" (check: {s['check']})" if s["check"] else "")
                      for n, s in enumerate(best["steps"], 1))  # fmt: skip
    return (
        f"A similar goal was reached before («{best['name']}»), with this plan:\n{steps}\n"
        "Use it if it fits; change what does not."
    )
