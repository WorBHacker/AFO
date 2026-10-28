"""Tool definitions sized to the model (hub/compact.py, 2026-10-28): one per name, no project tools for a run
without projects, short descriptions for a small window - names, types, choices and what is required kept."""

from hub import autonomous, compact, computer, fit
from hub.agents import AGENT_TOOLS


def everything():
    return [*AGENT_TOOLS, *autonomous.TOOLS, *computer.TOOLS]


def test_one_definition_per_name_and_project_tools_only_with_projects():
    tools = everything()
    assert [t["name"] for t in tools].count("ask_user") == 2  # the chat's and the agent's
    kept = compact.for_model(tools, None)
    names = [t["name"] for t in kept]
    assert names.count("ask_user") == 1 and len(names) == len(set(names))
    assert kept[0] is tools[0]  # a big window: the definitions as they are
    alone = [t["name"] for t in compact.for_model(tools, None, projects=False)]
    assert "read_file" not in alone and "run_command" not in alone and "edit_file" not in alone
    assert {"web_fetch", "set_plan", "screen_look", "open_program"} <= set(alone)


def test_a_small_window_gets_short_definitions():
    tools = everything()
    small = compact.for_model(tools, 8192, projects=False)
    assert fit.tokens(small) < fit.tokens(compact.for_model(tools, None, projects=False)) * 0.8
    assert compact.for_model(tools, 32768) == compact.for_model(tools, None)  # big enough: as they are
    by = {t["name"]: t for t in small}
    full = {t["name"]: t for t in tools}
    for name, tool in by.items():  # what the model must get right stays
        assert tool["parameters"].get("required") == full[name]["parameters"].get("required")
        assert set(tool["parameters"].get("properties", {})) == set(full[name]["parameters"].get("properties", {}))
        assert len(tool["description"]) <= max(compact.DESCRIPTION_CHARS + 1, len(compact.SHORT.get(name, "")))
    act = by["screen_act"]["parameters"]["properties"]["action"]
    assert act["enum"] == full["screen_act"]["parameters"]["properties"]["action"]["enum"]
    assert "find" in by["screen_look"]["description"] and "never make one up" in by["open_program"]["description"]
    assert full["screen_look"]["description"] != by["screen_look"]["description"]  # the caller's stay whole


def test_a_short_sentence():
    assert compact.short("Read a file. Then more.", 60) == "Read a file."
    said = "The project's name, as listed in the system prompt."
    assert compact.short(said, 60) == said  # short enough: as it is
    assert compact.short("For done: how you verified it (a test run, the file read back)", 60) == (
        "For done: how you verified it."
    )
    assert compact.short("word " * 40, 30).endswith("…") and len(compact.short("word " * 40, 30)) <= 31
