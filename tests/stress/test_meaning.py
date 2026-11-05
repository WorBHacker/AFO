"""Search by meaning (D52, step 18.3), measured on AFO's own code: questions whose answer is known, asked the way a
model told by 18.1 would - search_code with the question's words as one regex, find_symbol with the same words. A
hit: the answer's file is in what the tool shows (search_code - its 50 lines; find_symbol - its first 10)."""

import asyncio
import time
from pathlib import Path

from hub import meaning
from hub.projects import Project
from hub.symbols import SymbolIndex
from hub.tools import ToolContext, run_tool

from .stand import RESULTS, note

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = [
    ("write a file atomically", ("hub/changes.py",)),
    ("classify a destructive command", ("hub/approvals.py",)),
    ("mask secrets in text", ("hub/secret_filter.py",)),
    ("apply sandbox changes onto the project", ("hub/sandbox.py",)),
    ("three way merge of a file", ("hub/sandbox.py",)),
    ("download a big file in parallel pieces", ("hub/hf.py",)),
    ("check for a newer release", ("hub/release.py",)),
    ("judge whether the goal is achieved", ("hub/judge.py", "hub/goals.py")),
    ("delegate tasks to agents in a batch", ("hub/agents.py", "hub/delegate.py")),
    ("rebuild a file from the chosen hunks", ("hub/changes.py",)),
    ("fit the conversation into the model context window", ("hub/fit.py",)),
    ("find programs installed on this computer", ("hub/programs.py",)),
    ("watch the window thread for stalls", ("app/ui/hitches.py",)),
    ("prompt cache key for a conversation", ("hub/gateway/openai_responses.py",)),
    ("tune model launch parameters by measurement", ("hub/tuning.py",)),
    ("refuse a command that reads the hub data", ("hub/approvals.py",)),
]
SHOWN_SYMBOLS = 10


def ask(name, args, ctx):
    return asyncio.run(run_tool(name, args, ctx))[0]


def test_search_by_meaning_on_afos_own_code():
    afo = Project(id="afo", name="afo", path=str(ROOT), description="", cloud_ok=True, created_at="", updated_at="")
    index = SymbolIndex(budget_s=120.0)
    began = time.perf_counter()
    state = index.refresh(afo)
    built = time.perf_counter() - began
    ctx = ToolContext([afo], symbols=index)
    by_text = by_meaning = 0
    table = []
    for question, answer in QUESTIONS:
        regex = "|".join(meaning.terms(question))
        lines = ask("search_code", {"project": "afo", "query": regex, "regex": True}, ctx).splitlines()[1:]
        text_hit = any(line.startswith(f"{a}:") for line in lines for a in answer)
        found = ask("find_symbol", {"project": "afo", "name": question}, ctx).splitlines()[1 : 1 + SHOWN_SYMBOLS]
        place = next((n for n, line in enumerate(found, 1) if any(line.startswith(f"{a}:") for a in answer)), 0)
        by_text += text_hit
        by_meaning += bool(place)
        table.append(f"{question}: search_code {'+' if text_hit else '-'}, find_symbol {place or '-'}")
    print("\n" + "\n".join(table))
    RESULTS.append(("meaning: files indexed", str(len(state.files)), ""))
    note("meaning: index of AFO's code built", built, "s", 30)
    note("meaning: search_code misses (of 16)", len(QUESTIONS) - by_text, "шт", 16)
    note("meaning: find_symbol misses (of 16)", len(QUESTIONS) - by_meaning, "шт", 4)
