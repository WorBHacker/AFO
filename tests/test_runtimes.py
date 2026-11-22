"""The engines' one entry (stage 45, hub/runtimes.py): each model of llama-swap's config is told to its engine by the
entry - llama.cpp, the pictures' sd-server, Strata, one AFO does not know - and the window changes what that engine
lets it. llama.cpp's own behaviour is the one it had before the entry (its tests in test_llama_config.py unchanged)."""

import pytest

from hub import llama_config as lc
from hub import model_install as mi
from hub import runtimes, tuning
from tests.test_llama_config import CONFIG

MORE = """
  "drawn":
    name: "Pictures"
    cmd: |
      ${sd_server} --diffusion-model "F:/models/z.gguf"
      --listen-port ${PORT}
    metadata:
      engine: stable-diffusion.cpp

  "odd":
    name: "Someone's own server"
    cmd: |
      C:/tools/vllm.exe --model F:/models/odd
      --port ${PORT}
"""
OTHERS = CONFIG + MORE


def test_each_model_is_told_to_its_engine_and_changes_what_it_lets():
    big, huge, fast = (lc.model_view(CONFIG, m) for m in ("big", "huge", "fast"))
    drawn, odd = (lc.model_view(OTHERS, m) for m in ("drawn", "odd"))
    told = [runtimes.LLAMA, runtimes.LLAMA, runtimes.STRATA_RUNTIME, runtimes.PICTURES, runtimes.engine_of([], [])]
    assert [runtimes.of(v) for v in (big, huge, fast, drawn, odd)] == told
    assert [v["engine"] for v in (drawn, odd)] == ["stable-diffusion.cpp", "other"]  # its program a macro: the line
    # llama.cpp with its command on lines of its own: every launch setting the window had before the entry
    assert runtimes.settings(big) == {"ttl", "context", "gpu_layers", "flash_attn", "cache_type", "parallel",
                                      *lc.COUNTS, *lc.MOE, *lc.SPEC_CHANGES}  # fmt: skip
    assert runtimes.edits(big) and runtimes.measured(big) and runtimes.estimated(big) and runtimes.drafted(big)
    # the others - the ttl, llama-swap's own; nothing measured, no header read, no drafts
    for view in (fast, drawn, odd):
        assert runtimes.settings(view) == {"ttl"}, view["id"]
        assert not (runtimes.edits(view) or runtimes.measured(view) or runtimes.estimated(view)
                    or runtimes.drafted(view) or runtimes.templated(view)), view["id"]  # fmt: skip
    assert lc.check_changes({"ttl": 60}, fast) == {"ttl": 60}
    with pytest.raises(lc.ConfigError, match="профиль пишет Strata"):
        lc.check_changes({"context": 8192}, fast)


def test_a_command_on_one_line_changes_only_its_ttl():
    text = CONFIG + '\n  "solo":\n    cmd: ${llama_server} -m "F:/models/solo.gguf" --port ${PORT}\n'
    solo = lc.model_view(text, "solo")
    assert runtimes.of(solo) is runtimes.LLAMA and not solo["editable"] and "одну строку" in solo["problem"]
    assert runtimes.settings(solo) == {"ttl"} and not runtimes.edits(solo) and not runtimes.measured(solo)
    assert runtimes.estimated(solo) and runtimes.templated(solo)  # its file and its flags are still read
    assert solo["file"] == "F:/models/solo.gguf"


def test_an_engine_put_into_the_entry_is_the_one_the_config_names(monkeypatch):
    """What the next engine needs (Strata, 45.1в): its place in the entry - model_view and check_changes ask it, not
    a name of their own."""
    mine = runtimes.Runtime("mine", program="mine-server", settings=("context",))
    monkeypatch.setattr(runtimes, "RUNTIMES", (*runtimes.RUNTIMES, mine))
    text = CONFIG + '\n  "own":\n    cmd: |\n      C:/mine/mine-server.exe -m F:/models/own.bin\n      --port ${PORT}\n'
    own = lc.model_view(text, "own")
    assert (own["engine"], own["editable"]) == ("mine", False) and runtimes.of(own) is mine
    assert lc.check_changes({"ttl": 30}, own) == {"ttl": 30}  # its flags are not llama.cpp's: the window keeps off
    with pytest.raises(lc.ConfigError):
        lc.check_changes({"context": 4096}, own)


def test_the_tuning_and_the_installer_ask_the_entry_too(monkeypatch):
    """45.1б: which models are measured, which the installer's checks go over (lost drafts), whose memory is told
    from the header - the engine's answers, not «llama.cpp» written in each place."""
    measured = runtimes.Runtime("measured", program="measured-server", flags=True, settings=runtimes.LLAMA_SETTINGS,
                                measured=True, header=True)  # fmt: skip
    kept = runtimes.Runtime("kept", program="kept-server", flags=True, settings=("context",))  # changed, not measured
    monkeypatch.setattr(runtimes, "RUNTIMES", (*runtimes.RUNTIMES, measured, kept))
    text = CONFIG + "".join(f'\n  "{name}":\n    cmd: |\n      C:/{name}/{name}-server.exe -m F:/models/{name}.gguf\n'
                            f"      --port ${{PORT}}\n" for name in ("measured", "kept"))  # fmt: skip
    assert tuning.tunable(text) == ["big", "huge", "measured"]  # Strata's «fast» and «kept» are not measured
    assert [v["id"] for v in mi._views(text)] == ["big", "huge", "measured", "kept"]
    views = {m: lc.model_view(text, m) for m in ("measured", "kept")}
    assert runtimes.estimated(views["measured"]) and not runtimes.estimated(views["kept"])
    assert not runtimes.drafted(views["measured"])  # it measures, but guesses ahead with no draft of AFO's
    assert mi._estimated(text, "kept", 16384) is None  # no header read: the window shows no estimate
